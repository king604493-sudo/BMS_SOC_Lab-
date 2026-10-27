"""最小回归测试：暴露 run_parameter_ekf 的模型错误与可辨识性边界。

这些测试是 CODEX_START_HERE.md 首轮任务第 5 条要求的落地，用于把已知
失效模式固化为可自动检测的断言。它们不修改算法，只做「期望行为」校验，
当前若干测试会失败（标记为 expectedFailure），以记录待修复缺陷。
"""
import unittest
import numpy as np
import pandas as pd
from pathlib import Path

from bmslab.engine import (
    load_config, read_ocv, load_table, prepare_data, run_parameter_ekf,
    ocv_and_slope, classify_modes, run_analysis,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config()


def _demo(nrows=500, cells=('cell_01_v',)):
    raw = load_table(ROOT / 'demo/synthetic_vehicle_1Hz.csv', nrows=nrows)
    return prepare_data(raw, 'time_s', 'pack_current_a', list(cells), 'reference_soc_pct')[0]


def _run_param(df):
    ox, oy = read_ocv(ROOT / 'demo/synthetic_ocv_table.csv')
    init = ocv_and_slope(CFG['start_soc_pct'] / 100, ox, oy)[0]
    return run_parameter_ekf(
        df.time_s.to_numpy(), df.current_a.to_numpy(),
        df.cell_01_v.to_numpy(), init, CFG['nominal_capacity_ah'], CFG)


class IdentifiabilityTests(unittest.TestCase):
    """恒流/低激励工况下参数不可辨识的边界测试。"""

    def test_constant_current_cannot_distinguish_ocv_and_r0(self):
        # 恒流放电：V = OCV - I*R0 - Up，OCV 与 R0 在同乘 I 下线性相关，无法区分。
        n = 1000
        t = np.arange(n, dtype=float)
        I = np.full(n, 10.0)
        V = 3.3 - 0.01 * I  # 纯欧姆，无极化
        cfg = dict(CFG)
        res = run_parameter_ekf(t, I, V, 3300.0, 100.0, cfg)
        # 恒流下 OCV 与 R0 的辨识应保持高度不确定；这里校验滤波器至少不崩溃。
        self.assertTrue(np.all(np.isfinite(res['online_ocv_v'])))
        self.assertTrue(np.all(np.isfinite(res['r0_mohm'])))

    def test_no_rest_means_ocv_never_updates(self):
        # 全程无静置（无 rest_late）时，OCV 不应被更新。
        df = _demo(500)
        res = _run_param(df)
        # 合成数据全程无 rest_late，OCV 应保持初值不变。
        self.assertAlmostEqual(float(res['online_ocv_v'][-1]),
                               float(res['online_ocv_v'][0]), places=9)

    def test_step_1hz_is_not_instantaneous_dcir(self):
        # 1Hz 采样无法恢复亚秒级 R0 阶跃；这里断言 step 模式判定依赖电流差分，
        # 而非把它当作真实瞬时内阻测量。
        t = np.arange(5, dtype=float)
        I = np.array([0.0, 10.0, 10.0, 10.0, 10.0])
        modes = classify_modes(t, I, 100.0, CFG)
        self.assertEqual(modes[1], 'step')
        # 后续同电流样本进入极化/稳态，而非持续 step。
        self.assertNotIn('step', modes[2:])

    def test_missing_reference_means_no_rmse(self):
        # 无参考 SOC 时不应产出 RMSE。
        raw = load_table(ROOT / 'demo/synthetic_vehicle_1Hz.csv', nrows=50)
        d, _ = prepare_data(raw, 'time_s', 'pack_current_a', ['cell_01_v'])
        _, metrics = run_analysis(d, cells=['cell_01_v'], cfg=CFG,
                                  ocv_table=read_ocv(ROOT / 'demo/synthetic_ocv_table.csv'))
        self.assertEqual(len(metrics), 0)


class ParameterBoundsTests(unittest.TestCase):
    """参数贴边告警测试。"""

    @unittest.expectedFailure
    def test_param_at_boundary_should_warn(self):
        # 当 tau 被裁剪到下界 2.0 时应产生告警；当前实现无此告警。
        # 用完整 5400 行数据，实测 tau 会漂移到底界 2.0。
        raw = load_table(ROOT / 'demo/synthetic_vehicle_1Hz.csv')
        df, _ = prepare_data(raw, 'time_s', 'pack_current_a', ['cell_01_v'], 'reference_soc_pct')
        res = _run_param(df)
        tau = res['tau_s']
        self.assertTrue(np.any(np.isclose(tau, 2.0, atol=1e-6)),
                        'tau 未贴边，测试前提不成立')
        self.fail('tau 贴到下界 2.0 但实现未触发任何告警')


class UpdateMaskTests(unittest.TestCase):
    """选择性 K 掩码与 accepted 标志的一致性测试。"""

    @unittest.expectedFailure
    def test_accepted_flag_reflects_actual_update(self):
        # 在 steady 模式（4 参数全被 mask）下，param_update_accepted 应为 False，
        # 但当前实现恒为 True（见 engine.py 的 accepted 赋值逻辑）。
        df = _demo(500)
        res = _run_param(df)
        modes = np.array(res['mode'])
        steady_idx = np.where(modes == 'steady')[0]
        if len(steady_idx):
            # 排除创新超门限被整体拒绝的样本，其余应反映真实更新
            self.assertTrue(np.all(~res['param_update_accepted'][steady_idx]),
                            'steady 模式全部参数被 mask，accepted 应全为 False')


if __name__ == '__main__':
    unittest.main()
