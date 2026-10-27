# BMS_SOC_Lab — 电池 SOC 估计算法库

> **版本**：v1.0.0 ｜ **适用电芯**：三星 INR21700-30T（3Ah，NCA 高功率） ｜ **采样**：1 Hz（10 Hz 动态数据降采样后）

一个面向锂离子电池 **SOC（荷电状态）估计** 的算法研究与验证库。基于一阶 RC 等效电路模型，采用**双扩展卡尔曼滤波（DEKF）**，并引入**分时解耦门控**机制解决参数可辨识性问题，用三星 INR21700-30T 真实电芯数据完成标定与验证。

> ⚠️ 本项目是**算法研究原型**，不是量产车 BMS，也不是安全验证软件，禁止用于实时车辆安全决策。

---

## 1. 项目定位

BMS_SOC_Lab 提供一套**可复现、可审计、可扩展**的 SOC 估计框架，核心目标：

- 用一阶 RC 电池模型实现**双 EKF** 在线估计 SOC 与阻抗参数；
- 用**分时解耦门控**解决参数 EKF 的可辨识性难题；
- 用真实电芯数据（三星 30T）而非合成数据验证算法，并明确物理边界。

## 2. 算法：双扩展卡尔曼滤波（DEKF）

v1.0.0 的 SOC 估计**唯一采用双扩展卡尔曼滤波**，基于**一阶 RC 模型**（一个 RC 环，参数 R0、R1、τ）：

```
V_term = OCV(SOC) − I·R0 − U1
dU1/dt = −U1/τ + I·R1/τ       （RC 极化电压，τ = R1·C）
```

两个滤波器交替运行：

```
参数 EKF（慢）：状态 θ = [R0, R1, τ]   初值 ← 三维表查表，在线辨识阻抗参数
状态 EKF（快）：状态 x = [SOC, U1]     用最新 θ 估计 SOC
```

---

## 3. 分时解耦门控（核心机制）

参数 EKF 要在线辨识 [R0, R1, τ]，但三者**共享单一电压观测**，同时辨识会互相"抢残差"，导致参数漂移发散。

**核心思想**：不同参数的可辨识信息出现在不同时刻，只在各自信息最充分的时刻才更新：

| 参数 | 物理含义 | 可辨识时刻 | 门控条件 |
|---|---|---|---|
| R0 | 欧姆内阻 | 电流跳变瞬间（瞬时压降 ΔV/ΔI） | `|dI| ≥ step_min_a` |
| R1, τ | 极化内阻/时间常数 | 静置/极化恢复（无电流电压回升） | `|I| ≤ rest_current_a` |
| 全部冻结 | 稳态放电 | — | 其余 |

门控逻辑（按电流状态三档切换）：

```
dI = I[k] − I[k−1]

情况 A：|dI| ≥ step_min_a   → 只更新 R0（R1/τ 冻结）
情况 B：|I|  ≤ rest_current_a → 只更新 R1、τ（R0 冻结）
情况 C：其余               → 参数全部冻结，仅状态 EKF 更新 SOC
```

另有**保护闸**：残差超 `max_voltage_innovation_mv`（默认 150mV）时拒绝参数更新，防野点带偏。

**门控效果**：不门控时 1C 工况 SOC 误差 20pp（R0 从 7mΩ 漂到 0.1mΩ 发散），门控后稳定至 2.47pp，参数只在正确时刻微调。

---

## 4. 三维阻抗查表（`Impedance3DLookup`）

表格含 `temperature_c, current_a, soc_pct, r0_mohm, r1_mohm, tau_s`，按（温度 × 电流幅值 × SOC）做**三线性插值**（`RegularGridInterpolator`）。

- 电流取幅值、温度/电流轴外取最近值；
- **仅作参数 EKF 的启动时刻初值**，之后由在线辨识跟踪。

---

## 5. 验证结果（25℃，三星 30T）

DEKF（一阶 + 在线辨识 + 分时门控）在温和工况优于固定参数零阶模型：

| 工况 | 目标 | DEKF RMSE |
|---|---|---:|
| 1C | 3% | 2.47 pp |
| UDDS | 3% | 2.88 pp |
| LA92 | 3% | 1.55 pp |
| US06 | 5% | 4.52 pp |
| HWFET | 5% | 5.81 pp |

> 大电流 HWFET 仍受浓差极化边界制约（详见「已知边界」）。

---

## 6. 已知边界与局限（诚实声明）

1. **浓差极化未建模**：一阶 RC 无扩散项（Warburg），大电流下误差放大。
2. **迟滞未建模**：充放电 OCV 双支路未建模，是剩余 ≤5% 误差的主要物理来源。
3. **参数可辨识性**：即使门控解耦，R0 在电流跳变瞬间仍被浓差即时响应污染。
4. **无老化数据**：三星 30T 数据为单颗全新电芯多温度表征，SOH-Q/SOH-R 无从提取。
5. **参考 SOC 为代理值**：动态工况参考由满电 + 1C 容量 + 累计安时重构，非独立真值。

---

## 7. 项目结构

```
bmslab/engine.py        # 核心算法库（DEKF + 三维查表）
bmslab/reports.py       # 报告生成
config.json             # 默认配置（参数滤波 / SOC 滤波协方差 / 门控阈值）
tests/                  # 单元测试（9 项通过）
docs/                   # 文档（算法说明 / 版本 / 变更日志 / 审计）
demo/                   # 合成数据自检样例
```

## 8. 文档

- [`docs/ALGORITHM_SPEC.md`](docs/ALGORITHM_SPEC.md) — **算法说明文档**（DEKF 数学模型 + 分时门控关键逻辑）
- [`docs/VERSION.md`](docs/VERSION.md) — 版本说明
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — 变更日志
- [`docs/ALGORITHM_AUDIT.md`](docs/ALGORITHM_AUDIT.md) — 参数 EKF 可辨识性审计

## 9. 快速开始

```bash
# 安装依赖（需 Python 3.10+）
pip install -r requirements.txt

# 运行单元测试
python -m pytest -q
```

## 10. 参考文献

- Wan, E.A. & Nelson, A.T., "Dual extended Kalman filter methods", 2001.（DEKF 双滤波结构）
- Plett, G.L., "Extended Kalman filtering for battery management systems of LiPB-based HEV battery packs", *J. Power Sources*, 2004.
- Kollmeyer, P., et al., Samsung INR21700-30T 电池测试数据，McMaster University.（本项目数据源）
