"""Generate synthetic LFP + graphite data with known ground truth.
NOT measured vehicle data or evidence for algorithm performance.
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
KNOT_SOC=np.array([0,4,10,15,30,48,55,58,65,70,85,95,100],dtype=float)
KNOT_OCV=np.array([2.89,3.18,3.265,3.287,3.300,3.315,3.322,3.325,3.350,3.357,3.374,3.414,3.56])

def main():
    rng=np.random.default_rng(20261026)
    N=5400;t=np.arange(N,dtype=float);I=np.empty(N)
    # City-driving-inspired synthetic pulses with rest intervals; not a real drive cycle.
    for k in range(N):
        block=k//90
        current=[18,45,85,60,22,0,35,75,40,0,90,20][block%12]
        I[k]=float(current)+(rng.normal(0,1) if current else rng.normal(0,0.15))
    true_capacity=90.;true_soc=np.empty(N);true_soc[0]=94.
    r0=.85;r1=.35;tau=38.;up=0.;obs=np.empty(N)
    for k in range(N):
        if k:true_soc[k]=max(0.,true_soc[k-1]-I[k-1]/3600/true_capacity*100)
        if k:
            a=np.exp(-1/tau);up=a*up+(1-a)*r1*I[k-1]
        ov=np.interp(true_soc[k],KNOT_SOC,KNOT_OCV)*1000
        obs[k]=(ov-r0*I[k]-up+rng.normal(0,1.3))/1000
    df=pd.DataFrame({'time_s':t,'pack_current_a':I,
        'cell_01_v':obs,'cell_02_v':obs+0.0008+rng.normal(0,.0005,N),
        'temperature_c':25+0.6*np.sin(t/1800),
        'reference_soc_pct':true_soc,
        'bms_soc_pct':np.clip(true_soc + 1.2 + 0.2*np.sin(t/150),0,100),
        'vehicle_state':np.where(I<1,'rest','driving')})
    out=ROOT/'demo';out.mkdir(exist_ok=True)
    df.to_csv(out/'synthetic_vehicle_1Hz.csv',index=False)
    pd.DataFrame({'soc_pct':KNOT_SOC,'ocv_v':KNOT_OCV}).to_csv(out/'synthetic_ocv_table.csv',index=False)
    print(f'Generated synthetic data: {out} (5400 rows, true SOH=90%, unknown to estimators)')

if __name__=='__main__':main()
