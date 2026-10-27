# CHANGELOG

本文件记录 `BMS_SOC_Lab` 的每一项改动、原因与已知限制。

## v1.0.0 — 2026-10-27 — 真实电芯数据 + 算法落地与优化

首个基于三星 INR21700-30T 真实数据完成算法落地、验证、优化的稳定版本。

### 新增算法（`bmslab/engine.py`）
- `Impedance3DLookup`：三维阻抗查表（温度 × 电流幅值 × SOC → R0/R1/tau），基于 `scipy.RegularGridInterpolator` 三线性插值，轴外取最近值。
- `run_1rc_ekf_3d`：一阶 RC EKF，R0/R1/tau 逐时刻从三维表按（温度、电流幅值、当前 SOC 估计）查表插值。
- `run_dual_ekf_1rc`：双扩展卡尔曼滤波（DEKF），参数 EKF 在线辨识 [R0, R1, tau]，状态 EKF 估计 [SOC, U1]。三维表只作为启动时刻初值。
- `run_1rc_ekf` 新增 `update_mask` 参数：可选跳过测量更新（默认全程更新）。

### 核心机制：分时解耦门控（DEKF 可辨识性）
参数 EKF 的 [R0, R1, tau] 共享单电压观测，同时辨识必然互相污染。故引入门控：
- R0 只在「电流跳变」（|dI| ≥ step_min_a）时更新；
- R1/tau 只在「静置/极化恢复」（|I| ≤ rest_current_a）时更新；
- 稳态放电段参数冻结，仅状态 EKF 更新 SOC；
- 残差超 `max_voltage_innovation_mv` 拒绝更新（防野点）。

### 数据前置（`_samsung30t/`）
- 从 C20 放电提取 OCV-SOC 表；从 HPPC 辨识 R0/R1/tau；构建三维阻抗表 `impedance_3d_table.csv`（0/25/40℃）。
- 容量-温度表 `capacity_by_temp.csv`；25℃ 1C 标定容量 2.9449 Ah。

### 25℃ 验证结论（详见 `_samsung30t/optimized_25degC/REPORT.md`）
- 零阶 R0=6.6mΩ、R1=0 为最优固定参数：1C 2.98 / UDDS 2.06 / LA92 2.70 / HWFET 4.86 / US06 4.84 pp，前 3 ≤3%、后 2 ≤5%。
- DEKF（一阶 + 在线辨识）：温和工况优于零阶（LA92 1.55、US06 4.52），大电流 HWFET 5.81 受浓差极化边界制约。
- 「仅放电更新」实验证伪：跳过充电/静置更新反而使误差全面变大，最终保留全程更新。

### 已知限制
1. 三星 30T 数据**无老化数据**（单颗全新电芯的多温度特性表征），SOH-Q/SOH-R 无从提取。
2. DEKF 参数可辨识性仍受大电流浓差极化污染，R0 在跳变瞬间被浓差即时响应低估。
3. 动态工况参考 SOC 为安时积分代理值，非独立真值。
4. 迟滞（充放电 OCV 双支路）未建模，是剩余 ≤5% 误差的物理来源。

---

## 2026-10-26 — Codex 首轮交接（复现 + 审计 + 测试，未改算法）

### 新增
- `tests/test_algorithm_audit.py`：新增 6 项最小回归测试，固化已知失效模式。
  - 恒流下 OCV/R0 不可区分（`test_constant_current_cannot_distinguish_ocv_and_r0`）
  - 无静置时 OCV 不更新（`test_no_rest_means_ocv_never_updates`）
  - 1Hz 阶跃 ≠ 瞬时 DCIR（`test_step_1hz_is_not_instantaneous_dcir`）
  - 缺参考 SOC 不输出 RMSE（`test_missing_reference_means_no_rmse`）
  - 参数贴边应告警（`test_param_at_boundary_should_warn`，标记 `expectedFailure`）
  - `accepted` 标志应反映真实更新（`test_accepted_flag_reflects_actual_update`，标记 `expectedFailure`）
- `docs/ALGORITHM_AUDIT.md`：参数 EKF 的数学/单位/协方差/可辨识性问题清单与失效原因分析。
- `outputs/codex_baseline/`：合成数据基线复现结果。

### 改动
- 无对 `bmslab/`、`config.json` 或 `app.py`/`cli.py` 的行为修改。保留原算法以便 A/B 对照。

### 结果
- 现有 5 项测试全部通过（`python -m unittest discover -s tests -v`）。
- 合成数据基线复现成功：cell_01 Ah=3.252 / 单EKF=0.276 / 双EKF=17.923 pp；cell_02 对应 3.252 / 0.348 / 18.210 pp，与交接文档一致。

### 已知限制（待后续修复，详见 ALGORITHM_AUDIT.md）
1. 在线 OCV 被 `rest_late` 门控冻结，参数 EKF 失去 SOC 可观测性（根因）。
2. `param_update_accepted` 标志与真实更新不一致（诊断失真）。
3. 参数贴边（τ 贴下界 2.0s）无告警。
4. 选择性 K 掩码破坏协方差一致性（Joseph 更新与掩码不一致）。
5. 双 EKF 共享电压观测，未处理交叉协方差。
