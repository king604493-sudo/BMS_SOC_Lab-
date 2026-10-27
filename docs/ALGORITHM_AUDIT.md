# 算法审计报告（ALGORITHM_AUDIT）

**版本：** V0.1（Codex 首轮交接审计）
**日期：** 2026-10-26
**审计对象：** `bmslab/engine.py` 的 `run_parameter_ekf`（参数 EKF / 双 EKF 第一级）
**结论性质：** 基于合成数据可复现的失效原因分析，非量产算法判定。

---

## 1. 一句话结论

双 EKF 在合成数据上 SOC RMSE 高达 17.9~18.2 pp 的**根因不是数值发散，而是结构性可辨识性缺陷**：在线 OCV 在整个 5400 秒行程中**从未被更新一次**（被 `rest_late` 门控永久冻结），导致参数 EKF 无法通过 OCV 追踪 SOC 变化，转而把 SOC 漂移错误地归因到 R0/R1/τ 上，最终污染了喂给第二级 SOC EKF 的阻抗参数。

---

## 2. 可复现基线

| 项目 | 值 |
|---|---|
| 合成数据 SHA256 | `aafed0c9097f14c18cb403bcbc002658e2d597edeb4d8ed28c34aa5cb676e30e` |
| OCV 表 SHA256 | `37b70c41097ee858ccacef1acab5a061bea4c6f26f0e3f24c6fd55a205f76a05` |
| 数据规模 | 5400 行，1 Hz |
| 配置 | `config.json`（未改） |

| 电芯 | Ah RMSE | 单 EKF RMSE | 双 EKF RMSE |
|---|---:|---:|---:|
| cell_01 | 3.252 pp | 0.276 pp | **17.923 pp** |
| cell_02 | 3.252 pp | 0.348 pp | **18.210 pp** |

与交接文档 §2 记录的基线**完全一致**，复现成功。

---

## 3. 关键失效证据（实测）

用诊断脚本对 `run_parameter_ekf` 的输出做如下统计：

| 观测项 | 实测值 | 判定 |
|---|---|---|
| 五工况分布 | `steady` 1755s / `step` 45s / `polarization` 2700s / `rest_early` 900s / **`rest_late` 0s** | 全程无一次 `rest_late` |
| 在线 OCV 唯一值个数 | **1**（初值全程不变） | OCV 从未更新 |
| 在线 OCV 漂移 | 0.0 mV | OCV 冻结 |
| τ（tau）范围 | 2.0 ~ 113.76 s | **贴到下界 2.0** |
| R1 范围 | 0.086 ~ 1.671 mΩ | 远超初始 0.4 mΩ 的合理区间 |
| `param_update_accepted` 计数 | **5400（全部）** | 标志失真（见 §4.3） |
| 创新残差均值 / 标准差 | -20.5 mV / 22.6 mV | 系统性偏置 |
| 创新 >\|50mV\| 占比 | 14.3% | 明显非白噪声 |

---

## 4. 失效原因分析（按严重程度排序）

### 4.1 【根因】OCV 被 `rest_late` 门控永久冻结，参数 EKF 失去 SOC 可观测性

`classify_modes` 只在连续静置 ≥ `deep_rest_seconds`（默认 300s）时才产出 `rest_late`；而 `mode_update_mask['rest_late'] = [true, false, false, false]` 是**唯一允许 OCV 更新的模式**。本合成数据全程没有超过 300s 的连续静置，因此：

- OCV 状态 `x[0]` 的 Kalman 增益 `K[0]` 始终被 `K[~allowed]=0` 强制清零；
- OCV 一直停留在初值 `ocv_init_mv`（由 `start_soc_pct=88%` 查表得到）；
- SOC 在 5400 秒内从 88% 放电到 26%，对应的 OCV 本应下降数百 mV，但参数 EKF 完全看不见这个变化。

**后果：** 参数 EKF 的观测方程 `V = OCV - I·R0 - Up` 中，左侧端电压随 SOC 单调下降，右侧 OCV 却被钉死，于是滤波器只能靠 R0/R1/τ/Up 去"凑"这个偏差，导致阻抗参数被系统性污染。

### 4.2 【根因】电压残差被错误归因到 R0/R1/τ，而非 OCV

由于 OCV 不可更新，`polarization`（2700s，占一半时长）和 `step`（45s）模式下允许更新的只有 R0/R1/τ。这些参数被迫吸收本应由 OCV 承担的 SOC 漂移：

- τ 漂移到下界 2.0s（贴边，见 4.4）；
- R1 漂移到 0.086~1.671 mΩ，偏离初始 0.4 mΩ 达 4 倍以上。

被污染的 R0/R1/τ 再作为 `r0_series/r1_series/tau_series` 输入第二级 `run_1rc_ekf`，直接拉高 SOC RMSE。这解释了为何**双 EKF 反而比单 EKF 差 60 倍**——单 EKF 用的是固定初始阻抗参数（0.75/0.40 mΩ），反而更接近合成数据的真实生成参数。

### 4.3 【缺陷】`param_update_accepted` 标志与真实更新不一致

`engine.py:281`：

```python
accepted[k] = bool(np.any(allowed))
```

该行在**增益掩码之后**执行，但判断依据是"是否有参数被 `allowed` 允许"，而不是"本次测量更新是否真的改变了状态"。当 4 个参数全被 mask（`steady` 模式）、或创新超门限被整体拒绝时，`accepted` 仍可能为 `True`。实测 `steady` 样本 `accepted=True`，而该模式 `mode_update_mask['steady']=[false,false,false,false]` 根本不允许任何参数更新。

**后果：** `samsung30t_pipeline.diagnostics()` 用 `param_update_accepted.sum()` 统计"测量更新次数"，这个数字被系统性高估，无法作为 OCV 是否真正被辨识的依据。诊断层因此无法发现 OCV 冻结问题。

### 4.4 【缺陷】参数贴边无告警

`engine.py:278`：

```python
x = np.clip(x + K * residual, limits_lo, limits_hi)
```

参数被 `clip` 到物理边界（τ 下界 2.0s、R0/R1 上下界）时，**没有任何告警或标志**。实测 τ 全程贴在下界 2.0s，但滤波器静默继续，下游无法察觉参数已失去自由度。这正是 `AGENTS.md` "高优先级已知问题"中"参数贴边告警"所要求的。

### 4.5 【结构性问题】选择性 K 掩码破坏协方差一致性

`engine.py:273-280`：

```python
allowed = np.r_[allowed, modes[k] != 'rest_late']
K[~allowed] = 0.
x = np.clip(x + K * residual, limits_lo, limits_hi)
J = np.eye(5) - np.outer(K, H)
P = J @ P @ J.T + R*np.outer(K, K)
```

这里在**增益 K 计算之后**才把不允许更新的分量清零，然后仍用被截断的 K 走 Joseph 形式更新 P。数学上：

- 正确做法是**先**把不允许更新的状态从观测雅可比 H 和状态向量中排除，再对缩小子空间做标准 KF 更新；或显式使用 `H_masked` 计算增益。
- 当前"先算全 K、再清零分量"会导致 `J = I - K H^T` 中，被 mask 的状态对应的 P 更新项 `K[i]·H` 不为零被扣掉，但 K[i] 又已是 0，两者不一致，破坏 `P = J P J^T + K R K^T` 的协方差一致性。

**后果：** P 不再是真实后验协方差，可能出现过度自信（P 过小），使后续样本的增益进一步失真。这与 `AGENTS.md` "测量更新掩码在增益计算后清零分量，需检查后验协方差与数学一致性"的提示完全对应。

### 4.6 【已知限制】两个滤波器共享同一电压观测

参数 EKF 与 SOC EKF 都用 `cell_XX_v` 作为测量，但未建立交叉协方差。双 EKF 各自独立假设自己是最优，忽略了共享观测带来的相关性，会系统性低估不确定性。这是 `AGENTS.md` 已知问题，当前未处理。

---

## 5. 建议补丁（按优先级，均保留原算法以便 A/B）

> 依据 CODEX_START_HERE.md 第 6 条：先提交诊断与补丁建议，不直接重写估计器。

1. **P0 — 修复 `accepted` 标志语义**：改为"本步 K 的非零分量与状态实际变化是否非零"（如 `accepted[k] = bool(np.any(K != 0))`，且创新未超门限）。这是诊断准确性的前提。
2. **P0 — 参数贴边告警**：在 `np.clip` 前后比对，凡分量触及 `limits_lo`/`limits_hi` 即记录 `at_boundary` 标志或 warning，供下游判断参数是否失去自由度。
3. **P1 — 修正选择性掩码的协方差更新**：改为先构造被 mask 的 H（`H_masked = H * allowed`），用 `H_masked` 计算增益并让 `J = I - K H_masked^T` 保持一致；或在 mask 状态下跳过 P 更新、仅保留预测。
4. **P1 — OCV 更新的可辨识性门控重设计**：当前"仅 `rest_late` 更新 OCV"在真实行驶数据里大概率也永不触发（静置 ≥300s 稀缺）。建议：
   - 显式区分"严格静置 OCV 更新"与"探索性动态 OCV 更新"（后者已是 `samsung30t_pipeline.py` 的 `exploratory_dynamic_ocv` 变体），并明确标注后者会吸收极化偏差；
   - 增加"OCV 更新次数"的独立计数（已在 pipeline 里用 `ocv_nonzero_step_count` 部分实现），并把它作为 OCV 辨识成功与否的硬门槛。
5. **P2 — 恒流不可辨识保护**：在低电流变化率下，OCV 与 R0 线性相关（都乘 I），无法分离。应检测 `|ΔI|` 持续过小时，明确将 OCV/R0 标记为不可辨识，而非静默输出一个看似稳定的值。

---

## 6. 给后续阶段的底线

- **不得**仅凭低端电压残差（电压拟合好）判定 OCV/R0/SOC 辨识正确——本案例电压残差 std 仅 22.6 mV，但 SOC RMSE 高达 18 pp。
- 真实三星 30T 数据尚未下载，`samsung30t_pipeline.py` 的参考 SOC 来自"假定满电 + C/20 容量"，是代理值而非真值；OCV 对比只能针对 C/20 准 OCV，不能声称绝对精度。
- 五工况 Q 乘数均为仿真假设，未标定，不能等同于 Q 的最优数值。
