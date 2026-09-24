"""Distance-dependent MCS link rates (legacy-characterized).

This module re-implements the legacy link-budget formulas
(SimulationRL.py:get_data_rate:8295, Gateway.adjustDataRate:2887,
Satellite.adjustDownRate:2361) as a self-contained, importable module for
leo_sim V2.  Legacy source is NOT copied: the formulas are re-written from the
platform specification and pinned by `tests/test_link_budget_integration.py`
against the same golden values as `test_link_budget_characterization.py`.

The legacy platform really used three different RF parameter sets:

* ISL inter-plane links: 26 GHz / 500 MHz / 10 W / 0.26 m Tx+Rx
  (markovianMatchingTwo, SimulationRL.py:8353).
* Uplink (ground gateway -> satellite): 30 GHz / 500 MHz / 20 W / 0.33 m Tx /
  0.26 m Rx (Gateway.gs2ngeo, SimulationRL.py:2617).
* Downlink (satellite -> ground gateway): 20 GHz / 500 MHz / 10 W / 0.26 m
  Tx+Rx (module-level f/B/maxPtx/Adtx/Adrx globals, SimulationRL.py:297-310,
  used to build Satellite.ngeo2gt at :1935).

All three use the same DVB-S2X-style MCS table (speff/linear thresholds).
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np

VC = 299792458.0          # speed of light, m/s
K_BOLTZ = 1.38e-23        # Boltzmann constant
EFF = 0.55                # parabolic antenna efficiency

# MCS threshold tables embedded in get_data_rate/adjustDataRate/adjustDownRate
# (SimulationRL.py:8298-8311 / 2889-2907 / 2363-2381).
LEGACY_DVBS2X_SPEFF = np.array([
    0, 0.434841, 0.490243, 0.567805, 0.656448, 0.789412, 0.889135, 0.988858,
    1.088581, 1.188304, 1.322253, 1.487473, 1.587196, 1.647211, 1.713601,
    1.779991, 1.972253, 2.10485, 2.193247, 2.370043, 2.458441, 2.524739,
    2.635236, 2.637201, 2.745734, 2.856231, 2.966728, 3.077225, 3.165623,
    3.289502, 3.300184, 3.510192, 3.620536, 3.703295, 3.841226, 3.951571,
    4.206428, 4.338659, 4.603122, 4.735354, 4.933701, 5.06569, 5.241514,
    5.417338, 5.593162, 5.768987, 5.900855])
LEGACY_DVBS2X_LIN = np.array([
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

LEGACY_DVBS2X = "legacy-dvbs2x"


@dataclasses.dataclass(frozen=True)
class RFParams:
    """One physical RF link parameter set (mirrors legacy RFlink inputs)."""
    frequency_hz: float
    bandwidth_hz: float
    max_ptx_w: float
    antenna_diameter_tx_m: float
    antenna_diameter_rx_m: float
    pointing_loss_db: float
    noise_figure_db: float
    noise_temperature_k: float
    min_rate_bps: float

    @classmethod
    def from_mapping(cls, m) -> "RFParams":
        return cls(
            frequency_hz=float(m["frequency_hz"]),
            bandwidth_hz=float(m["bandwidth_hz"]),
            max_ptx_w=float(m["max_ptx_w"]),
            antenna_diameter_tx_m=float(m["antenna_diameter_tx_m"]),
            antenna_diameter_rx_m=float(m["antenna_diameter_rx_m"]),
            pointing_loss_db=float(m["pointing_loss_db"]),
            noise_figure_db=float(m["noise_figure_db"]),
            noise_temperature_k=float(m["noise_temperature_k"]),
            min_rate_bps=float(m["min_rate_bps"]),
        )


LEGACY_ISL_RF = RFParams(
    frequency_hz=26e9, bandwidth_hz=500e6, max_ptx_w=10.0,
    antenna_diameter_tx_m=0.26, antenna_diameter_rx_m=0.26,
    pointing_loss_db=0.3, noise_figure_db=2.0, noise_temperature_k=290.0,
    min_rate_bps=10_000.0)
LEGACY_UPLINK_RF = RFParams(
    frequency_hz=30e9, bandwidth_hz=500e6, max_ptx_w=20.0,
    antenna_diameter_tx_m=0.33, antenna_diameter_rx_m=0.26,
    pointing_loss_db=0.3, noise_figure_db=2.0, noise_temperature_k=290.0,
    min_rate_bps=10_000.0)
LEGACY_DOWNLINK_RF = RFParams(
    frequency_hz=20e9, bandwidth_hz=500e6, max_ptx_w=10.0,
    antenna_diameter_tx_m=0.26, antenna_diameter_rx_m=0.26,
    pointing_loss_db=0.3, noise_figure_db=2.0, noise_temperature_k=290.0,
    min_rate_bps=10_000.0)


def _derived(rf: RFParams) -> tuple[float, float, float]:
    """Legacy RFlink derived quantities: maxPtx_db, G (dB), No (dBW)."""
    maxptx_db = 10 * math.log10(rf.max_ptx_w)
    gtx = 10 * math.log10(EFF * ((math.pi * rf.antenna_diameter_tx_m
                                  * rf.frequency_hz / VC) ** 2))
    grx = 10 * math.log10(EFF * ((math.pi * rf.antenna_diameter_rx_m
                                  * rf.frequency_hz / VC) ** 2))
    g = gtx + grx - 2 * rf.pointing_loss_db
    no = (10 * math.log10(rf.bandwidth_hz * K_BOLTZ) + rf.noise_figure_db
          + 10 * math.log10(290 + (rf.noise_temperature_k - 290)
                            * (10 ** (-rf.noise_figure_db / 10))))
    return maxptx_db, g, no


def max_rate_range_km(rf: RFParams,
                      table: str = LEGACY_DVBS2X) -> float:
    """Largest slant range (km) at which ANY MCS rate is feasible.

    The legacy tables are monotone in SNR, so the highest usable range is the
    distance at which SNR equals the first linear threshold
    (LEGACY_DVBS2X_LIN[1]).  The kernel uses this value with a certified
    range-under query to wake a service whose peer is temporarily beyond the
    rate threshold instead of waiting to the horizon.

    NOTE: if bandwidth * first non-zero spectral efficiency is below
    min_rate_bps, the effective 'rate up' threshold is not this distance;
    config validation rejects that combination when rate_model=mcs.
    """
    if table != LEGACY_DVBS2X:
        raise ValueError(f"unsupported mcs_table {table!r}")
    maxptx_db, g, no = _derived(rf)
    snr_target = float(LEGACY_DVBS2X_LIN[1])
    # snr = 10^((maxPtx_db + G - path_loss - No)/10) => path_loss_db =
    # maxPtx_db + G - No - 10*log10(snr)
    path_loss_db = maxptx_db + g - no - 10 * math.log10(snr_target)
    # path_loss_db = 10*log10((4*pi*d*f/VC)^2)
    d_m = math.pow(10.0, path_loss_db / 20.0) * VC \
        / (4.0 * math.pi * rf.frequency_hz)
    return d_m / 1000.0


def mcs_rate_threshold_ranges_km(
        rf: RFParams, table: str = LEGACY_DVBS2X) -> tuple[float, ...]:
    """Return slant ranges at which the legacy MCS rate can change.

    ``mcs_rate_bps`` is piecewise constant in distance.  A new table entry
    becomes feasible whenever received SNR crosses one of the positive MCS
    thresholds, so these ranges are the certified cut points for the
    availability metric's interval quadrature.
    """
    if table != LEGACY_DVBS2X:
        raise ValueError(f"unsupported mcs_table {table!r}")
    maxptx_db, g, no = _derived(rf)
    out = []
    for snr_target in LEGACY_DVBS2X_LIN[1:]:
        path_loss_db = (maxptx_db + g - no
                        - 10 * math.log10(float(snr_target)))
        d_m = (math.pow(10.0, path_loss_db / 20.0) * VC
               / (4.0 * math.pi * rf.frequency_hz))
        out.append(d_m / 1000.0)
    return tuple(sorted(set(out)))


def mcs_rate_bps(slant_km: float, rf: RFParams,
                 table: str = LEGACY_DVBS2X) -> float:
    """Legacy get_data_rate/adjustDataRate/adjustDownRate for one distance."""
    if table != LEGACY_DVBS2X:
        raise ValueError(f"unsupported mcs_table {table!r}")
    d_m = float(slant_km) * 1000.0
    maxptx_db, g, no = _derived(rf)
    path_loss = 10 * math.log10((4 * math.pi * d_m * rf.frequency_hz / VC) ** 2)
    snr = 10 ** ((maxptx_db + g - path_loss - no) / 10)
    feasible = LEGACY_DVBS2X_SPEFF[np.nonzero(LEGACY_DVBS2X_LIN <= snr)]
    if feasible.size == 0:
        return 0.0
    rate = rf.bandwidth_hz * float(feasible[-1])
    if rate < rf.min_rate_bps:
        return 0.0
    return rate
