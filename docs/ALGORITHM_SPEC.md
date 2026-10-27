# 算法说明文档（Algorithm Specification）

> 版本：v1.0.0 ｜ 适用电芯：三星 INR21700-30T（3Ah，NCA 高功率） ｜ 采样：1 Hz（10 Hz 动态数据降采样后）

---

## 1. 文档目的与范围

本文档描述 `BMS_SOC_Lab` v1.0.0 的完整算法体系，用于：

1. 让开发者理解每个估计算法的**数学模型、关键逻辑、参数含义**；
2. 明确各算法之间的**关系、适用场景与边界**；
3. 作为后续版本迭代（二阶模型、迟滞建模、SOH 估计）的技术基线。

本文档聚焦**算法逻辑**，数据前置处理（OCV 表、HPPC 辨识、三维表生成）在《数据来源说明》中另行描述。

---

## 2. 算法总览

v1.0.0 提供**四类 SOC 估计算法**，从简单到复杂递进：

| 算法 | 函数 | 模型阶数 | 参数来源 | 适用场景 |
|---|---|---|---|---|
| 安时积分（Coulomb Counting） | `run_ah` | —（无电压反馈） | 容量 | 基准/兜底 |
| 单 EKF | `run_1rc_ekf` | 零阶/一阶 | 固定或逐秒序列 | 已标定参数的在线估计 |
| 三维查表 EKF | `run_1rc_ekf_3d` | 一阶 | 三维表逐时刻查表 | 多温度/多倍率离线参数 |
| 双 EKF（DEKF） | `run_dual_ekf_1rc` | 一阶 | 三维表初值 + 在线辨识 | 参数未知或需在线跟踪 |

**模型阶数约定**（本项目统一定义）：

- **零阶模型**：纯欧姆内阻，`V = OCV(SOC) − I·R0`，无 RC 环。
- **一阶模型**：一个 RC 环，`V = OCV(SOC) − I·R0 − U1`，其中 U1 为极化电压，由 `R1 ∥ C`（时间常数 τ = R1·C）描述。

> 术语说明：本文档的「一阶」= 一个 RC 环（R0 + R1/τ 三个参数）；「零阶」= 仅 R0。

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

## 4. 单 EKF（`run_1rc_ekf`）

### 4.1 状态与观测

- 状态向量：`x = [SOC, U1]`（SOC 用 0~1 分数，U1 单位 mV）
- 观测：端电压 `V`（单位 V，内部换算 mV）

### 4.2 预测步（时间更新）

```
状态先验：
  zp = clip(SOC[k−1] − I[k−1]·Δt/(3600·Q), 0, 1)
  up = a·U1[k−1] + (1−a)·R1·I[k−1]

协方差先验：
  F = [[1, 0],
       [0, a]]
  P⁻ = F·P·Fᵀ + Q·max(Δt, 1)

观测预测：
  V̂ = OCV(zp) − I[k]·R0 − up
```

### 4.3 更新步（测量校正）

```
观测雅可比：H = [∂OCV/∂SOC , −1] = [slope, −1]

创新：innov = V[k]·1000 − V̂

创新协方差：S = H·P⁻·Hᵀ + R

卡尔曼增益：K = P⁻·Hᵀ / S

状态后验：x = [zp, up] + K·innov（SOC 裁剪到 [0,1]，U1 裁剪到 [−500,500]）

协方差后验（Joseph 形式）：
  A = I − K·H
  P = A·P⁻·Aᵀ + K·R·Kᵀ（再对称化）
```

### 4.4 关键协方差参数（`config.json` 的 `soc_filter`）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `sigma_measurement_mv` | 7.0 | 电压观测噪声 σ_V（mV） |
| `initial_sigma_soc_pct` | 12.0 | SOC 初始不确定性（pp） |
| `initial_sigma_polarization_mv` | 12.0 | 极化电压初始不确定性（mV） |
| `sigma_process_soc_pct_per_step` | 0.015 | SOC 过程噪声（pp/步） |
| `sigma_process_polarization_mv_per_step` | 0.4 | 极化过程噪声（mV/步） |

### 4.5 零阶与一阶的切换

- **一阶**：`R1 > 0`，`τ` 生效，U1 状态活跃。
- **零阶**：`R1 = 0`，则 `up = a·U1 + 0`，U1 随时间常数 τ 单调衰减至 0，等效于退化到 `V = OCV − I·R0`。

> 重要：即使 R1=0，`a = exp(−Δt/τ)` 仍会进入协方差传播（`F[1,1]=a`），因此 τ 在零阶模式下会**间接调节卡尔曼增益**，属于过程噪声调谐，需注意过拟合风险。

### 4.6 `update_mask` 跳更机制

`update_mask`（布尔数组）标记每个时刻是否做测量更新：

- `True`：正常卡尔曼校正。
- `False`：跳过更新，`SOC = zp`（纯安时积分推进），协方差仅预测。

> 实验结论（v1.0.0 验证）：对三星 30T 动态工况，「仅放电更新 / 跳过充电静置」反而使误差全面变大，因为静置段端电压≈OCV 是最可靠观测。故**默认全程更新**，此开关保留备用。

---

## 5. 三维阻抗查表（`Impedance3DLookup`）

### 5.1 数据结构

表格需含列：`temperature_c, current_a, soc_pct, r0_mohm, r1_mohm, tau_s`。

三个轴：
- 温度轴（-20/-10/0/10/25/40℃，当前实测表仅 0/25/40℃）
- 电流幅值轴（|I|，覆盖 HPPC 各倍率）
- SOC 轴（0~100%）

### 5.2 插值逻辑

1. 构建规则网格值 `[temp, current, soc]`，用 `RegularGridInterpolator` 做**三线性插值**。
2. 缺失值先沿 SOC 维、再沿电流维 `np.interp` 填充。
3. 查询时：
   - 电流取**幅值**（`abs(I)`，阻抗对方向近似对称）；
   - 温度、电流轴外取**最近值**（clip 到轴范围）；
   - SOC 裁剪到 [0, 100]。

### 5.3 关键语义：仅作「启动时刻初值」

在 DEKF 中，三维表的**唯一职责**是为参数 EKF 提供启动时刻的 R0/R1/τ 初值。之后参数由在线辨识跟踪，不再回到三维表。这与「逐时刻查表」（`run_1rc_ekf_3d`）形成两种不同用法。

---

## 6. 三维查表 EKF（`run_1rc_ekf_3d`）

与单 EKF 同构，唯一区别：R0/R1/tau 不在配置中固定，而是**逐时刻**从三维表按（温度、电流幅值、当前 SOC 估计）查表。

- 预测步用**上一时刻 SOC** 查表（保证因果性，不引入未来信息）。
- 适合参数已离线标定好的多温度/多倍率场景。

---

## 7. 双 EKF（DEKF，`run_dual_ekf_1rc`）

### 7.1 结构

两个滤波器交替运行：

```
参数 EKF（慢）：状态 θ = [R0, R1, τ]
  初值 ← 三维表查表（启动时刻）
  用电压残差在线辨识参数

状态 EKF（快）：状态 x = [SOC, U1]
  用参数 EKF 传来的最新 θ 估计 SOC
```

### 7.2 参数 EKF 观测与雅可比

观测方程同前：`V = OCV(SOC) − I·R0 − U1`。

参数雅可比 `Hθ`（对 [R0, R1, τ]）：

```
∂V/∂R0 = −I[k]
∂V/∂R1 = −(1−a)·I[k−1]
∂V/∂τ = −(Δt/τ²)·a·(U1[k−1] − R1·I[k−1])
```

参数用**随机游走**模型：`θ[k] = θ[k−1] + w`，过程噪声 `Qθ = diag(q_r0, q_r1, q_tau)·Δt`。

### 7.3 分时解耦门控（核心）

**问题**：R0/R1/τ 共享单一电压观测，同时辨识时互相"抢残差"，导致参数漂移发散（实测不门控时 1C 误差飙至 20pp）。

**方案**：不同参数的可辨识信息出现在不同时刻，按电流状态门控：

| 参数 | 可辨识时刻 | 门控条件 |
|---|---|---|
| R0 | 电流跳变瞬间（瞬时压降 ΔV/ΔI） | `|dI| ≥ step_min_a` |
| R1, τ | 静置/极化恢复（无电流电压回升） | `|I| ≤ rest_current_a` |
| 全部冻结 | 稳态放电 | 其余 |

**门控实现**：

```
dI = I[k] − I[k−1]

gate_r0    = (|dI| ≥ step_thr)      # R0 可辨识
gate_r1tau = (|I[k]| ≤ rest_thr)    # R1/τ 可辨识

allowed = [gate_r0, gate_r1tau, gate_r1tau]   # 对应 [R0, R1, τ]
Kt[~allowed] = 0.0                            # 冻结不可辨识参数的增益
```

**保护闸**：`|innov| > max_voltage_innovation_mv`（默认 150mV）时**拒绝参数更新**（判断为异常/模型失配，防野点带偏）。

### 7.4 门控参数（`parameter_filter`）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `step_min_a` | 5.0 | R0 更新的电流跳变阈值（A） |
| `step_c_rate` | 0.1 | 跳变阈值的 C-rate 换算（×容量） |
| `rest_current_a` | 2.0 | R1/τ 更新的静置电流阈值（A） |
| `rest_c_rate` | 0.01 | 静置阈值的 C-rate 换算 |
| `max_voltage_innovation_mv` | 150.0 | 拒绝更新的残差上限（mV） |

> 实际阈值取 `max(绝对值阈值, C-rate×容量)` 的较大者，兼顾不同容量电芯。

### 7.5 状态 EKF

与单 EKF 完全一致（见第 4 节），仅 R0/R1/τ 来自参数 EKF 的当前估计值。

### 7.6 状态 EKF 与参数 EKF 的协方差（`parameter_filter`）

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

## 9. 关键算法逻辑摘要（伪代码）

### 9.1 单 EKF 主循环

```
初始化：z=start/100, U1=0, P=diag(σ_SOC², σ_U1²)
for k in 1..n:
    dt = t[k]−t[k−1]
    a  = exp(−dt/max(τ,1))
    # 预测
    zp = clip(z − I[k−1]·dt/(3600Q), 0, 1)
    up = a·U1 + (1−a)·R1·I[k−1]
    P⁻ = F·P·Fᵀ + Q·max(dt,1)
    V̂  = OCV(zp) − I[k]·R0 − up
    # 更新（若 V 有效且 mask[k]）
    innov = V[k]·1000 − V̂
    H = [slope, −1]; S = H·P⁻·Hᵀ + R; K = P⁻·Hᵀ/S
    x = [zp, up] + K·innov（裁剪）
    P = (I−K·H)·P⁻·(I−K·H)ᵀ + K·R·Kᵀ
```

### 9.2 DEKF 主循环（含门控）

```
初始化：θ = 三维表.query(T0, I0, SOC0)；z, U1, Px, Pθ
for k in 1..n:
    dt = t[k]−t[k−1]; a = exp(−dt/τ)
    dI = I[k]−I[k−1]
    # 状态预测
    zp, up = ...（同单 EKF）
    # 参数预测（随机游走）
    Pθ⁻ = Pθ + diag(q)·dt
    # 观测预测 + 雅可比
    V̂ = OCV(zp) − I[k]·R0 − up
    Hx = [slope, −1]; Hθ = [−I[k], −(1−a)I[k−1], −(Δt/τ²)a(U1−R1·I[k−1])]
    # 状态更新（始终）
    Kx = ...; z, U1, Px = ...
    # 参数更新（门控解耦）
    gate_r0 = |dI| ≥ step_thr
    gate_r1τ = |I[k]| ≤ rest_thr
    allowed = [gate_r0, gate_r1τ, gate_r1τ]
    if allowed.any() and |innov| ≤ max_innov:
        Kt = Pθ⁻·Hθᵀ/(Hθ·Pθ⁻·Hθᵀ + Rθ); Kt[~allowed] = 0
        θ = clip(θ + Kt·innov, lo, hi)
```

---

## 10. 已知边界与局限（诚实声明）

1. **浓差极化未建模**：零阶/一阶 RC 均无扩散项（Warburg），大电流下 SOC 误差放大（HWFET/US06 曾达 10~14pp，优化后压至 ~5pp）。
2. **迟滞未建模**：动态工况含 7%~21% 充电回馈，充放电 OCV 双支路未建模，是剩余 ≤5% 误差的主要物理来源。
3. **参数可辨识性**：DEKF 的 R0 在电流跳变瞬间被浓差即时响应污染，无法与欧姆内阻彻底解耦。
4. **无老化数据**：三星 30T 数据为单颗全新电芯多温度表征，SOH-Q/SOH-R 无从提取。
5. **参考 SOC 为代理值**：动态工况参考 SOC 由满电 + 1C 容量 + 累计安时重构，非独立真值。

---

## 11. 参考文献

- Plett, G.L., "Extended Kalman filtering for battery management systems of LiPB-based HEV battery packs", *J. Power Sources*, 2004.（单 EKF 经典结构）
- Wan, E.A. & Nelson, A.T., "Dual extended Kalman filter methods", 2001.（DEKF 双滤波结构）
- Kollmeyer, P., et al., Samsung INR21700-30T 电池测试数据，McMaster University.（本项目数据源）
