from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def create_plot(output:pd.DataFrame, *, cell:str, dest:Path):
    t=output['time_s'].to_numpy()/3600
    fig,ax=plt.subplots(figsize=(11,5.5))
    ax.plot(t,output['ah_soc_pct'],label='Ah integration',linewidth=1.0)
    if 'reference_soc_pct' in output:
        ax.plot(t,output['reference_soc_pct'],label='Reference SOC',linewidth=1.6,color='black')
    for suffix,name in [('single_soc_pct','1RC EKF'),('dual_soc_pct','Dual EKF prototype')]:
        col=f'{cell}_{suffix}'
        if col in output:ax.plot(t,output[col],label=name,linewidth=1.0)
    ax.set(xlabel='Elapsed time (h)',ylabel='SOC (%)',title=f'SOC estimates — {cell}')
    ax.set_ylim(-3,103);ax.grid(alpha=.22);ax.legend(loc='best')
    fig.tight_layout();fig.savefig(dest,dpi=160);plt.close(fig)


def save_report(output:pd.DataFrame,metrics:pd.DataFrame, *, cells:list[str], output_dir, context:dict):
    path=Path(output_dir).expanduser();path.mkdir(parents=True,exist_ok=True)
    output.to_csv(path/'timeseries_results.csv',index=False,encoding='utf-8-sig')
    metrics.to_csv(path/'soc_metrics.csv',index=False,encoding='utf-8-sig')
    with open(path/'run_config.json','w',encoding='utf-8') as f:json.dump(context,f,ensure_ascii=False,indent=2,default=str)
    for cell in cells[:8]:create_plot(output,cell=cell,dest=path/f'SOC_{cell}.png')
    lines=['# BMS SOC 仿真报告','','> 本地研究原型；不能作为量产安全/功能验证证明。','',
           '## 数据与算法','','- 安时积分：依赖输入的容量与初始SOC；不会校正初始误差。',
           '- 单EKF：一阶RC＋输入OCV表（离线），用于SOC状态估计。',
           '- 双EKF原型：第一组在线估算 OCV / R0 / R1 / τ，第二组用离线OCV表估算SOC。',
           '- 在线OCV仅输出历史序列，并未自动重构或写回离线OCV表。','',
           '## 参考SOC的有效性','','- 仅当提供独立且可信的参考SOC时，RMSE等指标才可理解为估算误差。',
           '- 如果参考SOC来自原车BMS显示值，这些指标只是与原车BMS的一致性，不是真实精度。',
           '- 若真实容量、初值或OCV表不准，估计偏差会受到影响。','','## 指标','']
    if len(metrics):
        lines+=['| Cell | Algorithm | RMSE (pp) | MAE (pp) | Max | Bias |',
                '|---|---|---:|---:|---:|---:|']
        for r in metrics.to_dict('records'):
            lines.append(f"| {r['cell']} | {r['algorithm']} | {r['rmse_pct']:.3f} | {r['mae_pct']:.3f} | {r['max_abs_error_pct']:.3f} | {r['bias_pct']:+.3f} |")
    else:lines+=['没有输入参考SOC，因此未计算误差指标。']
    lines+=['','## 工程限制','',
            '1. 1Hz数据不能可靠恢复亚秒级电流瞬态；R0阶跃辨识仅作为探索性近似。',
            '2. 五工况Q与参数KF更新门控均为待标定的示例值，不能据此推断老化后的OCV已被准确识别。',
            '3. 此模型未单独处理迟滞、滞后温度场、单体均衡与可辨识性失效。',
            '4. 电芯单体SOC与电池包SOC不必完全相同；对同一个包参考SOC的比较是探索性分析。',
            '5. 参数EKF未实现完整双滤波器交叉协方差，参数与状态误差可能相关。',
            '6. 在低激励工况下参数可能不可辨识，电压预测拟合好不等于参数辨识正确。',
            '','## 文件','',
            '- `timeseries_results.csv`：逐时刻SOC、辨识参数、残差和工况。',
            '- `soc_metrics.csv`：与参考SOC的RMSE/MAE等。',
            '- `SOC_cell_XX_v.png`：SOC曲线。',
            '- `run_config.json`：本次分析配置。']
    (path/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return path
