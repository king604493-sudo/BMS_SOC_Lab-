# CODEX_START_HERE — 开始执行的第一份任务

## 目的
接管本地Python工程 `BMS_SOC_Lab`，为后续真实三元电芯与16串整车数据的SOC及在线OCV研究建立可审计、可复现的实验基线。当前项目**不是**量产算法。

## 首轮具体动作（按顺序）

1. 阅读 `AGENTS.md`、`docs/软件设计说明书.md`、`README.md`、`config.json`、`bmslab/engine.py`、`RUN_SAMSUNG_30T.md`。
2. 建立Python虚拟环境，安装 `requirements.txt`；运行所有现有测试并报告通过/失败数量。
3. 运行合成数据CLI仿真，记录SHA256、使用的配置、每节电芯Ah/单EKF/双EKF RMSE；输出位置在`outputs/codex_baseline/`。
4. 审核 `run_parameter_ekf` 的Q/P/R单位、雅可比、极化状态、选择性K掩码、后验协方差Joseph更新；给出**可复现的失效原因分析**，不要仅凭低SOC RMSE判断参数辨识是否准确。
5. 增加能够暴露模型错误的最小测试（例如电流恒定不可区分OCV和R0、缺少静置OCV不得更新、参数贴边告警、缺失参考不输出RMSE、1Hz阶跃不等于瞬时DCIR）。
6. 先提交诊断报告与建议补丁，再考虑算法架构性修改。保留原算法以便A/B对照。

## 后续阶段（获得真实MAT文件之后）

真实文件必须来自 `https://data.mendeley.com/datasets/9xyvy2njj3/2`，并记录文件大小/哈希与实际持续时间。需要25℃动态工况及同型号C/20准OCV参考。分别运行严格静置OCV更新与探索性动态OCV更新，给出更新次数和参数可信度。若无法下载，明确停止真实验证，不得使用合成MAT替代。

## 完成输出

- `outputs/codex_baseline/REPORT.md`：完整真实执行日志与代码版本
- `docs/ALGORITHM_AUDIT.md`：数学/单位/协方差/可辨识性问题清单
- `tests/`：新增可运行的最小回归测试
- `docs/CHANGELOG.md`：每项改动、原因、已知限制

## 重要提醒

用户原先想利用五工况各自固定Q矩阵来适应1秒采样的量产条件；无需在MCU上实时计算Fisher信息。离线数据可用灵敏度/Fisher分析辅助标定，但**不能直接等同于Q的最优数值**。在线OCV暂不回写SOC查表，后续采用双SOC锚点/累计容量进行新OCV曲线的实验性重构。
