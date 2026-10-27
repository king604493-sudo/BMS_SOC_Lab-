import unittest
import numpy as np
import pandas as pd
from bmslab.engine import (
    load_config,read_ocv,load_table,detect_columns,prepare_data,
    make_two_anchor_reference,run_ah,run_analysis,run_parameter_ekf,
)
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class BMSTests(unittest.TestCase):
    def test_demo_io(self):
        source=load_table(ROOT/'demo/synthetic_vehicle_1Hz.csv',nrows=100)
        m=detect_columns(source)
        self.assertEqual(m['current'],'pack_current_a')
        self.assertGreaterEqual(len(m['cells']),2)
        d,w=prepare_data(source,m['time'],m['current'],m['cells'][:2],m['reference'])
        self.assertEqual(len(d),100)
        self.assertAlmostEqual(d['current_a'].iloc[0],source['pack_current_a'].iloc[0])

    def test_ah_formula(self):
        a=run_ah(np.array([0.,3600.]),np.array([10.,10.]),100.,100.)
        self.assertAlmostEqual(a[-1],90.,places=6)

    def test_two_anchor_formula(self):
        t=np.arange(3601,dtype=float)
        i=np.full(len(t),10.,dtype=float)
        z,q=make_two_anchor_reference(t,i,100.,90.)
        self.assertAlmostEqual(q,100.,places=6)
        self.assertAlmostEqual(z[0],100.,places=6)
        self.assertAlmostEqual(z[-1],90.,places=6)

    def test_single_dual_finite(self):
        raw=load_table(ROOT/'demo/synthetic_vehicle_1Hz.csv',nrows=500)
        d,_=prepare_data(raw,'time_s','pack_current_a',['cell_01_v'],'reference_soc_pct')
        result,metric=run_analysis(d,cells=['cell_01_v'],cfg=load_config(),
                                    ocv_table=read_ocv(ROOT/'demo/synthetic_ocv_table.csv'))
        for c in ['ah_soc_pct','cell_01_v_single_soc_pct','cell_01_v_dual_soc_pct',
                  'cell_01_v_online_ocv_v','cell_01_v_r0_mohm','cell_01_v_r1_mohm']:
            self.assertTrue(np.all(np.isfinite(result[c])),c)
        self.assertEqual(set(metric['algorithm']),{'ah','single_ekf','dual_ekf_prototype'})
        self.assertEqual(len(result),500)
        self.assertTrue(np.all((result['cell_01_v_single_soc_pct'] >= 0) &
                               (result['cell_01_v_single_soc_pct'] <= 100)))

    def test_no_ref_no_rmse(self):
        raw=load_table(ROOT/'demo/synthetic_vehicle_1Hz.csv',nrows=50)
        d,_=prepare_data(raw,'time_s','pack_current_a',['cell_01_v'])
        _,metric=run_analysis(d,cells=['cell_01_v'],cfg=load_config(),
                              ocv_table=read_ocv(ROOT/'demo/synthetic_ocv_table.csv'))
        self.assertEqual(len(metric),0)

if __name__=='__main__':unittest.main()
