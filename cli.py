import argparse
import json
from pathlib import Path
import pandas as pd
from bmslab.engine import load_table,detect_columns,prepare_data,read_ocv,load_config,run_analysis
from bmslab.reports import save_report


def main():
    ap=argparse.ArgumentParser(description='Offline BMS SOC analysis')
    ap.add_argument('--input',required=True,help='CSV, XLSX, or Parquet path')
    ap.add_argument('--ocv',help='Validated offline OCV CSV/XLSX with soc_pct,ocv_v')
    ap.add_argument('--time-col');ap.add_argument('--current-col');ap.add_argument('--ref-col')
    ap.add_argument('--cell-cols',nargs='+');ap.add_argument('--capacity-ah',type=float)
    ap.add_argument('--initial-soc-pct',type=float);ap.add_argument('--invert-current',action='store_true')
    ap.add_argument('--output-dir',default='./outputs/cli_run');ap.add_argument('--config',default='config.json')
    args=ap.parse_args();cfg=load_config(args.config)
    if args.capacity_ah:cfg['nominal_capacity_ah']=args.capacity_ah
    if args.initial_soc_pct is not None:cfg['start_soc_pct']=args.initial_soc_pct
    raw=load_table(args.input,nrows=cfg.get('max_rows'))
    m=detect_columns(raw)
    time=args.time_col or m['time'];cur=args.current_col or m['current']
    cells=args.cell_cols or m['cells'][:2]
    if not cells:raise ValueError('缺少电芯电压列，请指定--cell-cols')
    prepared,warnings=prepare_data(raw,time,cur,cells,args.ref_col or m['reference'],invert_current=args.invert_current)
    ocv=read_ocv(args.ocv) if args.ocv else None
    result,metrics=run_analysis(prepared,cells=[f'cell_{i+1:02d}_v' for i in range(len(cells))],cfg=cfg,ocv_table=ocv)
    ctx={'input_path':args.input,'ocv_path':args.ocv,'settings':cfg,'original_cells':cells,'warnings':warnings}
    path=save_report(result,metrics,cells=[f'cell_{i+1:02d}_v' for i in range(len(cells))],output_dir=args.output_dir,context=ctx)
    print('Report:',path/'REPORT.md');print('Warnings:',warnings)
    print(metrics.to_string(index=False) if len(metrics) else 'No reference SOC; metrics omitted')

if __name__=='__main__':main()
