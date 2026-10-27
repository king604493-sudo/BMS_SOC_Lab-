# 公开三元锂放电与OCV数据：来源与真实性说明

检索日期：2026-10-26。

## 结论
尚未找到同时具备 **便携储能、16S、NMC、连续放电>1h、每串单体电压、相同电芯OCV曲线** 的可核验公开原始数据。
**绝不能把单电芯数据复制16次并宣称为真实16S实测。**

## 推荐的真实单电芯替代数据（均为三星 INR21700-30T NMC）

1. 动态激励：Pillai 等，2022，约7200点、1秒采样（约2小时），电流电压，Mendeley `h3yfxtwkjz/1`：https://data.mendeley.com/datasets/h3yfxtwkjz/1
2. OCV标定：Sundaresan 等，2022，同型号Samsung 30T，以C/30缓慢充放电、各约30小时，数据用于构建pseudo-OCV–SOC表，Mendeley `m9w7grpjc7/1`：https://data.mendeley.com/datasets/m9w7grpjc7/1
3. 动态驾驶/HPPC：Kollmeyer 等，Samsung 30T，Mendeley `9xyvy2njj3/2`：https://data.mendeley.com/datasets/9xyvy2njj3/2

⚠ 数据来自**同型号**电芯，但不是已证实的同一物理样品；OCV仍需注意SOH与实验温度。第二项是准静态OCV测量，并非严格无限静置热力学平衡OCV。

## 下载到Mac移动硬盘

Mac终端执行（替换移动硬盘名字）：

```bash
python3 fetch_mendeley.py --output '/Volumes/你的硬盘名/BMS_SOC_Lab/datasets' --dataset all
```

下载脚本依赖Mendeley公开API可访问且文件具备有效下载链接。API若变化，脚本会打印原始数据集页面，届时需浏览器手工下载。

## 后续仿真注意

动态激励数据约7200秒，但不等于已核验“连续放电2小时”：其中电流可能有充电/休息。需下载后检查电流符号和连续放电区间。OCV数据需要先根据积分容量重建OCV-SOC曲线。单电芯数据无法验证16S电芯一致性、均衡和最弱单体SOC。
