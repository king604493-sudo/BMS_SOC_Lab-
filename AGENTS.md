# AGENTS.md — BMS SOC Lab (Codex engineering instructions)

## Mission
Maintain and extend a **local, research-only** BMS SOC estimation lab for 1Hz vehicle telemetry (up to 16 cells) on Apple Silicon Macs. Core algorithms: Ah, one-RC SOC EKF, dual EKF parameter/OCV study, five driving-mode Q schedule, future aged OCV-SOC reconstruction.

## Mandatory first read
Read `README.md`, `docs/软件设计说明书.md`, `config.json`, `bmslab/engine.py`, `tests/test_engine.py`, and `RUN_SAMSUNG_30T.md` before editing behavior. Do not infer experimental results from prior chat alone.

## Hard invariants
1. **Do not let online-estimated OCV silently replace the offline OCV-SOC lookup used by SOC EKF.** Make that an explicit experimental feature flag only after validation.
2. Discharge current is positive after standardization. SOC is a fraction [0,1] inside EKF, displayed in percent. Parameter KF operates in mV, mΩ, seconds; document unit conversions.
3. Separate **independent reference SOC**, **two-anchor Ah proxy**, **OEM displayed SOC**, and **no reference**. Never call proxies ground truth; do not calculate RMSE if reference is unavailable.
4. Samsung original MAT files are **not in this repository**. `demo/` and format fixtures are synthetic. No fictitious real-data validation.
5. Five Q modes: `step`, `polarization`, `steady`, `rest_early`, `rest_late`. Preserve configurable multipliers and update masks; do not claim they are optimized for a real cell.
6. Keep the original parameter EKF runnable; implement alternate designs behind flags/branches and compare on unchanged data.
7. All raw car telemetry remains local. Never transmit to external APIs or add cloud logging without explicit user request.
8. This software must not be used for production vehicle SOC or safety decisions. Preserve disclaimer and graceful failure behavior.

## Development loop
- Run `python -m unittest discover -s tests -v` before and after changes.
- Run CLI demo end-to-end and compare against known **synthetic** baseline in docs. If result changes, explain mechanism and label as new baseline.
- On EKF changes check: covariance symmetric/PSD, finite states, sensible physical bounds, innovation outliers, update count by mode, consistency of P/Q/R units, reproducibility.
- Add tests for missing samples, current sign, time gaps, 16 cells, zero-excitation identifiability, mode transitions, parameter bounds, freeze semantics, and no reference SOC.
- Favor simple pure functions and pinned runtime dependencies. Don't add giant frameworks or new system dependencies without justification.
- Do not claim that small terminal voltage residual proves correct OCV/R0/SOC identification.

## High-priority known issues
- Dual EKF yields SOC RMSE ~17.9–18.2 pp on 5400-row **synthetic** demo; may misattribute model error to OCV and impair SOC.
- Measurement update mask zeros entries of Kalman gain after gain calculation; review posterior covariance and mathematical consistency.
- The same cell voltage is used by both parameter EKF and SOC EKF; analyze shared observation correlation and overconfidence.
- `samsung30t_pipeline.py` produces a C/20 **quasi-OCV**; dynamic segment's reference SOC may assume 100% initial SOC and is not independently verified.
- 1Hz telemetry cannot isolate sub-second true ohmic step response; do not overstate instantaneous R0 identification.

## First authorized work
Complete P0 and P1 only: reproduce baseline, add diagnostics and safety tests, produce an audit report with specific failure causes. Do NOT rewrite estimator or invent a "successful" OCV validation unless supported by data.
