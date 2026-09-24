"""Characterization goldens for the legacy link-budget functions.

These tests freeze the numerical behavior of the retained legacy platform's
`get_data_rate` (SimulationRL.py:8295) and `los_slant_range` (:8282) as an
executable spec for the future V2 integration (design:
ANALYSIS/LINK-BUDGET-DESIGN-20260816.md). The legacy module cannot be
imported here (module-level TensorFlow import), and legacy code must not be
copied into this repo, so the formulas are re-implemented inline from the
platform spec with line citations; golden values were computed once with
this same math and are pinned below.

Legacy constants (SimulationRL.py): Vc=299792458 m/s (:297), k=1.38e-23
(:298), eff=0.55 (:299); interISL RFlink params (:8353-8363): f=26e9 Hz,
B=500e6 Hz, maxPtx=10 W, aDiameterTx=Rx=0.26 m, pointingLoss=0.3 dB,
noiseFigure=2 dB, noiseTemperature=290 K, min_rate=10e3. RFlink derived
quantities (:1804-1809): maxPtx_db=10log10(maxPtx), Gtx/Grx=
10log10(eff*(pi*d*f/Vc)^2), G=Gtx+Grx-2*pointingLoss, No=10log10(B*k)+NF+
10log10(290+(T-290)*10^(-NF/10)).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

VC = 299792458.0
K_BOLTZ = 1.38e-23
EFF = 0.55

# MCS threshold tables embedded in get_data_rate (SimulationRL.py:8298-8311)
SPEFF_THRESHOLDS = np.array([
    0, 0.434841, 0.490243, 0.567805, 0.656448, 0.789412, 0.889135, 0.988858,
    1.088581, 1.188304, 1.322253, 1.487473, 1.587196, 1.647211, 1.713601,
    1.779991, 1.972253, 2.10485, 2.193247, 2.370043, 2.458441, 2.524739,
    2.635236, 2.637201, 2.745734, 2.856231, 2.966728, 3.077225, 3.165623,
    3.289502, 3.300184, 3.510192, 3.620536, 3.703295, 3.841226, 3.951571,
    4.206428, 4.338659, 4.603122, 4.735354, 4.933701, 5.06569, 5.241514,
    5.417338, 5.593162, 5.768987, 5.900855])
LIN_THRESHOLDS = np.array([
    1e-10, 0.5188000389, 0.5821032178, 0.6266138647, 0.751622894,
    0.9332543008, 1.051961874, 1.258925412, 1.396368361, 1.671090614,
    2.041737945, 2.529297996, 2.937649652, 2.971666032, 3.25836701,
    3.548133892, 3.953666201, 4.518559444, 4.83058802, 5.508076964,
    6.45654229, 6.886522963, 6.966265141, 7.888601176, 8.452788452,
    9.354056741, 10.49542429, 11.61448614, 12.67651866, 12.88249552,
    14.48771854, 14.96235656, 16.48162392, 18.74994508, 20.18366364,
    23.1206479, 25.00345362, 30.26913428, 35.2370871, 38.63669771,
    45.18559444, 49.88844875, 52.96634439, 64.5654229, 72.27698036,
    76.55966069, 90.57326009])


def _rflink_derived():
    """RFlink(:1798) derived quantities for the legacy interISL params."""
    f, B, maxPtx, a_d, pl, nf, nt = 26e9, 500e6, 10.0, 0.26, 0.3, 2.0, 290.0
    maxptx_db = 10 * math.log10(maxPtx)
    gtx = 10 * math.log10(EFF * ((math.pi * a_d * f / VC) ** 2))
    g = 2 * gtx - 2 * pl
    no = (10 * math.log10(B * K_BOLTZ) + nf
          + 10 * math.log10(290 + (nt - 290) * (10 ** (-nf / 10))))
    return f, B, maxptx_db, g, no


def _legacy_data_rate(d_m: float) -> float:
    """get_data_rate (SimulationRL.py:8295) for one slant range in meters.

    pathLoss = 10log10((4*pi*d*f/Vc)^2)  (:8313)
    snr      = 10^((maxPtx_db + G - pathLoss - No)/10)  (:8314)
    rate     = B * max{speff_i : lin_i <= snr}, else 0  (:8318-8326)
    NOTE: the Shannon rate computed at :8315 is NOT returned; the returned
    rate is the MCS-quantized one.
    """
    f, B, maxptx_db, g, no = _rflink_derived()
    path_loss = 10 * math.log10((4 * math.pi * d_m * f / VC) ** 2)
    snr = 10 ** ((maxptx_db + g - path_loss - no) / 10)
    feasible = SPEFF_THRESHOLDS[np.nonzero(LIN_THRESHOLDS <= snr)]
    return B * feasible[-1] if feasible.size else 0.0


def _legacy_los_clamp(slant, meta, max_range, positions_dist):
    """los_slant_range (SimulationRL.py:8282): entries above the per-class
    max become inf."""
    out = np.copy(slant)
    n = len(slant)
    for i in range(n):
        for j in range(n):
            if out[i, j] > max_range[meta[i], meta[j]]:
                out[i, j] = math.inf
    return out


def test_rflink_derived_goldens():
    _f, _b, maxptx_db, g, no = _rflink_derived()
    assert maxptx_db == pytest.approx(10.0, abs=1e-12)
    assert g == pytest.approx(68.21828841819857, rel=1e-12)
    assert no == pytest.approx(-114.98752911363789, rel=1e-12)


def test_data_rate_goldens():
    """Pinned one-shot values (computed 2026-08-16 with the math above)."""
    goldens = {
        1000e3: 500e6 * 3.620536,   # snr ~= 17.61
        2000e3: 500e6 * 1.972253,   # snr ~= 4.40
        4000e3: 500e6 * 0.889135,   # snr ~= 1.10
        6000e3: 0.0,                # snr ~= 0.49 < 0.5188 -> no feasible MCS
    }
    for d_m, expected in goldens.items():
        assert _legacy_data_rate(d_m) == pytest.approx(expected, rel=1e-9), d_m


def test_data_rate_properties():
    # monotone non-increasing with distance (across 500 km..6 Mm)
    ds = np.linspace(500e3, 6000e3, 50)
    rates = [ _legacy_data_rate(float(d)) for d in ds ]
    assert all(a >= b for a, b in zip(rates, rates[1:]))
    # legacy max ISL boundary (6000 km, V2 default) already yields ZERO rate
    # with the legacy RF params — the constant-rate V2 default is NOT
    # reproduceable by the legacy link budget at long range
    assert _legacy_data_rate(6000e3) == 0.0
    # peak rate is the top MCS
    assert _legacy_data_rate(100e3) == pytest.approx(500e6 * 5.900855)


def test_los_slant_range_clamp_golden():
    dist = np.array([[math.inf, 900.0, 1500.0],
                     [900.0, math.inf, 2500.0],
                     [1500.0, 2500.0, math.inf]])
    meta = [0, 0, 1]
    max_range = np.array([[1000.0, 2000.0], [2000.0, 1000.0]])
    out = _legacy_los_clamp(dist, meta, max_range, None)
    # same-plane (class 0-0) max 1000: 900 kept, 2500 cross-link > 2000 -> inf
    expected = np.array([[math.inf, 900.0, 1500.0],
                         [900.0, math.inf, math.inf],
                         [1500.0, math.inf, math.inf]])
    assert np.array_equal(out, expected)
