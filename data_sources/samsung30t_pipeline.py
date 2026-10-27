#!/usr/bin/env python3
"""Samsung 30T real-data import and EKF OCV plausibility study.

No fabricated data: only runs when public measured MAT/CSV files exist locally.
The reference from C/20 is explicitly a quasi-OCV, not equilibrium OCV.
This script is independent of Streamlit and writes inspectable diagnostics.
"""
import argparse, copy, json, math, re, sys, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.io import loadmat

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bmslab.engine import load_config,read_ocv,run_analysis,run_parameter_ekf,ocv_and_slope
from bmslab.reports import save_report


def flatten_mat(obj, prefix='', depth=0):
    if depth>8:return {}
    out={}
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k.startswith('__'):continue
            out.update(flatten_mat(v, f'{prefix}.{k}' if prefix else k, depth+1))
    elif hasattr(obj,'_fieldnames'):
        for k in obj._fieldnames:
            out.update(flatten_mat(getattr(obj,k),f'{prefix}.{k}' if prefix else k,depth+1))
    elif isinstance(obj,np.ndarray) and obj.dtype.names:
        for name in obj.dtype.names:
            out.update(flatten_mat(obj[name],f'{prefix}.{name}',depth+1))
    elif isinstance(obj,np.ndarray) and obj.dtype==object and obj.size==1:
        out.update(flatten_mat(obj.item(),prefix,depth+1))
    else:
        arr=np.asarray(obj)
        if arr.size>=15 and arr.ndim<=2 and np.issubdtype(arr.dtype,np.number):
            out[prefix]=arr.squeeze()
    return out


def load_samsung_mat(path):
    p=Path(path)
    if p.suffix.lower()=='.csv':
        d=pd.read_csv(p)
        return {str(k):np.asarray(d[k]) for k in d}
    if p.suffix.lower()!='.mat': raise ValueError(f'Not a MATLAB file: {p}')
    try:
        return flatten_mat(loadmat(p, simplify_cells=True))
    except (NotImplementedError, ValueError) as exc:
        raise RuntimeError('MATLAB v7.3 HDF5 file: export to CSV using MATLAB, or install h5py and inspect its HDF5 structure') from exc


def choose_key(data,aliases,exclude=()):
    options=[]
    for k,v in data.items():
        a=np.asarray(v)
        if a.ndim!=1 or a.size<20:continue
        s=re.sub('[^a-z0-9]','',k.lower())
        if any(a0 in s for a0 in exclude):continue
        for a0 in aliases:
            alias=re.sub('[^a-z0-9]','',a0.lower())
            if alias and (s.endswith(alias) or alias in s):
                score=100 if s.endswith(alias) else 40
                if 'meas' in s:score+=10
                options.append((score,int(a.size),k))
                break
    if not options:return None
    return sorted(options,reverse=True)[0][2]


def normalize_times(t, label):
    t=np.asarray(t,dtype=float); t=t-t[0]
    good=np.diff(t)
    if not np.all(np.isfinite(t)) or np.any(good<=0):raise ValueError('Time not monotonic')
    step=np.median(good)
    if 'hour' in label.lower() or (step<0.01 and t[-1]<100):t*=3600.
    elif 'minute' in label.lower():t*=60.
    elif step>100 and t[-1]>1.e5: t/=1000.
    return t


def parse_meas(path, polarity='auto'):
    values=load_samsung_mat(path)
    time_key=choose_key(values,['testtime','steptime','relative_time','elapsedtime','time','time_s'])
    cur_key=choose_key(values,['current','current_a','amps'],exclude=['capacity'])
    volt_key=choose_key(values,['voltage','voltage_v','cellvoltage'])
    if not all([time_key,cur_key,volt_key]):
        raise ValueError(f'Missing time/current/voltage; available columns: {list(values)[:60]}')
    length=min(len(np.atleast_1d(values[x])) for x in [time_key,cur_key,volt_key])
    raw_t=np.asarray(values[time_key]).flatten()[:length]
    curr=np.asarray(values[cur_key],dtype=float).flatten()[:length]
    volt=np.asarray(values[volt_key],dtype=float).flatten()[:length]
    t=normalize_times(raw_t,time_key)
    if polarity=='negative_discharge':
        curr=-curr
    elif polarity=='auto':
        # A net discharge makes both capacity integrate positive and voltage fall.
        # For discharge-then-charge tests, covariance against cumulative Ah helps
        # recover sign even when median I is close to zero.
        ah=np.r_[0.,np.cumsum(curr[:-1]*np.diff(t)/3600)]
        ix=np.isfinite(ah)&np.isfinite(volt)
        rho=np.corrcoef(ah[ix],volt[ix])[0,1] if ix.sum()>100 and np.std(ah[ix])>0 else np.nan
        if np.isfinite(rho) and rho>0.2:
            curr=-curr
        elif not np.isfinite(rho) and np.nanmedian(curr)<-0.01:
            curr=-curr
    # positive_discharge assumes supplied current already follows that convention.

    if np.nanmedian(volt)>10:volt=volt/1000
    temp_key=choose_key(values,['batterytemp','temperature','celltemp'])
    d=pd.DataFrame({'time_s':t,'current_a':curr,'cell_01_v':volt})
    if temp_key and len(np.asarray(values[temp_key]).flatten())>=length:
        d['temperature_c']=np.asarray(values[temp_key],dtype=float).flatten()[:length]
    d=d.replace([np.inf,-np.inf],np.nan).dropna(subset=['time_s','current_a','cell_01_v'])
    d=d.drop_duplicates('time_s').sort_values('time_s').reset_index(drop=True)
    if len(d)<100:raise ValueError('Too few valid rows')
    if not (1.8 < d.cell_01_v.median()<4.4):raise ValueError('Voltage unit/column does not resemble a cell')
    d.attrs['input_keys']={'time':time_key,'current':cur_key,'voltage':volt_key,'temp':temp_key}
    return d


def pick_file(root,pattern):
    files=[p for p in root.rglob('*.mat') if not p.name.startswith('._')]
    if not files:
        files=list(root.rglob('*.csv'))
    files=[p for p in files if pattern.lower() in p.name.lower()]
    return files


def extract_archives(root):
    for p in root.rglob('*.zip'):
        out=p.with_suffix('')
        if out.is_dir():continue
        out.mkdir(exist_ok=True)
        with zipfile.ZipFile(p) as z:
            for member in z.infolist():
                member_path=Path(member.filename)
                if member_path.is_absolute() or '..' in member_path.parts:continue
                z.extract(member,out)


def build_quasi_ocv(reference):
    """Use slow C/20 discharging as non-equilibrium approximation.
    Only if a full sweep is present. Do not claim true OCV.
    """
    I=reference.current_a.to_numpy(); t=reference.time_s.to_numpy();v=reference.cell_01_v.to_numpy()
    dt=np.diff(t,prepend=t[0]);
    # Allow either sign but requires continuous actual discharge.
    positive=(I>0.01)
    if np.mean(positive)<0.15:
        raise ValueError('Reference does not look like a discharge; check polarity')
    # Select longest low-rate discharge run and ignore charge/rest segments
    valid=positive & (I<0.55)
    change=np.diff(np.r_[False,valid,False].astype(int))
    runs=[(s,e) for s,e in zip(np.where(change==1)[0],np.where(change==-1)[0])]
    if not runs:raise ValueError('No low-current discharge interval found')
    s,e=max(runs,key=lambda se:t[se[1]-1]-t[se[0]])
    tt=t[s:e];cc=I[s:e];vv=v[s:e]
    if len(tt)<1000:raise ValueError('Reference low-current segment too short')
    dq=np.r_[0,np.cumsum(cc[:-1]*np.diff(tt)/3600)]
    q=dq[-1]
    if not 2.1<q<3.6:raise ValueError(f'C20 capacity {q:.2f}Ah does not cover a near-full Samsung 30T discharge')
    soc=100*(1-dq/q)
    d=pd.DataFrame({'soc_pct':soc,'ocv_v':vv})
    # Smooth voltage within SOC bins; enforce physical monotonicity by cummax in SOC order.
    d['soc_bin']=np.clip(np.round(d.soc_pct/2)*2,0,100)
    tab=d.groupby('soc_bin',as_index=False).agg(ocv_v=('ocv_v','median'))
    tab=tab.rename(columns={'soc_bin':'soc_pct'}).sort_values('soc_pct')
    tab=tab.set_index('soc_pct').reindex(np.arange(0,101,2)).interpolate(limit_direction='both').reset_index()
    tab['ocv_v']=np.maximum.accumulate(tab.ocv_v.to_numpy())
    return tab,{'reference_discharge_capacity_ah':float(q),'reference_samples':int(e-s),
                 'source':'C/20 terminal voltage, non-equilibrium quasi-OCV',
                 'duration_h':float((tt[-1]-tt[0])/3600)}


def diagnostics(table, results, cfg, meta):
    df=results
    t=df.time_s.to_numpy(); I=df.current_a.to_numpy()
    params=['online_ocv_v','r0_mohm','r1_mohm','tau_s','param_innovation_mv']
    r={}
    for p in params:
        col='cell_01_v_'+p
        if col in df:
            x=pd.to_numeric(df[col],errors='coerce').to_numpy()
            r[p]={'min':float(np.nanmin(x)),'median':float(np.nanmedian(x)),'max':float(np.nanmax(x))}
    prefix='cell_01_v_'
    zref=df.get('reference_soc_pct')
    if zref is not None:
        ref= np.interp(np.clip(zref,0,100),table.soc_pct,table.ocv_v)
        err=(df[prefix+'online_ocv_v'].to_numpy()-ref)*1000
        r['ocv_vs_quasi_ocv_mae_mv']=float(np.nanmean(np.abs(err)))
        r['ocv_vs_quasi_ocv_rmse_mv']=float(np.sqrt(np.nanmean(err**2)))
        r['ocv_vs_quasi_ocv_bias_mv']=float(np.nanmean(err))
    modes=df[prefix+'mode'].value_counts().to_dict()
    upd=df[prefix+'param_update_accepted'].astype(bool)
    r['mode_seconds']={str(k):int(v) for k,v in modes.items()}
    r['measurement_update_count']=int(upd.sum())
    r['total_samples']=int(len(df))
    r['note']='OCV comparison only against C/20 quasi-OCV and charge-anchor Ah proxy; not independently validated true OCV.'
    return r


def run(args):
    base=Path(args.data).expanduser().resolve()
    if not base.is_dir():raise ValueError(f'Missing directory: {base}')
    extract_archives(base)
    dyn=Path(args.dynamic) if args.dynamic else None
    ref=Path(args.reference) if args.reference else None
    if dyn is None:
        cands=pick_file(base,'25degc')
        drive=[p for p in cands if any(k in p.name.lower() for k in ['mixed','udds','hwfet','la92','us06'])]
        if not drive:drive=pick_file(base,'C1202')
        if not drive:raise FileNotFoundError('No Samsung 30T drive/alternating dataset; download original MAT files first')
        dyn=max(drive,key=lambda p:p.stat().st_size)
    if ref is None:
        cands=pick_file(base,'C20DisCh')
        ref=next((p for p in cands if '25degc' in p.name.lower()),cands[0] if cands else None)
        if ref is None:raise FileNotFoundError('No same-cell C20DisCh file; pass --reference .mat')
    data=parse_meas(dyn,args.current_sign)
    anchor=parse_meas(ref,args.current_sign)
    table,quasi=build_quasi_ocv(anchor)
    if len(data)<300 or data.time_s.iloc[-1]<3600:
        raise ValueError('Selected measured dynamic data does not cover more than one hour')
    cfg=load_config();cfg['nominal_capacity_ah']=float(quasi['reference_discharge_capacity_ah'])
    cfg['start_soc_pct']=args.initial_soc
    par=cfg['parameter_filter']
    # Exploratory numerical seeds, not measured model parameters.
    par.update({'r0_mohm':args.r0_mohm,'r1_mohm':args.r1_mohm,'tau_s':args.tau_s,
                'step_min_a':0.6,'rest_current_a':0.05,'deep_rest_seconds':120.0,
                'max_voltage_innovation_mv':250.})
    par['initial_sigma'].update({'ocv_mv':40.0,'r0_mohm':5.0,'r1_mohm':3.0,'tau_s':20.0})
    par['sigma_change_over_100s'].update({'ocv_mv':6.0,'r0_mohm':0.25,'r1_mohm':0.2,'tau_s':4.0})
    par['sigma_measurement_mv']=10.0
    # Configurable numerical bounds; not a claim that Samsung parameters are in this range.
    par['r0_max_mohm']=65.0;par['r1_max_mohm']=80.0
    data['reference_soc_pct']=args.initial_soc-100*np.r_[0,np.cumsum(data.current_a.to_numpy()[:-1]*np.diff(data.time_s)/3600)]/cfg['nominal_capacity_ah']
    configdesc={'input_dynamic':str(dyn),'input_reference':str(ref),'reference_origin':'assumed 100% at dynamic segment start + C/20 capacity, NOT independently measured SOC',
                'parameter_priors':'exploratory engineering guesses, not Samsung 30T measured HPPC parameters',
                'offline_reference':quasi,'current_sign':args.current_sign}
    ox=table.soc_pct.to_numpy()/100;oy=table.ocv_v.to_numpy()*1000
    outroot=Path(args.output);outroot.mkdir(parents=True,exist_ok=True)
    table.to_csv(outroot/'samsung30t_quasi_ocv.csv',index=False)
    variants={
        'strict_rest_only':{},
        'exploratory_dynamic_ocv':{'polarization':[True,False,True,True],
                                   'steady':[True,False,False,False]},
    }
    compare={}
    for name,overrides in variants.items():
        cfg_v=copy.deepcopy(cfg)
        cfg_v['parameter_filter']['mode_update_mask'].update(overrides)
        cfg_v['parameter_filter']['mode_q_multipliers']['polarization'][0]=2 if name.startswith('exploratory') else 0.05
        cfg_v['parameter_filter']['mode_q_multipliers']['steady'][0]=0.5 if name.startswith('exploratory') else 0.1
        out,metrics=run_analysis(data,cells=['cell_01_v'],cfg=cfg_v,ocv_table=(ox,oy))
        outdir=save_report(out,metrics,cells=['cell_01_v'],output_dir=outroot/name,
                           context={**configdesc,'settings':cfg_v,'run_name':name})
        summary=diagnostics(table,out,cfg_v,configdesc)
        # Diagnostic count of actual OCV change; accepted update count alone includes other states.
        ocv=out['cell_01_v_online_ocv_v'].to_numpy()
        summary['ocv_nonzero_step_count']=int(np.sum(np.abs(np.diff(ocv))>1e-7))
        summary['ocv_total_change_mv']=float(1000*(ocv[-1]-ocv[0]))
        if len(out)>0:
            from matplotlib import pyplot as plt
            fig, axs=plt.subplots(3,1,figsize=(11,10),sharex=True)
            h=out.time_s.to_numpy()/3600
            axs[0].plot(h,out.cell_01_v_online_ocv_v,label='KF online OCV')
            axs[0].plot(h,np.interp(out.reference_soc_pct.to_numpy(),table.soc_pct,table.ocv_v),label='C/20 quasi-OCV at assumed Ah-SOC',alpha=.8)
            axs[0].set_ylabel('Cell voltage (V)');axs[0].legend();axs[0].grid(alpha=.2)
            axs[1].plot(h,out.cell_01_v_r0_mohm,label='R0')
            axs[1].plot(h,out.cell_01_v_r1_mohm,label='R1')
            axs[1].set_ylabel('Resistance (mOhm)');axs[1].legend();axs[1].grid(alpha=.2)
            axs[2].plot(h,out.cell_01_v_param_innovation_mv,label='Parameter-filter innovation')
            axs[2].set_ylabel('Innovation (mV)');axs[2].set_xlabel('Elapsed hours');axs[2].legend();axs[2].grid(alpha=.2)
            fig.tight_layout();fig.savefig(outdir/'OCV_PARAMETER_DIAGNOSTICS.png',dpi=160);plt.close(fig)
        (outdir/'ONLINE_OCV_DIAGNOSTICS.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
        compare[name]=summary
    (outroot/'SOURCES_AND_LIMITATIONS.md').write_text(
        '# Samsung INR21700-30T online OCV plausibility analysis\n\n'
        f'- Measured dynamic file: `{dyn.name}`\n- Low-rate reference: `{ref.name}`\n'
        '- Original McMaster / Mendeley DOI: https://doi.org/10.17632/9xyvy2njj3.2\n'
        '- C/20 **terminal voltage** is quasi-OCV, not equilibrium OCV.\n'
        '- SOC reference in report is an **Ah-based proxy assuming initial anchor**, not independently validated true SOC.\n'
        '- Both variants use guessed R0/R1/tau and covariance tuning; results cannot establish true-OCV identification accuracy.\n'
        '- Strict: OCV update only during sufficiently long rest. Exploratory: OCV allowed even in steady mode; high risk of absorbing unmodeled polarization.\n'
        '- Compare same-cell HPPC priors and independent intermittent rest OCV before making engineering claims.\n'
        '- No physical 16S measurements; data is one cell.\n',encoding='utf-8')
    (outroot/'SUMMARY.json').write_text(json.dumps({'data':configdesc,'modes':compare},indent=2,ensure_ascii=False))
    print(json.dumps({'input_dynamic':str(dyn),'input_ocv':str(ref),'output':str(outroot),'results':compare},indent=2,ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='Measured Samsung 30T online OCV evaluation (needs actual files)')
    ap.add_argument('--data',default=str(ROOT/'datasets'))
    ap.add_argument('--dynamic');ap.add_argument('--reference')
    ap.add_argument('--output',default=str(ROOT/'outputs/samsung30t_validation'))
    ap.add_argument('--current-sign',choices=['auto','negative_discharge','positive_discharge'],default='auto')
    ap.add_argument('--initial-soc',type=float,default=100)
    ap.add_argument('--r0-mohm',type=float,default=12.)
    ap.add_argument('--r1-mohm',type=float,default=6.)
    ap.add_argument('--tau-s',type=float,default=30.)
    run(ap.parse_args())
