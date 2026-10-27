# 算法说明文档（Algorithm Specification）

> 版本：v1.0.0 ｜ 适用电芯：三星 INR21700-30T（3Ah，NCA 高功率） ｜ 采样：1 Hz（10 Hz 动态数据降采样后）

---

## 1. 文档目的与范围

本文档描述 `BMS_SOC_Lab` v1.0.0 采用的 **SOC 估计算法：双扩展卡尔曼滤波（DEKF）**，包括：

1. 让开发者理解 DEKF 的**数学模型、关键逻辑、参数含义**；
2. 重点阐述**分时解耦门控**这一核心机制的设计动机与实现；
3. 作为后续版本迭代（二阶模型、迟滞建模、SOH 估计）的技术基线。

本文档聚焦**算法逻辑**，数据前置处理（OCV 表、HPPC 辨识、三维表生成）在《数据来源说明》中另行描述。

---

## 2. 算法定位

v1.0.0 的 SOC 估计**唯一采用双扩展卡尔曼滤波（Dual EKF，DEKF）**，基于**一阶 RC 等效电路模型**。

- **一阶模型**：一个 RC 环，`V = OCV(SOC) − I·R0 − U1`，其中 U1 为极化电压，由 `R1 ∥ C`（时间常数 τ = R1·C）描述，共 R0、R1、τ 三个参数。
- **双 EKF**：两个滤波器交替运行——
  - **参数 EKF（慢）**：在线辨识阻抗参数 θ = [R0, R1, τ]；
  - **状态 EKF（快）**：估计状态 x = [SOC, U1]。

> 术语说明：本文档的「一阶」= 一个 RC 环（R0 + R1/τ 三个参数）。

---

## 3. 电池等效电路模型（一阶 RC）

### 3.1 数学模型

连续时间状态空间：

```
dSOC/dt = −I / (3600 · Q)            （库仑计数，Q 为容量 Ah）

dU1/dt = −U1 / τ + I · R1 / τ       （RC 极化电压，τ = R1·C）

V_term = OCV(SOC) − I·R0 − U1       （观测方程，I 放电为正）
```

### 3.2 离散化（采样周期 Δt）

RC 环节用**零阶保持**离散化，令 `a = exp(−Δt/τ)`：

```
SOC[k] = SOC[k−1] − I[k−1]·Δt / (3600·Q)

U1[k]  = a·U1[k−1] + (1−a)·R1·I[k−1]
```

### 3.3 OCV-SOC 关系

- 由 C/20 准平衡放电提取（`ocv_soc_25degC.csv`，101 点，0~100%）。
- 查值用 `np.interp` 线性插值；斜率 `slope = ΔOCV/ΔSOC`（mV / SOC 分数）用于 EKF 雅可比。

---

## 4. 三维阻抗查表（`Impedance3DLookup`）

### 4.1 数据结构

表格需含列：`temperature_c, current_a, soc_pct, r0_mohm, r1_mohm, tau_s`。

三个轴：
- 温度轴（-20/-10/0/10/25/40℃，当前实测表仅 0/25/40℃）
- 电流幅值轴（|I|，覆盖 HPPC 各倍率）
- SOC 轴（0~100%）

### 4.2 插值逻辑

1. 构建规则网格值 `[temp, current, soc]`，用 `RegularGridInterpolator` 做**三线性插值**。
2. 缺失值先沿 SOC 维、再沿电流维 `np.interp` 填充。
3. 查询时：
   - 电流取**幅值**（`abs(I)`，阻抗对方向近似对称）；
   - 温度、电流轴外取**最近值**（clip 到轴范围）；
   - SOC 裁剪到 [0, 100]。

### 4.3 关键语义：仅作「启动时刻初值」

三维表的**唯一职责**是为参数 EKF 提供启动时刻的 R0/R1/τ 初值。之后参数由在线辨识跟踪，不再回到三维表。

---

## 5. 双 EKF（DEKF，`run_dual_ekf_1rc`）

### 5.1 结构

两个滤波器交替运行：

```
参数 EKF（慢）：状态 θ = [R0, R1, τ]
  初值 ← 三维表查表（启动时刻）
  用电压残差在线辨识参数

状态 EKF（快）：状态 x = [SOC, U1]
  用参数 EKF 传来的最新 θ 估计 SOC
```

### 5.2 观测方程与雅可比

观测方程：`V = OCV(SOC) − I·R0 − U1`。

**状态雅可比 `Hx`**（对 [SOC, U1]）：

```
Hx = [∂OCV/∂SOC, −1] = [slope, −1]
```

**参数雅可比 `Hθ`**（对 [R0, R1, τ]）：

```
∂V/∂R0 = −I[k]
∂V/∂R1 = −(1−a)·I[k−1]
∂V/∂τ = −(Δt/τ²)·a·(U1[k−1] − R1·I[k−1])
```

参数用**随机游走**模型：`θ[k] = θ[k−1] + w`，过程噪声 `Qθ = diag(q_r0, q_r1, q_tau)·Δt`。

---

## 6. 分时解耦门控（核心机制）

> 本章是 DEKF 的关键设计，解决参数 EKF 的**可辨识性**问题。

### 6.1 问题：为什么需要门控？

参数 EKF 要在线辨识 [R0, R1, τ] 三个参数，但它们**共享同一个电压观测**（单一方程 `V = OCV − I·R0 − U1`）。若三个参数每次都同时追同一个电压残差，就会互相"抢残差"，谁也辨识不准，最终一起漂移发散。

**实测证据**：不门控时，R0 从 7mΩ 一路跌到 0.1mΩ（下限），R1 涨到 40mΩ（上限），τ 从 8s 涨到 288s，SOC 误差飙至 20pp——三个参数互相顶替，完全失真。

### 6.2 核心思想：不同参数的信息出现在不同时刻

每个参数都有各自的"可辨识时刻"，只有在其信息最充分的时刻更新，才能避免互相污染：

| 参数 | 物理含义 | 可辨识时刻 | 信息来源 |
|---|---|---|---|
| R0 | 欧姆内阻 | **电流跳变瞬间** | 瞬时压降 ΔV/ΔI |
| R1 | 极化内阻 | **静置/极化恢复段** | 无电流时电压回升 |
| τ | 时间常数 | **静置/极化恢复段** | 极化的消退速度 |

因此门控按**两个电流信号**切换：
- 电流导数 `dI = I[k] − I[k−1]`：判断是否"跳变"；
- 电流幅值 `|I|`：判断是否"静置"。

### 6.3 门控规则（三档）

```
每一时刻 k，先算：
  dI = I[k] − I[k−1]          # 电流变化量
  |I|                          # 当前电流幅值

情况 A：|dI| ≥ step_min_a        → 判定为「电流跳变」
        → 只更新 R0（瞬时压降才是 R0 的信息）
        → R1、τ 冻结

情况 B：|I| ≤ rest_current_a     → 判定为「静置/极化恢复」
        → 只更新 R1、τ（无电流时电压变化才是极化信息）
        → R0 冻结

情况 C：其余（稳态放电、普通电流）
        → 三个参数全部冻结
        → 只有状态 EKF 更新 SOC
```

### 6.4 门控实现

```
dI = I[k] − I[k−1]

gate_r0    = (|dI| ≥ step_thr)      # R0 可辨识
gate_r1tau = (|I[k]| ≤ rest_thr)    # R1/τ 可辨识

allowed = [gate_r0, gate_r1tau, gate_r1tau]   # 对应 [R0, R1, τ]
Kt[~allowed] = 0.0                            # 冻结不可辨识参数的增益
```

**保护闸**：`|innov| > max_voltage_innovation_mv`（默认 150mV）时**拒绝参数更新**（判断为异常/模型失配，防野点带偏）。

### 6.5 门控参数（`parameter_filter`）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `step_min_a` | 5.0 | R0 更新的电流跳变阈值（A） |
| `step_c_rate` | 0.1 | 跳变阈值的 C-rate 换算（×容量） |
| `rest_current_a` | 2.0 | R1/τ 更新的静置电流阈值（A） |
| `rest_c_rate` | 0.01 | 静置阈值的 C-rate 换算 |
| `max_voltage_innovation_mv` | 150.0 | 拒绝更新的残差上限（mV） |

> 实际阈值取 `max(绝对值阈值, C-rate×容量)` 的较大者，兼顾不同容量电芯。

### 6.6 门控的实际效果

| | 不门控 | 门控后 |
|---|---|---|
| 1C 工况 SOC 误差 | 20pp（发散） | 2.47pp ✓ |
| R0 轨迹 | 7→0.1mΩ 漂到底 | 稳定在初值附近 |
| 参数状态 | 三参数乱飘打架 | 各自只在正确时刻微调 |

---

## 7. 状态 EKF 与参数 EKF 的协方差

### 7.1 状态 EKF（`soc_filter`）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `sigma_measurement_mv` | 7.0 | 电压观测噪声 σ_V（mV） |
| `initial_sigma_soc_pct` | 12.0 | SOC 初始不确定性（pp） |
| `initial_sigma_polarization_mv` | 12.0 | 极化电压初始不确定性（mV） |
| `sigma_process_soc_pct_per_step` | 0.015 | SOC 过程噪声（pp/步） |
| `sigma_process_polarization_mv_per_step` | 0.4 | 极化过程噪声（mV/步） |

### 7.2 参数 EKF（`parameter_filter`）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `sigma_measurement_mv` | 7.0 | 参数观测噪声（mV） |
| `initial_sigma.r0_mohm` | 3.0 | R0 初始不确定性（mΩ） |
| `initial_sigma.r1_mohm` | 3.0 | R1 初始不确定性（mΩ） |
| `initial_sigma.tau_s` | 20.0 | τ 初始不确定性（s） |
| `sigma_change_over_100s.r0_mohm` | 0.05 | R0 随机游走速率（mΩ/√100s） |
| `sigma_change_over_100s.r1_mohm` | 0.05 | R1 随机游走速率 |
| `sigma_change_over_100s.tau_s` | 2.0 | τ 随机游走速率（s/√100s） |

**参数物理约束**（clip）：R0 ∈ [0.5, r0_max]，R1 ∈ [0, r1_max]，τ ∈ [2, 300]。

---

## 8. 误差评估（`error_metrics`）

对 SOC 估计与参考值逐点计算：

| 指标 | 定义 |
|---|---|
| RMSE | `√(mean(e²))` |
| MAE | `mean(|e|)` |
| Max Abs Error | `max(|e|)` |
| Bias | `mean(e)`（符号表征系统性高估/低估） |

其中 `e = estimate − reference`，单位 pp（百分点）。

---

## 9. DEKF 主循环（伪代码，含门控）

```
初始化：θ = 三维表.query(T0, I0, SOC0)；z, U1, Px, Pθ
for k in 1..n:
    dt = t[k]−t[k−1]; a = exp(−dt/τ)
    dI = I[k]−I[k−1]

    # 1. 状态 EKF 预测
    zp = clip(z − I[k−1]·dt/(3600·Q), 0, 1)
    up = a·U1 + (1−a)·R1·I[k−1]
    Px⁻ = F·Px·Fᵀ + Qx·dt

    # 2. 参数 EKF 预测（随机游走）
    Pθ⁻ = Pθ + diag(q_r0, q_r1, q_tau)·dt

    # 3. 观测预测 + 雅可比
    V̂ = OCV(zp) − I[k]·R0 − up
    Hx = [slope, −1]
    Hθ = [−I[k], −(1−a)·I[k−1], −(Δt/τ²)·a·(U1−R1·I[k−1])]

    # 4. 状态 EKF 更新（始终进行）
    innov = V[k]·1000 − V̂
    Kx = Px⁻·Hxᵀ / (Hx·Px⁻·Hxᵀ + R)
    x = [zp, up] + Kx·innov（裁剪）
    Px = (I − Kx·Hx)·Px⁻（Joseph 形式）

    # 5. 参数 EKF 更新（分时解耦门控）
    gate_r0    = (|dI| ≥ step_thr)
    gate_r1tau = (|I[k]| ≤ rest_thr)
    allowed = [gate_r0, gate_r1tau, gate_r1tau]
    if allowed.any() and |innov| ≤ max_innov:
        Kt = Pθ⁻·Hθᵀ / (Hθ·Pθ⁻·Hθᵀ + Rθ)
        Kt[~allowed] = 0.0                 # 冻结不可辨识参数
        θ = clip(θ + Kt·innov, lo, hi)
        Pθ = (I − Kt·Hθ)·Pθ⁻
```

---

## 10. 已知边界与局限（诚实声明）

1. **浓差极化未建模**：一阶 RC 无扩散项（Warburg），大电流下 SOC 误差放大（HWFET/US06 曾达 10~14pp，优化后压至 ~5pp）。
2. **迟滞未建模**：动态工况含 7%~21% 充电回馈，充放电 OCV 双支路未建模，是剩余 ≤5% 误差的主要物理来源。
3. **参数可辨识性**：即使门控解耦，R0 在电流跳变瞬间仍被浓差即时响应污染，无法与欧姆内阻彻底解耦。
4. **无老化数据**：三星 30T 数据为单颗全新电芯多温度表征，SOH-Q/SOH-R 无从提取。
5. **参考 SOC 为代理值**：动态工况参考 SOC 由满电 + 1C 容量 + 累计安时重构，非独立真值。

---

## 11. 参考文献

- Plett, G.L., "Extended Kalman filtering for battery management systems of LiPB-based HEV battery packs", *J. Power Sources*, 2004.
- Wan, E.A. & Nelson, A.T., "Dual extended Kalman filter methods", 2001.（DEKF 双滤波结构）
- Kollmeyer, P., et al., Samsung INR21700-30T 电池测试数据，McMaster University.（本项目数据源）
