# 三星 INR21700-30T 双EKF在线OCV验证流程

## 本次状态（2026-10-26）

- **真实原始数据尚未下载成功。** 当前ChatGPT运行容器无法解析 `api.data.mendeley.com` 主机名；下载脚本在此环境返回DNS错误。**不能用示例数据的结果冒充三星实测。**
- 已完成 `.mat` 导入、C/20准OCV参考重建、1秒序列处理、两套OCV更新策略对照、逐秒R0/R1/τ与OCV输出、异常与残差诊断。
- 已使用**人工生成的测试MAT文件**执行端到端自检；本次验证仅代表软件接口联通。

## 原始数据链接与许可证

研究团队（Phillip Kollmeyer / Michael Skells），McMaster University，三星30T：
https://data.mendeley.com/datasets/9xyvy2njj3/2
DOI: https://doi.org/10.17632/9xyvy2njj3.2 (CC BY 4.0)。

建议下载**同一数据集的25℃两类文件**：
- `_C20DisCh_25degC_...mat`：低倍率完整放电，用来构造**准OCV**曲线（不是完全平衡OCV）。
- `_Mixed..._25degC_...mat` 或 `_UDDS_25degC_...mat`：放电电流变化工况，优先选时长超过3600秒的数据。

不要混用不同化学体系的OCV，亦不要把不同样品的曲线误写成相同样品实验。

## Mac启动（在有互联网的Mac上）

打开终端，进入解压后的 `BMS_SOC_Lab` 文件夹，然后执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python data_sources/download_30t_focus.py --output datasets/30t_measured
python data_sources/samsung30t_pipeline.py --data datasets/30t_measured --output outputs/samsung30t_validation
```

如果Mendeley下载接口拒绝连接，请访问原始数据页面，**手动下载两个MAT文件**放入 `datasets/30t_measured`，然后只执行最后一条命令。

如果自动文件选择失败，则明确传入文件路径：

```bash
python data_sources/samsung30t_pipeline.py \
  --data datasets/30t_measured \
  --dynamic 'datasets/30t_measured/实际Mixed文件.mat' \
  --reference 'datasets/30t_measured/实际C20DisCh文件.mat' \
  --output outputs/samsung30t_validation
```

默认假设动态工况起始SOC为100%，电池容量由C/20放电量估计；**这种SOC属于代理参考值，并非独立真值**。如果原始实验不从满充起始，就必须修改`--initial-soc`，并另行建立真实锚点。

## 运行输出

每个模式输出：
- `timeseries_results.csv`：逐秒测量端电压、安时积分SOC、单EKF SOC、双EKF SOC、在线OCV、R0、R1、τ、残差和模式。
- `OCV_PARAMETER_DIAGNOSTICS.png`：在线OCV与准OCV、在线电阻、参数新息曲线。
- `ONLINE_OCV_DIAGNOSTICS.json`：在线OCV更新次数、OCV相对准OCV误差（**非真值误差**）、参数范围等。
- `SOC_cell_01_v.png`：SOC估算与代理参考SOC对比。
- `SOURCES_AND_LIMITATIONS.md`：方法边界。

### 对照策略

1. `strict_rest_only`：OCV只在充分静置时更新。若全程无长静置，则OCV可能保持不变，**不应被判定为成功在线辨识**。
2. `exploratory_dynamic_ocv`：即便非静置也允许OCV更新，用于**检验模型偏差是否被OCV吸收**。这不是量产参数设置。

### 判断成功的最低标准

- 至少出现有意义的OCV测量更新；
- R0/R1/τ无长期贴边、非物理跳变；
- 端电压残差无系统性模式依赖；
- 在独立OCV片段或经过充分静置的实验中验证在线OCV误差，单纯的C/20准OCV比较不能证明精度；
- 至少用未参与调参的另一条动态曲线复现结果。

## 风险提示

程序是**科研原型**，不是功能安全认证BMS。第一KF参数和Q/P/R目前都是仿真假设；第二KF即使SOC误差很低，也不证明第一KF辨识出的OCV正确。
