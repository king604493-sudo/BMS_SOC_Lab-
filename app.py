"""Mac-local, offline Streamlit front end for BMS_SOC_Lab."""
from __future__ import annotations

import copy
import io
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import streamlit as st

from bmslab.engine import (
    load_config, load_table, detect_columns, prepare_data,
    read_ocv, run_analysis, make_two_anchor_reference,
)
from bmslab.reports import save_report

ROOT = Path(__file__).resolve().parent
SUPPORTED = {'.csv', '.xlsx', '.xlsm', '.parquet'}
st.set_page_config(page_title='BMS SOC 离线仿真实验室', page_icon='🔋', layout='wide')
st.title('🔋 BMS SOC 离线仿真实验室')
st.caption('Mac 本地运行｜1 秒级整车数据｜一阶RC＋安时积分＋单EKF＋双EKF研究原型')
st.info('所有文件在你的电脑本地读取和处理；本软件不需要将整车原始数据上传到 ChatGPT。\n'
        '量产前仍须验证数值稳定性、故障边界及真实 SOC 基准。')


def guess_index(options, name, fallback=0):
    if name in options:
        return options.index(name)
    return min(fallback, len(options)-1)


def file_candidates(folder: Path):
    if not folder.exists() or not folder.is_dir():
        return []
    try:
        first = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED),
                       key=lambda p: p.name.lower())
        return first[:200]
    except (PermissionError, OSError):
        return []


def upload_or_path(label, key, *, default_path=''):
    method = st.radio(label, ['选择本地文件路径', '从浏览器选择文件'], horizontal=True, key=key+'_method')
    if method == '从浏览器选择文件':
        uploaded = st.file_uploader('选择 CSV / XLSX / Parquet',
                                    type=['csv', 'xlsx', 'xlsm', 'parquet'], key=key+'_upload')
        return uploaded
    path = st.text_input('完整路径', value=default_path, key=key+'_path')
    if not path.strip():
        return None
    p = Path(path.strip()).expanduser()
    if p.is_dir():
        candidates = file_candidates(p)
        if not candidates:
            st.warning('该目录没有直接包含可识别的数据文件；请填写文件完整路径。')
            return None
        sel = st.selectbox('选择文件', candidates, format_func=lambda x: x.name, key=key+'_select')
        return sel
    if p.is_file():
        return p
    st.warning('文件路径不存在。Mac 移动硬盘通常位于 /Volumes/硬盘名称/')
    return None


def source_name(src):
    if isinstance(src, Path):
        return str(src)
    return getattr(src, 'name', str(src))


with st.sidebar:
    st.header('① 导入本地 BMS 数据')
    drives = []
    volume_root = Path('/Volumes')
    if volume_root.exists():
        drives = sorted([p for p in volume_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    if drives:
        with st.expander('已识别的 Mac 磁盘', expanded=False):
            for d in drives:
                st.code(str(d), language=None)
    input_source = upload_or_path('原始数据来源', 'data', default_path=str(ROOT/'demo'))
    max_rows = st.number_input('最大读取行数（开发期先取少量）', min_value=1000,
                               max_value=1_000_000, value=150_000, step=1000)

if input_source is None:
    st.markdown('### 开始使用')
    st.write('先在左侧选择文件。项目自带 `demo/synthetic_vehicle_1Hz.csv` 和模拟 OCV 曲线，'
             '可以不连接移动硬盘先测试完整流程。')
    st.stop()

try:
    raw = load_table(input_source, nrows=int(max_rows))
except Exception as exc:
    st.error(f'读取失败：{exc}')
    st.stop()
if raw.empty:
    st.error('文件为空')
    st.stop()

auto = detect_columns(raw)
cols = list(raw.columns)
st.success(f'已读取：{source_name(input_source)}，{len(raw):,} 行，{len(cols)} 列')
with st.expander('原始数据预览（前20行）', expanded=False):
    st.dataframe(raw.head(20), use_container_width=True)

st.subheader('② 映射列并配置数据')
c1, c2, c3 = st.columns(3)
with c1:
    time_col = st.selectbox('时间列', cols, index=guess_index(cols,auto['time']))
    current_col = st.selectbox('总电流列', cols, index=guess_index(cols,auto['current']))
with c2:
    guess_cells = [c for c in auto['cells'] if c in cols]
    cell_cols = st.multiselect('电芯电压列（建议先选1～2节）', cols,
                                default=guess_cells[:2], max_selections=16)
    invert = st.checkbox('原始电流放电为负：转换成放电为正', value=False)
    time_unit = st.selectbox('数字时间列的单位', ['自动','秒','毫秒'],index=0)
with c3:
    ref_mode = st.selectbox('对比SOC来源', ['不设参考', '独立参考SOC列', '原车BMS显示SOC（仅一致性）',
                                        '两端锚点推算（近似参考）'])
    chosen_ref = None
    if ref_mode == '独立参考SOC列':
        chosen_ref = st.selectbox('选择独立SOC参考列', cols,
                                  index=guess_index(cols, auto['reference']))
    elif ref_mode == '原车BMS显示SOC（仅一致性）':
        chosen_ref = st.selectbox('选择BMS显示SOC列', cols,
                                  index=guess_index(cols,auto['bms_soc']))

cfg = copy.deepcopy(load_config())
pref = cfg['parameter_filter']
st.subheader('③ SOC 算法与 OCV 曲线')
a,b,c = st.columns(3)
with a:
    capacity = st.number_input('估计所用容量（Ah）', min_value=0.1,
                               max_value=2000., value=float(cfg['nominal_capacity_ah']),step=1.)
    start_soc = st.number_input('算法初始SOC（%）',min_value=0.,max_value=100.,
                                value=float(cfg['start_soc_pct']),step=1.)
with b:
    st.write('双KF的在线OCV仅作观测记录，不直接进入SOC查表。')
    soc_sigma = st.number_input('SOC观测等效噪声标准差（mV）',min_value=0.1,
                                 value=float(cfg['soc_filter']['sigma_measurement_mv']),step=0.5)
with c:
    ocv_default = str(ROOT/'demo'/'synthetic_ocv_table.csv') if 'synthetic_vehicle' in source_name(input_source) else ''
    ocv_source = upload_or_path('离线OCV–SOC表', 'ocv', default_path=ocv_default)
    st.caption('真实数据必须用匹配化学体系、温度和充放电方向的标定OCV表。')

if ref_mode == '两端锚点推算（近似参考）':
    st.warning('这类参考SOC由同一段电流积分生成，并非独立真值。用它计算RMSE只衡量与两端锚点假设的一致性。')
    a1, a2 = st.columns(2)
    with a1: anchor_start = st.number_input('起点SOC锚点（%）',0.,100.,100.,1.)
    with a2: anchor_end = st.number_input('数据终点SOC锚点（%）',0.,100.,15.,1.)

with st.expander('高级选项：五工况Q倍数标定',expanded=False):
    st.write('Q基本方差由 config.json 的“100秒参数标准差”推算；本表修改的是倍数。'
             '数值是研究初值，不是已验证的最优参数。')
    labels = {'step':'电流阶跃','polarization':'阶跃后极化',
              'steady':'稳定电流/低激励','rest_early':'刚进入静置',
              'rest_late':'充分静置'}
    table = pd.DataFrame([{'工况':labels[k], '模式':k,
                           'OCV':pref['mode_q_multipliers'][k][0],
                           'R0':pref['mode_q_multipliers'][k][1],
                           'R1':pref['mode_q_multipliers'][k][2],
                           'tau':pref['mode_q_multipliers'][k][3]}
                         for k in labels])
    edited = st.data_editor(table, hide_index=True, use_container_width=True,
                            disabled=['工况','模式'], key='q_editor')
    st.caption('如果要修改P₀、R以及参数物理范围，可直接编辑项目文件 config.json。')

run = st.button('运行离线仿真并生成报告',type='primary',use_container_width=True)
if run:
    if not cell_cols:
        st.error('请至少选择一列电芯电压')
        st.stop()
    if ocv_source is None:
        st.error('需要离线OCV–SOC曲线才能运行EKF；请先选择匹配电芯的OCV表。')
        st.stop()
    try:
        input_df, warnings = prepare_data(raw,time_col,current_col,cell_cols,
                                           reference_col=chosen_ref,
                                           invert_current=invert,
                                           time_unit={'自动':'auto','秒':'s','毫秒':'ms'}[time_unit])
        if ref_mode == '两端锚点推算（近似参考）':
            endpoint, cap_endpoint = make_two_anchor_reference(
                input_df['time_s'].to_numpy(),input_df['current_a'].to_numpy(),
                anchor_start,anchor_end)
            input_df['reference_soc_pct'] = endpoint
            warnings.append(f'两端锚点隐含可用容量为 {cap_endpoint:.3f}Ah；参考SOC是近似代理值。')
        ox,oy=read_ocv(ocv_source)
        for row in edited.to_dict('records'):
            vals=[float(row[k]) for k in ['OCV','R0','R1','tau']]
            if any(not np.isfinite(v) or v<0 for v in vals):
                raise ValueError('Q倍数必须是非负有限数')
            pref['mode_q_multipliers'][row['模式']]=vals
        cfg['nominal_capacity_ah']=float(capacity)
        cfg['start_soc_pct']=float(start_soc)
        cfg['soc_filter']['sigma_measurement_mv']=float(soc_sigma)
        chosen_cells=[f'cell_{i+1:02d}_v' for i in range(len(cell_cols))]
        with st.spinner('逐秒运行一阶RC、参数EKF和SOC-EKF……'):
            result,metrics=run_analysis(input_df,cells=chosen_cells,cfg=cfg,ocv_table=(ox,oy))
        output_dir=ROOT/'outputs'/'latest_run'
        report_path=save_report(result,metrics,cells=chosen_cells,output_dir=output_dir,
                 context={'data_source':source_name(input_source),'ocv_table':source_name(ocv_source),
                          'source_type':ref_mode,'warnings':warnings,'settings':cfg,
                          'cell_mapping':cell_cols})
        st.session_state['result']=(result,metrics,chosen_cells,report_path,warnings,ref_mode)
    except Exception as exc:
        st.error(f'无法完成仿真：{type(exc).__name__}: {exc}')

if 'result' in st.session_state:
    result,metrics,cell_names,report_path,warnings,ref_type=st.session_state['result']
    st.divider()
    st.subheader('④ SOC对比及诊断')
    st.caption('参考来源：'+ref_type+'；RMSE单位为SOC百分点（pp）。')
    if warnings:
        for w in warnings:
            st.warning(w)
    if metrics.empty:
        st.info('未提供参考SOC，算法运行正常，但不计算RMSE/MAE。')
    else:
        xx = metrics.pivot(index='cell', columns='algorithm', values='rmse_pct')
        if 'dual_ekf_prototype' in xx and 'single_ekf' in xx and (xx['dual_ekf_prototype']>xx['single_ekf']*2).any():
            st.error('本次双KF研究原型的RMSE明显高于单EKF：当前参数辨识可能失配，请先查看内阻与OCV曲线，勿直接采用。')
        st.dataframe(metrics.round(4),use_container_width=True,hide_index=True)
    cell=st.selectbox('查看电芯',cell_names,format_func=lambda x: x.replace('cell_','Cell '),key='view_cell')
    view=pd.DataFrame({'time_h':result['time_s']/3600,
                       '安时积分':result['ah_soc_pct'],
                       '一阶RC SOC-EKF':result[f'{cell}_single_soc_pct'],
                       '双EKF研究原型':result[f'{cell}_dual_soc_pct']})
    if 'reference_soc_pct' in result:
        view['参考SOC（注意来源）']=result['reference_soc_pct']
    stride=max(1,len(view)//2500)
    st.line_chart(view.iloc[::stride].set_index('time_h'),height=360)
    diag=pd.DataFrame({'time_h':result['time_s']/3600,
                       '在线OCV (V)':result[f'{cell}_online_ocv_v'],
                       '实测端电压 (V)':result[f'{cell}_measured_v']})
    st.line_chart(diag.iloc[::stride].set_index('time_h'),height=250)
    par=pd.DataFrame({'time_h':result['time_s']/3600,
                      'R0 (mΩ)':result[f'{cell}_r0_mohm'],
                      'R1 (mΩ)':result[f'{cell}_r1_mohm']})
    st.line_chart(par.iloc[::stride].set_index('time_h'),height=250)
    st.caption(f'报告已保存到：{report_path}（原始数据没有复制到报告目录）')
    with st.expander('检查算法失效风险',expanded=True):
        st.write('⚠️ 在线OCV和参数是研究性输出。平坦工况下，参数滤波可能把OCV误差误分配给R0/R1，'
                 '双EKF可能明显差于单EKF。应通过新旧车、不同温度、电流工况进行独立交叉验证。')
    outzip=io.BytesIO()
    with zipfile.ZipFile(outzip,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for fp in report_path.glob('*'):
            if fp.is_file():archive.write(fp,arcname=fp.name)
    st.download_button('下载本次报告（ZIP）',outzip.getvalue(),file_name='BMS_SOC_本地仿真报告.zip',
                       mime='application/zip')
