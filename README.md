# BMS_SOC_Lab — 电池 SOC 估计算法库

> **版本**：v1.0.0 ｜ **适用电芯**：三星 INR21700-30T（3Ah，NCA 高功率） ｜ **采样**：1 Hz（10 Hz 动态数据降采样后）

一个面向锂离子电池 **SOC（荷电状态）估计** 的算法研究与验证库。基于一阶 RC 等效电路模型，实现从安时积分到双扩展卡尔曼滤波（DEKF）的完整算法栈，并用三星 INR21700-30T 真实电芯数据完成标定与验证。

> ⚠️ 本项目是**算法研究原型**，不是量产车 BMS，也不是安全验证软件，禁止用于实时车辆安全决策。

---

## 1. 项目定位

BMS_SOC_Lab 提供一套**可复现、可审计、可扩展**的 SOC 估计框架，核心目标：

- 用统一的一阶 RC 电池模型，对比不同估计算法的精度与边界；
- 用真实电芯数据（三星 30T）而非合成数据验证算法；
- 明确各算法在**参数可辨识性、浓差极化、迟滞**上的物理局限。

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
- **一阶模型**：一个 RC 环，`V = OCV(SOC) − I·R0 − U1`，U1 为极化电压，由 `R1 ∥ C`（时间常数 τ = R1·C）描述。

> 术语说明：本文档的「一阶」= 一个 RC 环（R0 + R1/τ 三个参数）；「零阶」= 仅 R0。

## 3. 电池等效电路模型（一阶 RC）

连续时间状态空间：

```
dSOC/dt = −I / (3600 · Q)            （库仑计数，Q 为容量 Ah）
dU1/dt  = −U1 / τ + I · R1 / τ       （RC 极化电压，τ = R1·C）
V_term  = OCV(SOC) − I·R0 − U1       （观测方程，I 放电为正）
```

RC 环节用**零阶保持**离散化，令 `a = exp(−Δt/τ)`：

```
SOC[k] = SOC[k−1] − I[k−1]·Δt / (3600·Q)
U1[k]  = a·U1[k−1] + (1−a)·R1·I[k−1]
```

OCV-SOC 关系由 C/20 准平衡放电提取，线性插值查值，斜率用于 EKF 雅可比。

## 4. 单 EKF（`run_1rc_ekf`）

状态 `x = [SOC, U1]`，观测端电压 `V`。标准扩展卡尔曼滤波：预测步做库仑计数 + 极化传播 + 协方差预测，更新步用电压创新做卡尔曼校正（Joseph 协方差形式）。

关键协方差参数见 `config.json` 的 `soc_filter`（σ_V=7mV、P₀=12pp、Q=0.015pp/步等）。

**零阶与一阶切换**：`R1=0` 时退化到 `V = OCV − I·R0`，U1 单调衰减至 0。

**`update_mask` 跳更机制**：可选跳过测量更新。实验证明「仅放电更新」反使误差变大，故默认全程更新，开关保留备用。

## 5. 三维阻抗查表（`Impedance3DLookup`）

表格含 `temperature_c, current_a, soc_pct, r0_mohm, r1_mohm, tau_s`，按（温度 × 电流幅值 × SOC）做**三线性插值**（`RegularGridInterpolator`）。

- 电流取幅值、温度/电流轴外取最近值；
- 在 DEKF 中**仅作启动时刻初值**，之后由在线辨识跟踪。

## 6. 双 EKF（DEKF，`run_dual_ekf_1rc`）

两个滤波器交替运行：

```
参数 EKF（慢）：状态 θ = [R0, R1, τ]   初值 ← 三维表查表，在线辨识
状态 EKF（快）：状态 x = [SOC, U1]     用最新 θ 估计 SOC
```

**分时解耦门控（核心机制）**：R0/R1/τ 共享单电压观测，同时辨识会互相污染导致发散，故按电流状态门控——

| 参数 | 可辨识时刻 | 门控条件 |
|---|---|---|
| R0 | 电流跳变瞬间（瞬时压降 ΔV/ΔI） | `|dI| ≥ step_min_a` |
| R1, τ | 静置/极化恢复（无电流电压回升） | `|I| ≤ rest_current_a` |
| 全部冻结 | 稳态放电 | 其余 |

另有保护闸：残差超 `max_voltage_innovation_mv`（默认 150mV）时拒绝参数更新，防野点。

## 7. 验证结果（25℃，三星 30T）

零阶模型 `R0=6.6mΩ` 为最优固定参数，前 3 工况 ≤3%、后 2 ≤5%：

| 工况 | 目标 | RMSE |
|---|---|---:|
| 1C | 3% | 2.98 pp |
| UDDS | 3% | 2.06 pp |
| LA92 | 3% | 2.70 pp |
| HWFET | 5% | 4.86 pp |
| US06 | 5% | 4.84 pp |

DEKF（一阶 + 在线辨识）在温和工况更优（LA92 1.55、US06 4.52 pp），大电流 HWFET 受浓差极化边界制约。

## 8. 已知边界与局限（诚实声明）

1. **浓差极化未建模**：无扩散项（Warburg），大电流下误差放大。
2. **迟滞未建模**：充放电 OCV 双支路未建模，是剩余 ≤5% 误差的主要物理来源。
3. **参数可辨识性**：DEKF 的 R0 在电流跳变瞬间被浓差即时响应污染。
4. **无老化数据**：三星 30T 数据为单颗全新电芯多温度表征，SOH-Q/SOH-R 无从提取。
5. **参考 SOC 为代理值**：动态工况参考由满电 + 1C 容量 + 累计安时重构，非独立真值。

## 9. 项目结构

```
bmslab/engine.py        # 核心算法库（四类 SOC 估计算法）
bmslab/reports.py       # 报告生成
config.json             # 默认配置（参数滤波 / SOC 滤波协方差）
tests/                  # 单元测试（9 项通过）
docs/                   # 文档（算法说明 / 版本 / 变更日志 / 审计）
demo/                   # 合成数据自检样例
```

## 10. 文档

- [`docs/ALGORITHM_SPEC.md`](docs/ALGORITHM_SPEC.md) — **算法说明文档**（完整数学模型与关键逻辑）
- [`docs/VERSION.md`](docs/VERSION.md) — 版本说明
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — 变更日志
- [`docs/ALGORITHM_AUDIT.md`](docs/ALGORITHM_AUDIT.md) — 参数 EKF 可辨识性审计

## 11. 快速开始

```bash
# 安装依赖（需 Python 3.10+）
pip install -r requirements.txt

# 运行单元测试
python -m pytest -q
```

## 12. 参考文献

- Plett, G.L., "Extended Kalman filtering for battery management systems of LiPB-based HEV battery packs", *J. Power Sources*, 2004.（单 EKF 经典结构）
- Wan, E.A. & Nelson, A.T., "Dual extended Kalman filter methods", 2001.（DEKF 双滤波结构）
- Kollmeyer, P., et al., Samsung INR21700-30T 电池测试数据，McMaster University.（本项目数据源）
