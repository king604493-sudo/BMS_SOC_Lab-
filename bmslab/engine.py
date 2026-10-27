from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import copy
import json
import math
import re
from typing import Optional

import numpy as np
import pandas as pd

try:
    from scipy.interpolate import RegularGridInterpolator
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False

DEFAULT_CONFIG_FILE = Path(__file__).resolve().parents[1] / 'config.json'
MODE_NAMES = ['step','polarization','steady','rest_early','rest_late']


class Impedance3DLookup:
    """三维阻抗查表：温度 × 电流幅值 × SOC → R0/R1/tau。

    表格 DataFrame 需含列：temperature_c, current_a, soc_pct, r0_mohm, r1_mohm, tau_s。
    查询用 RegularGridInterpolator 做三线性插值，边界外取最近值（clip）。
    """

    def __init__(self, table: pd.DataFrame,
                 temp_col='temperature_c', current_col='current_a', soc_col='soc_pct'):
        self._interps = {}
        temp_axis = np.array(sorted(table[temp_col].unique()), dtype=float)
        current_axis = np.array(sorted(table[current_col].unique()), dtype=float)
        soc_axis = np.array(sorted(table[soc_col].unique()), dtype=float)
        self._axes = (temp_axis, current_axis, soc_axis)
        self._axis_cols = (temp_col, current_col, soc_col)

        for param in ['r0_mohm', 'r1_mohm', 'tau_s']:
            if param not in table.columns:
                continue
            # 构建规则网格值 [temp, current, soc]，用 dict 精确填充
            nt, nc, ns = len(temp_axis), len(current_axis), len(soc_axis)
            values = np.full((nt, nc, ns), np.nan, dtype=float)
            idx = {t: i for i, t in enumerate(temp_axis)}
            cdx = {c: i for i, c in enumerate(current_axis)}
            sdx = {s: i for i, s in enumerate(soc_axis)}
            for _, row in table.iterrows():
                ti = idx.get(float(row[temp_col]))
                ci = cdx.get(float(row[current_col]))
                si = sdx.get(float(row[soc_col]))
                if ti is None or ci is None or si is None:
                    continue
                values[ti, ci, si] = float(row[param])
            # 缺失值填充：先沿 SOC 维，再沿电流维，最后温度维（线性外推/内插）
            if np.isnan(values).any():
                for ti in range(nt):
                    for ci in range(nc):
                        v = values[ti, ci, :]
                        if np.isnan(v).all():
                            continue
                        valid = ~np.isnan(v)
                        if valid.sum() >= 2:
                            values[ti, ci, :] = np.interp(
                                soc_axis, soc_axis[valid], v[valid])
                for ti in range(nt):
                    for si in range(ns):
                        v = values[ti, :, si]
                        valid = ~np.isnan(v)
                        if valid.sum() >= 2:
                            values[ti, :, si] = np.interp(
                                current_axis, current_axis[valid], v[valid])
            if _HAVE_SCIPY:
                self._interps[param] = RegularGridInterpolator(
                    self._axes, values, bounds_error=False, fill_value=None)
            else:
                self._interps[param] = None

    def query(self, temperature_c: float, current_a: float, soc_pct: float) -> dict:
        """给定温度(℃)、电流(A，符号任意，取幅值)、SOC(%)，返回 {r0_mohm, r1_mohm, tau_s}。"""
        t = float(temperature_c)
        i = float(abs(current_a))   # 阻抗查表用电流幅值
        s = float(np.clip(soc_pct, 0.0, 100.0))
        out = {}
        if not _HAVE_SCIPY:
            raise RuntimeError('三维查表需要 scipy（RegularGridInterpolator）')
        for param, interp in self._interps.items():
            if interp is None:
                continue
            # 温度/电流轴外推用最近值（clip 到轴范围）
            ta = self._axes[0]
            ia = self._axes[1]
            tq = float(np.clip(t, ta.min(), ta.max()))
            iq = float(np.clip(i, ia.min(), ia.max()))
            val = float(interp([tq, iq, s])[0])
            out[param] = val
        return out

    def series(self, temperature_c, current_a, soc_pct) -> tuple:
        """向量化查询，返回 (r0_arr, r1_arr, tau_arr)，单位 mΩ/mΩ/s。"""
        q = [self.query(t, i, s) for t, i, s in zip(temperature_c, current_a, soc_pct)]
        return (
            np.array([x.get('r0_mohm', np.nan) for x in q]),
            np.array([x.get('r1_mohm', np.nan) for x in q]),
            np.array([x.get('tau_s', np.nan) for x in q]),
        )


def load_config(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_table(path_or_file, *, nrows: Optional[int] = None) -> pd.DataFrame:
    name = str(getattr(path_or_file, 'name', path_or_file)).lower()
    if name.endswith('.csv'):
        # Common Chinese BMS exports use GBK; prefer UTF-8 first.
        for encoding in ['utf-8-sig', 'gb18030']:
            try:
                if hasattr(path_or_file, 'seek'): path_or_file.seek(0)
                return pd.read_csv(path_or_file, encoding=encoding, nrows=nrows, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise ValueError('CSV编码无法识别，请另存为UTF-8或GB18030')
    if name.endswith(('.xlsx','.xlsm')):
        if hasattr(path_or_file, 'seek'): path_or_file.seek(0)
        return pd.read_excel(path_or_file, engine='openpyxl', nrows=nrows)
    if name.endswith('.parquet'):
        if hasattr(path_or_file, 'seek'): path_or_file.seek(0)
        df = pd.read_parquet(path_or_file)
        return df.iloc[:nrows] if nrows is not None else df
    raise ValueError('目前支持 CSV、XLSX、XLSM、Parquet；不直接解析CAN原始二进制日志')


def detect_columns(df: pd.DataFrame) -> dict:
    cols = list(df.columns)
    low = {str(c).lower().replace(' ','').replace('_',''):c for c in cols}
    def find(terms):
        for t in terms:
            key=t.replace('_','').lower()
            if key in low: return low[key]
        for c in cols:
            key=str(c).lower().replace(' ','').replace('_','')
            if any(t.replace('_','').lower() in key for t in terms): return c
        return None
    time=find(['time_s','time_sec','timestamp','时间','time'])
    current=find(['pack_current_a','packcurrent','总电流','电流','current'])
    temp=find(['temperature_c','cell_temp','最高温度','温度','temperature'])
    ref=find(['reference_soc_pct','reference_soc','真实soc','参考soc','refsoc'])
    bms=find(['bms_soc_pct','bms_soc','显示soc'])
    voltages=[c for c in cols if re.search(r'(cell|单体|单节|电芯|brick).*(v|电压|volt)',str(c),re.I)]
    if not voltages:
        voltages=[c for c in cols if re.search(r'(^voltage_v$|^cell_voltage$|单体电压)',str(c),re.I)]
    return {'time':time,'current':current,'temperature':temp,'reference':ref,'bms_soc':bms,'cells':voltages}


def prepare_data(df: pd.DataFrame, time_col, current_col, voltage_cols,
                 reference_col=None, *, invert_current=False, default_dt=1.0, time_unit="auto") -> tuple[pd.DataFrame,list[str]]:
    if not time_col or not current_col or not voltage_cols:
        raise ValueError('请映射时间、总电流和至少一个电芯电压列')
    required=[time_col,current_col]+list(voltage_cols)+([reference_col] if reference_col else [])
    subset=df.loc[:,list(dict.fromkeys(required))].copy()
    warnings=[]
    if pd.api.types.is_numeric_dtype(subset[time_col]):
        raw_t=pd.to_numeric(subset[time_col],errors='coerce')
        t=raw_t-raw_t.iloc[0]
        if len(t)>1:
            dmed=float(np.nanmedian(np.diff(t)))
            is_millis = (time_unit == 'ms' or
                         (time_unit == 'auto' and ('_ms' in str(time_col).lower() or
                          '毫秒' in str(time_col) or
                          (float(raw_t.iloc[0]) > 1.e11 and 20 < dmed < 100000))))
            if is_millis:
                warnings.append('已将毫秒时间戳换算为秒')
                t=t/1000.0
            elif time_unit == 'auto' and dmed > 50:
                warnings.append('时间列采样间隔大于50，可能采用毫秒单位；可在界面手动指定')
    else:
        dat=pd.to_datetime(subset[time_col],errors='coerce')
        if dat.notna().mean()>0.95:
            t=(dat-dat.iloc[0]).dt.total_seconds()
        else:
            t=pd.to_numeric(subset[time_col],errors='coerce')
            t=(t-t.iloc[0])*default_dt
            warnings.append('时间列未成功识别为时间戳，已按数字序号处理')
    out=pd.DataFrame({'time_s':t.to_numpy(dtype=float),
                      'current_a':pd.to_numeric(subset[current_col],errors='coerce').to_numpy(dtype=float)})
    if invert_current: out['current_a']=-out['current_a']
    if (out['current_a']<0).mean()>0.85:
        warnings.append('超过85%的总电流为负；若该段是放电，请勾选“放电为负”转换')
    if reference_col:
        out['reference_soc_pct']=pd.to_numeric(subset[reference_col],errors='coerce').to_numpy(dtype=float)
    for idx,c in enumerate(voltage_cols):
        out[f'cell_{idx+1:02d}_v']=pd.to_numeric(subset[c],errors='coerce').to_numpy(dtype=float)
    before=len(out)
    out=out.dropna(subset=['time_s','current_a']).reset_index(drop=True)
    if len(out)<before: warnings.append(f'{before-len(out)}行缺少时间或电流，被删除')
    if len(out)<3: raise ValueError('有效数据不足3行')
    dt=np.diff(out['time_s'].to_numpy())
    if np.any(dt<=0): raise ValueError('时间列必须严格递增；请先清理重复时间戳、倒序行或数据段拼接')
    median=float(np.median(dt))
    if median<0.05 or median>30: warnings.append(f'时间中位间隔{median:.3f}s，预期约1s，请确认时间单位')
    if np.max(dt)>max(5*median,10.0): warnings.append('检测到采样断点，积分采用真实时间间隔；断点期间的电流未知，结果可能失真')
    for c in [col for col in out if col.startswith('cell_')]:
        vals=out[c].dropna()
        if vals.empty: warnings.append(f'{c}电压全部缺失')
        elif vals.median()>10:
            out[c]=out[c]/1000
            warnings.append(f'{c}检测为mV，已换算为V')
        if len(vals) and (out[c].min()<1.5 or out[c].max()>5.0): warnings.append(f'{c}超出1.5～5.0V，建议检查映射或坏点')
    if 'reference_soc_pct' in out:
        ref=out['reference_soc_pct'].dropna()
        if len(ref) and ref.max()<=1.01 and ref.min()>=0.0:
            out['reference_soc_pct']*=100
            warnings.append('参考SOC按0～1小数值输入，已换算为百分比')
    return out,warnings


def read_ocv(path_or_file) -> tuple[np.ndarray,np.ndarray]:
    df=load_table(path_or_file)
    lower={str(c).lower():c for c in df.columns}
    soc_col=next((lower[k] for k in ['soc_pct','soc','soc_percent'] if k in lower),None)
    ocv_col=next((lower[k] for k in ['ocv_v','ocv','voltage_v'] if k in lower),None)
    if soc_col is None or ocv_col is None:
        raise ValueError('OCV表必须包含 soc_pct,ocv_v 两列')
    d=df[[soc_col,ocv_col]].copy().apply(pd.to_numeric,errors='coerce').dropna().sort_values(soc_col)
    x=d[soc_col].to_numpy(dtype=float)
    y=d[ocv_col].to_numpy(dtype=float)
    if x.max()<=1.01: x*=100
    if y.max()>10: y/=1000
    if len(x)<5 or len(np.unique(x))!=len(x) or x.min()>0 or x.max()<100:
        raise ValueError('OCV表需要至少5个不同SOC点，覆盖0%～100%')
    if np.any(np.diff(y)<-1e-5):
        raise ValueError('OCV表需随SOC单调非降，请检查数据或迟滞支路混用')
    return x/100.0,y*1000.0


def ocv_and_slope(soc: float, x:np.ndarray,y_mv:np.ndarray) -> tuple[float,float]:
    z=float(np.clip(soc,0,1)); ocv=float(np.interp(z,x,y_mv))
    # Piecewise interpolation slope, mV per SOC fraction.
    j=min(max(int(np.searchsorted(x,z,side='right'))-1,0),len(x)-2)
    slope=float((y_mv[j+1]-y_mv[j])/(x[j+1]-x[j]))
    return ocv,slope


def classify_modes(time_s:np.ndarray,current_a:np.ndarray,capacity_ah:float,cfg:dict)->list[str]:
    p=cfg['parameter_filter']
    step_threshold=max(p['step_min_a'],p['step_c_rate']*capacity_ah)
    rest_threshold=max(p['rest_current_a'],p['rest_c_rate']*capacity_ah)
    last_step=float('-inf'); rest_start=None; modes=[]
    for k,(t,I) in enumerate(zip(time_s,current_a)):
        if abs(I)<=rest_threshold:
            if rest_start is None:rest_start=float(t)
            mode='rest_late' if t-rest_start>=p['deep_rest_seconds'] else 'rest_early'
        else:
            rest_start=None
            if k and abs(I-current_a[k-1])>=step_threshold:
                last_step=float(t);mode='step'
            elif t-last_step <= p['polarization_window_s']:
                mode='polarization'
            else:
                mode='steady'
        modes.append(mode)
    return modes


def run_ah(time_s, I, capacity_ah, start_soc_pct):
    out=np.empty(len(time_s));out[0]=float(start_soc_pct)
    for k in range(1,len(time_s)):
        dt=time_s[k]-time_s[k-1]
        out[k]=np.clip(out[k-1]-I[k-1]*dt/(3600*capacity_ah)*100,0,100)
    return out


def run_1rc_ekf(time_s,I,V,capacity_ah,start_soc_pct,ocv_x,ocv_mv,cfg,
                r0_series=None,r1_series=None,tau_series=None,
                update_mask=None) -> dict:
    """一阶 RC EKF。

    update_mask: 可选布尔数组，长度 n。True 表示该时刻做测量更新（卡尔曼校正），
    False 表示跳过测量更新（仅预测推进，SOC 保持安时积分结果）。默认 None = 全部更新。
    典型用途：仅在放电段更新，充电/静置段不更新（避免迟滞 OCV 污染）。
    """
    n=len(time_s); p=cfg['parameter_filter'];f=cfg['soc_filter']
    r0=np.full(n,p['r0_mohm']) if r0_series is None else np.asarray(r0_series)
    r1=np.full(n,p['r1_mohm']) if r1_series is None else np.asarray(r1_series)
    tau=np.full(n,p['tau_s']) if tau_series is None else np.asarray(tau_series)
    mask=np.ones(n,dtype=bool) if update_mask is None else np.asarray(update_mask,dtype=bool)
    z=np.empty(n);u=np.empty(n);v_pred=np.empty(n);innovation=np.empty(n)
    z[0]=np.clip(start_soc_pct/100,0,1);u[0]=0.0
    P=np.diag([(f['initial_sigma_soc_pct']/100)**2,f['initial_sigma_polarization_mv']**2])
    Q=np.diag([(f['sigma_process_soc_pct_per_step']/100)**2,f['sigma_process_polarization_mv_per_step']**2])
    R=float(f['sigma_measurement_mv'])**2
    v_pred[0]=ocv_and_slope(z[0],ocv_x,ocv_mv)[0]-I[0]*r0[0]
    innovation[0]=V[0]*1000-v_pred[0] if np.isfinite(V[0]) else np.nan
    for k in range(1,n):
        dt=float(time_s[k]-time_s[k-1]);a=math.exp(-dt/max(float(tau[k]),1.0))
        zp=np.clip(z[k-1]-I[k-1]*dt/(capacity_ah*3600),0,1)
        up=a*u[k-1]+(1-a)*r1[k]*I[k-1]
        pp=np.array([[1,0],[0,a]])@P@np.array([[1,0],[0,a]]).T+Q*max(dt,1)
        ov,slope=ocv_and_slope(zp,ocv_x,ocv_mv)
        vp=ov-I[k]*r0[k]-up;v_pred[k]=vp
        # 跳过测量更新：电压无效，或 update_mask 标记为 False（充电/静置段）
        if not np.isfinite(V[k]) or not mask[k]:
            z[k]=zp;u[k]=up;innovation[k]=np.nan;P=pp;continue
        innov=float(V[k]*1000-vp);innovation[k]=innov
        H=np.array([slope,-1.0]);denom=float(H@pp@H+R)
        K=(pp@H)/max(denom,1e-12)
        x=np.array([zp,up])+K*innov
        z[k]=float(np.clip(x[0],0,1));u[k]=float(np.clip(x[1],-500,500))
        A=np.eye(2)-np.outer(K,H)
        P=A@pp@A.T+np.outer(K,K)*R
        P=(P+P.T)*0.5
    return {'soc_pct':z*100,'polarization_mv':u,'predicted_voltage_v':v_pred/1000,'innovation_mv':innovation}


def run_1rc_ekf_3d(time_s, I, V, temperature_c, capacity_ah, start_soc_pct,
                   ocv_x, ocv_mv, cfg, impedance3d: Impedance3DLookup) -> dict:
    """一阶 RC EKF，R0/R1/tau 逐时刻从三维表（温度×电流×SOC）查表。

    与 run_1rc_ekf 同构，仅 r0/r1/tau 来源不同：按当前 SOC 估计值 + 电流幅值 +
    恒定温度查表插值，替代固定参数或逐秒序列。
    """
    n = len(time_s)
    p = cfg['parameter_filter']
    f = cfg['soc_filter']

    # 温度序列：若给标量则广播
    if np.isscalar(temperature_c):
        T = np.full(n, float(temperature_c))
    else:
        T = np.asarray(temperature_c, dtype=float)

    z = np.empty(n)
    u = np.empty(n)
    v_pred = np.empty(n)
    innovation = np.empty(n)
    r0_hist = np.empty(n)
    r1_hist = np.empty(n)
    tau_hist = np.empty(n)

    z[0] = np.clip(start_soc_pct / 100, 0, 1)
    u[0] = 0.0
    P = np.diag([(f['initial_sigma_soc_pct'] / 100) ** 2, f['initial_sigma_polarization_mv'] ** 2])
    Q = np.diag([(f['sigma_process_soc_pct_per_step'] / 100) ** 2, f['sigma_process_polarization_mv_per_step'] ** 2])
    R = float(f['sigma_measurement_mv']) ** 2

    # 初始查表
    q0 = impedance3d.query(T[0], I[0], z[0] * 100)
    r0_hist[0] = q0.get('r0_mohm', p['r0_mohm'])
    r1_hist[0] = q0.get('r1_mohm', p['r1_mohm'])
    tau_hist[0] = q0.get('tau_s', p['tau_s'])
    v_pred[0] = ocv_and_slope(z[0], ocv_x, ocv_mv)[0] - I[0] * r0_hist[0]
    innovation[0] = V[0] * 1000 - v_pred[0] if np.isfinite(V[0]) else np.nan

    for k in range(1, n):
        dt = float(time_s[k] - time_s[k - 1])
        # 先按上一时刻 SOC 查表（预测步用上一时刻参数，保证因果性）
        qp = impedance3d.query(T[k], I[k - 1], z[k - 1] * 100)
        r0k = float(qp.get('r0_mohm', p['r0_mohm']))
        r1k = float(qp.get('r1_mohm', p['r1_mohm']))
        tauk = float(qp.get('tau_s', p['tau_s']))
        r0_hist[k] = r0k
        r1_hist[k] = r1k
        tau_hist[k] = tauk

        a = math.exp(-dt / max(tauk, 1.0))
        zp = np.clip(z[k - 1] - I[k - 1] * dt / (capacity_ah * 3600.0), 0, 1)
        up = a * u[k - 1] + (1 - a) * r1k * I[k - 1]
        pp = np.array([[1, 0], [0, a]]) @ P @ np.array([[1, 0], [0, a]]).T + Q * max(dt, 1)
        ov, slope = ocv_and_slope(zp, ocv_x, ocv_mv)
        vp = ov - I[k] * r0k - up
        v_pred[k] = vp

        if not np.isfinite(V[k]):
            z[k] = zp
            u[k] = up
            innovation[k] = np.nan
            P = pp
            continue

        innov = float(V[k] * 1000 - vp)
        innovation[k] = innov
        H = np.array([slope, -1.0])
        denom = float(H @ pp @ H + R)
        K = (pp @ H) / max(denom, 1e-12)
        x = np.array([zp, up]) + K * innov
        z[k] = float(np.clip(x[0], 0, 1))
        u[k] = float(np.clip(x[1], -500, 500))
        A = np.eye(2) - np.outer(K, H)
        P = A @ pp @ A.T + np.outer(K, K) * R
        P = (P + P.T) * 0.5

    return {
        'soc_pct': z * 100,
        'polarization_mv': u,
        'predicted_voltage_v': v_pred / 1000,
        'innovation_mv': innovation,
        'r0_mohm': r0_hist,
        'r1_mohm': r1_hist,
        'tau_s': tau_hist,
    }


def run_dual_ekf_1rc(time_s, I, V, temperature_c, capacity_ah, start_soc_pct,
                      ocv_x, ocv_mv, cfg, impedance3d: Impedance3DLookup) -> dict:
    """双扩展卡尔曼滤波（DEKF）：一阶 RC 模型，参数在线辨识（分时解耦门控）。

    结构（两个滤波器交替）：
      1. 参数 EKF —— 状态 θ = [R0, R1, τ]，在线辨识阻抗参数。
         初值从三维表 impedance3d 按 (温度, 电流幅值, 当前SOC) 查表得到，
         「三维表只作为本次启动时刻的初值」，之后由参数 EKF 在线跟踪。
      2. 状态 EKF —— 状态 x = [SOC, U1(RC极化电压)]，用参数 EKF 传来的最新 θ 估 SOC。

    可辨识性门控（核心）：
      R0/R1/τ 共享单电压观测，同时辨识必然互相污染（漂移发散）。故分时解耦：
        - R0 只在「电流跳变」(dI_abs 大) 时更新 —— 瞬时压降 ΔV/ΔI 才是 R0 信息。
        - R1/τ 只在「静置/极化恢复」(电流小) 时更新 —— 无电流时电压变化才是极化信息。
        - 稳态放电段参数冻结，只让状态 EKF 更新 SOC。
    门控阈值由 cfg['parameter_filter'] 提供（step_min_a 决定 R0 门控，rest_current_a 决定 R1/τ 门控）。

    OCV 不做在线辨识（用查表），避免多参数共享单观测导致的可辨识性缺陷。
    观测方程：V = OCV(SOC) - I·R0 - U1，U1 = a·U1_prev + (1-a)·R1·I_prev。
    """
    n = len(time_s)
    f = cfg['soc_filter']
    pf = cfg.get('parameter_filter', {})

    # 门控阈值
    step_thr = float(pf.get('step_min_a', 5.0))          # R0 更新的电流跳变阈值 (A)
    rest_thr = float(pf.get('rest_current_a', 1.0))       # R1/τ 更新的静置电流阈值 (A)
    r0_gate_scale = float(pf.get('step_c_rate', 0.1)) * capacity_ah
    rest_gate_scale = float(pf.get('rest_c_rate', 0.01)) * capacity_ah
    step_thr = max(step_thr, r0_gate_scale)
    rest_thr = max(rest_thr, rest_gate_scale)

    # 参数 EKF 协方差/噪声
    pR = float(pf.get('sigma_measurement_mv', 7.0)) ** 2
    p_init = pf.get('initial_sigma', {})
    p_sigma_r0 = float(p_init.get('r0_mohm', 3.0))
    p_sigma_r1 = float(p_init.get('r1_mohm', 3.0))
    p_sigma_tau = float(p_init.get('tau_s', 20.0))
    d = pf.get('sigma_change_over_100s', {})
    q_r0 = (float(d.get('r0_mohm', 0.05)) ** 2) / 100.
    q_r1 = (float(d.get('r1_mohm', 0.05)) ** 2) / 100.
    q_tau = (float(d.get('tau_s', 2.0)) ** 2) / 100.
    # 参数范围（物理约束）
    p_lo = np.array([0.5, 0.0, 2.0])
    p_hi = np.array([float(pf.get('r0_max_mohm', 30.0)),
                     float(pf.get('r1_max_mohm', 40.0)), 300.0])

    # 状态 EKF 协方差/噪声
    sR = float(f['sigma_measurement_mv']) ** 2
    Px = np.diag([(f['initial_sigma_soc_pct'] / 100) ** 2, f['initial_sigma_polarization_mv'] ** 2])
    Qx = np.diag([(f['sigma_process_soc_pct_per_step'] / 100) ** 2, f['sigma_process_polarization_mv_per_step'] ** 2])

    # 温度序列
    if np.isscalar(temperature_c):
        T = np.full(n, float(temperature_c))
    else:
        T = np.asarray(temperature_c, dtype=float)

    # 初值：三维表查表（启动时刻初值）
    z0 = np.clip(start_soc_pct / 100, 0, 1)
    init = impedance3d.query(T[0], I[0], z0 * 100)
    r0 = float(init.get('r0_mohm', pf.get('r0_mohm', 8.0)))
    r1 = float(init.get('r1_mohm', pf.get('r1_mohm', 8.0)))
    tau = float(init.get('tau_s', pf.get('tau_s', 30.0)))
    r1 = max(r1, 0.1)
    tau = max(tau, 2.0)

    theta = np.array([r0, r1, tau], dtype=float)
    Ptheta = np.diag([p_sigma_r0 ** 2, p_sigma_r1 ** 2, p_sigma_tau ** 2])

    # 状态 EKF 初值
    z = z0
    U1 = 0.0

    # 输出缓存
    soc_hist = np.empty(n)
    u1_hist = np.empty(n)
    r0_hist = np.empty(n)
    r1_hist = np.empty(n)
    tau_hist = np.empty(n)
    v_pred = np.empty(n)
    innovation = np.empty(n)
    gate_hist = np.empty(n, dtype=object)  # 记录每步门控

    soc_hist[0] = z * 100
    u1_hist[0] = U1
    r0_hist[0] = r0
    r1_hist[0] = r1
    tau_hist[0] = tau
    gate_hist[0] = 'init'
    ov0, _ = ocv_and_slope(z, ocv_x, ocv_mv)
    v_pred[0] = ov0 - I[0] * r0
    innovation[0] = V[0] * 1000 - v_pred[0] if np.isfinite(V[0]) else np.nan

    for k in range(1, n):
        dt = float(time_s[k] - time_s[k - 1])
        dt = max(dt, 1e-6)
        r0, r1, tau = theta
        tau_c = max(tau, 2.0)
        a = math.exp(-dt / tau_c)
        prev_i = float(I[k - 1])
        dI = float(I[k] - I[k - 1])  # 电流导数

        # ---------- 1. 状态 EKF 预测 ----------
        zp = np.clip(z - prev_i * dt / (capacity_ah * 3600.0), 0, 1)
        up = a * U1 + (1 - a) * r1 * prev_i
        Pxp = np.array([[1., 0.], [0., a]]) @ Px @ np.array([[1., 0.], [0., a]]).T + Qx * dt

        # ---------- 2. 参数 EKF 预测（随机游走，但门控冻结时会减弱） ----------
        Ptheta_p = Ptheta + np.diag([q_r0, q_r1, q_tau]) * dt

        # ---------- 3. 观测预测 ----------
        ov, slope = ocv_and_slope(zp, ocv_x, ocv_mv)
        vp = ov - I[k] * r0 - up
        v_pred[k] = vp

        # 观测雅可比
        Hx = np.array([slope, -1.0])
        dU1_dtau = (dt / tau_c ** 2) * a * (U1 - r1 * prev_i)
        Ht = np.array([-float(I[k]), -(1 - a) * prev_i, -dU1_dtau])

        # ---------- 可辨识性门控 ----------
        gate_r0 = (abs(dI) >= step_thr)      # 电流跳变 → R0 可辨识
        gate_r1tau = (abs(I[k]) <= rest_thr)  # 静置/极化恢复 → R1/τ 可辨识

        if np.isfinite(V[k]):
            innov = float(V[k] * 1000 - vp)
            innovation[k] = innov

            # ---------- 4. 状态 EKF 更新（始终进行） ----------
            sx = float(Hx @ Pxp @ Hx + sR)
            if sx > 1e-15:
                Kx = (Pxp @ Hx) / sx
                dx = Kx * innov
                z = float(np.clip(zp + dx[0], 0, 1))
                U1 = float(np.clip(up + dx[1], -500, 500))
                Px = (np.eye(2) - np.outer(Kx, Hx)) @ Pxp
                Px = 0.5 * (Px + Px.T)
            else:
                z, U1, Px = zp, up, Pxp

            # ---------- 5. 参数 EKF 更新（按门控解耦） ----------
            allowed = np.array([gate_r0, gate_r1tau, gate_r1tau], dtype=bool)
            if allowed.any() and abs(innov) <= pf.get('max_voltage_innovation_mv', 150.):
                st = float(Ht @ Ptheta_p @ Ht + pR)
                if st > 1e-15:
                    Kt = (Ptheta_p @ Ht) / st
                    Kt[~allowed] = 0.0  # 冻结不可辨识的参数
                    theta = np.clip(theta + Kt * innov, p_lo, p_hi)
                    Ptheta = (np.eye(3) - np.outer(Kt, Ht)) @ Ptheta_p
                    Ptheta = 0.5 * (Ptheta + Ptheta.T)
                    gate_hist[k] = 'r0' if gate_r0 else ('r1tau' if gate_r1tau else 'freeze')
                else:
                    Ptheta = Ptheta_p
                    gate_hist[k] = 'freeze'
            else:
                Ptheta = Ptheta_p
                gate_hist[k] = 'freeze'
        else:
            innovation[k] = np.nan
            z, U1, Px = zp, up, Pxp
            Ptheta = Ptheta_p
            gate_hist[k] = 'freeze'

        soc_hist[k] = z * 100
        u1_hist[k] = U1
        r0_hist[k] = theta[0]
        r1_hist[k] = theta[1]
        tau_hist[k] = theta[2]

    return {
        'soc_pct': soc_hist,
        'polarization_mv': u1_hist,
        'predicted_voltage_v': v_pred / 1000,
        'innovation_mv': innovation,
        'r0_mohm': r0_hist,
        'r1_mohm': r1_hist,
        'tau_s': tau_hist,
        'param_gate': gate_hist,
    }


def run_parameter_ekf(time_s, I, V, ocv_init_mv, capacity_ah, cfg) -> dict:
    """Five-state EKF: four slowly changing parameters + RC polarization state.

    State is [OCV_mV, R0_mOhm, R1_mOhm, tau_s, Up_mV].
    Only OCV/R0/R1/tau are published as online identified parameters.
    OCV is not used to replace the validated offline OCV-vs-SOC lookup.

    This is a research prototype, not a certified estimator. Measurement updates
    are selectively gated by the driving regime. Because the four parameter
    variables share a single voltage observation, identifiability remains limited.
    """
    n = len(time_s)
    p = cfg['parameter_filter']
    modes = classify_modes(time_s, I, capacity_ah, cfg)
    x = np.array([ocv_init_mv, p['r0_mohm'], p['r1_mohm'], p['tau_s'], 0.], dtype=float)
    sig = p['initial_sigma']
    P = np.diag([sig['ocv_mv']**2, sig['r0_mohm']**2,
                 sig['r1_mohm']**2, sig['tau_s']**2,
                 p.get('initial_sigma_polarization_mv', 15.)**2])
    d = p['sigma_change_over_100s']
    qbase = np.array([d['ocv_mv']**2, d['r0_mohm']**2,
                      d['r1_mohm']**2, d['tau_s']**2]) / 100.
    q_polar = p.get('sigma_process_polarization_mv_per_step', 0.8)**2
    R = float(p['sigma_measurement_mv'])**2
    limits_lo = np.array([2300., 0.01, 0.01, 2., -500.])
    limits_hi = np.array([4300., p.get('r0_max_mohm',20.), p.get('r1_max_mohm',20.), 1000., 500.])
    estimates = np.empty((n, 5))
    innovations = np.full(n, np.nan)
    pred_v = np.full(n, np.nan)
    accepted = np.zeros(n, dtype=bool)
    H = np.array([1., 0., 0., 0., -1.])
    for k in range(n):
        dt = float(time_s[k] - time_s[k-1]) if k else float(cfg.get('sampling_seconds', 1.))
        dt = max(dt, 1e-6)
        prev_i = float(I[k-1]) if k else float(I[0])
        prev_x = x.copy()
        tau = max(prev_x[3], 2.)
        a = math.exp(-dt / tau)
        x[4] = a * prev_x[4] + (1. - a) * prev_i * prev_x[2]
        F = np.eye(5)
        F[4, 4] = a
        F[4, 2] = (1. - a) * prev_i
        F[4, 3] = a * dt / tau**2 * (prev_x[4] - prev_i * prev_x[2])
        multipliers = np.asarray(p['mode_q_multipliers'][modes[k]], dtype=float)
        step_q = np.r_[qbase*multipliers, q_polar] * dt
        P = F @ P @ F.T + np.diag(step_q)
        H[1] = -float(I[k])
        v_est = x[0] - float(I[k])*x[1] - x[4]
        pred_v[k] = v_est / 1000.
        if np.isfinite(V[k]):
            residual = float(V[k])*1000. - v_est
            innovations[k] = residual
            if abs(residual) <= p.get('max_voltage_innovation_mv', 150.):
                s = float(H @ P @ H + R)
                if s > 1.e-15 and np.isfinite(s):
                    K = (P @ H) / s
                    allowed = np.array(p['mode_update_mask'][modes[k]], dtype=bool)
                    # Keep polarization dynamics filterable except in late rest;
                    # late rest prioritizes a near-equilibrium OCV observation.
                    allowed = np.r_[allowed, modes[k] != 'rest_late']
                    K[~allowed] = 0.
                    x = np.clip(x + K * residual, limits_lo, limits_hi)
                    J = np.eye(5) - np.outer(K, H)
                    P = J @ P @ J.T + R*np.outer(K, K)
                    accepted[k] = bool(np.any(allowed))
        P = 0.5*(P + P.T)
        estimates[k] = x
        if not np.all(np.isfinite(x)) or not np.all(np.isfinite(P)):
            raise FloatingPointError(f'参数滤波数值异常，样本序号 {k}，已停止计算')
    return {
        'online_ocv_v': estimates[:, 0]/1000.,
        'r0_mohm': estimates[:, 1],
        'r1_mohm': estimates[:, 2],
        'tau_s': estimates[:, 3],
        'param_polarization_mv': estimates[:, 4],
        'param_predicted_voltage_v': pred_v,
        'param_innovation_mv': innovations,
        'param_update_accepted': accepted,
        'mode': modes,
    }

def error_metrics(estimate,reference):
    valid=np.isfinite(estimate)&np.isfinite(reference)
    if not np.any(valid): return None
    e=estimate[valid]-reference[valid]
    return {'n_comparable':int(valid.sum()),'rmse_pct':float(np.sqrt(np.mean(e*e))),
            'mae_pct':float(np.mean(abs(e))),'max_abs_error_pct':float(np.max(abs(e))),
            'bias_pct':float(np.mean(e)),'last_error_pct':float(e[-1])}


def run_analysis(df, *, cells, cfg, ocv_table=None, reference_col='reference_soc_pct',
                 start_soc_pct=None, algorithms=('ah','single','dual')):
    t=df['time_s'].to_numpy(dtype=float)
    current=df['current_a'].to_numpy(dtype=float)
    capacity=float(cfg['nominal_capacity_ah'])
    if capacity<=0:raise ValueError('标称容量必须大于0')
    start=float(start_soc_pct if start_soc_pct is not None else cfg['start_soc_pct'])
    ah=run_ah(t,current,capacity,start)
    output=pd.DataFrame({'time_s':t,'current_a':current,'ah_soc_pct':ah})
    if reference_col and reference_col in df:output['reference_soc_pct']=df[reference_col].to_numpy(dtype=float)
    metrics=[]
    for cell in cells:
        if cell not in df:raise ValueError(f'缺少电芯电压列: {cell}')
        V=df[cell].to_numpy(dtype=float)
        prefix=cell
        output[f'{prefix}_measured_v']=V
        res={}
        if ocv_table is not None and ('single' in algorithms or 'dual' in algorithms):
            ox,oy=ocv_table
            if 'single' in algorithms:
                single=run_1rc_ekf(t,current,V,capacity,start,ox,oy,cfg)
                for key,value in single.items():output[f'{prefix}_single_{key}']=value
                res['single_ekf']=single['soc_pct']
            if 'dual' in algorithms:
                online=run_parameter_ekf(t,current,V,ocv_and_slope(start/100,ox,oy)[0],capacity,cfg)
                for key,value in online.items():output[f'{prefix}_{key}']=value
                dual=run_1rc_ekf(t,current,V,capacity,start,ox,oy,cfg,
                     online['r0_mohm'],online['r1_mohm'],online['tau_s'])
                for key,value in dual.items():output[f'{prefix}_dual_{key}']=value
                res['dual_ekf_prototype']=dual['soc_pct']
        res['ah']=ah
        if 'reference_soc_pct' in output:
            ref=output['reference_soc_pct'].to_numpy(dtype=float)
            for alg,estimated in res.items():
                m=error_metrics(np.asarray(estimated),ref)
                if m:metrics.append({'cell':cell,'algorithm':alg,**m})
    return output,pd.DataFrame(metrics)


def make_two_anchor_reference(time_s, current_a, start_soc_pct: float,
                              end_soc_pct: float, end_index: int | None = None) -> tuple[np.ndarray, float]:
    """Reconstruct approximate SOC from *user-supplied* start/end anchors.

    For use only when start/end have independent physical evidence. The result
    is a Coulomb-counted reference proxy, NOT a measured SOC ground truth.
    Assumes discharge-positive current and constant usable capacity.
    """
    t = np.asarray(time_s, dtype=float)
    I = np.asarray(current_a, dtype=float)
    if len(t) != len(I) or len(t) < 3:
        raise ValueError('两锚点重构至少需要3行对齐的时间与电流数据')
    if not (0 <= end_soc_pct < start_soc_pct <= 100):
        raise ValueError('锚点必须满足 0 <= 终点SOC < 起点SOC <= 100')
    if end_index is None:
        end_index = len(t)-1
    if end_index < 2 or end_index >= len(t):
        raise ValueError('终点索引超出数据范围')
    increments = I[:-1] * np.diff(t) / 3600.
    dq = np.r_[0., np.cumsum(increments)]
    used = float(dq[end_index])
    if used <= 0:
        raise ValueError('这段数据累计净放电容量必须为正。请检查电流正负号或锚点选择')
    capacity = used / ((start_soc_pct-end_soc_pct)/100.)
    z = start_soc_pct - dq/capacity*100.
    z[end_index+1:] = np.nan
    return z, capacity
