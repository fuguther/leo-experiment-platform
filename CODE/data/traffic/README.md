# Traffic inputs

`mlab_2026-05-27.csv` is a checked-in M-Lab-derived **measurement proxy**. It is
an hourly city-to-city summary, not a packet capture and not a calibrated user
demand trace. The simulator uses `mean_throughput_mbps * sample_count` only to
weight source/destination choices; it does not claim that these measurements
are the offered load of a real satellite operator.

The compiler maps each latitude/longitude to the configured V2 grid and records
the source SHA-256, row count, OD-pair count, and observed UTC hours in the
trace manifest. Invalid rows, missing fields, zero/negative measurements, and
out-of-range hours fail closed. No silent uniform fallback is allowed.

`mlab` may be combined with an explicit burst window. That means “measured OD
weights plus a reproducible stress transform”, not a measured burst. The burst
is recorded in the manifest and must be analysed separately from the
measurement-proxy baseline.

The source snapshot was prepared from the public M-Lab measurement ecosystem;
the checked-in CSV and its SHA are the reproducibility boundary for this repo.
The source fields and units are described by
`mlab_measured_od_burst.schema.json`. Raw client identifiers and packet-level
records are not present.

## Step-5 gate-evidence micro trace

`t1_step5_micro_ab.csv` is a **hand-authored, 6-row deterministic emission
table**, not a measurement and not a demand model. It exists so that
`EXP-20260923-T1-STEP5-MICRO-R0{1,2}` can exercise per-link utilisation and
per-packet delay decomposition on a micro scenario with a trace whose bytes are
frozen in this repository.

Columns: `packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits`
(the `demand.mode=csv` input contract, `CODE/leo_sim/trace.py`).

Content: one warm-up packet (`packet_id=99`) at `t=0` and a five-packet burst
`packet_id=1..5` at `t=3.00,3.05,3.10,3.15,3.20`, all
`(0.1N, 0.1E) -> (52.6N, 90.4E)` with 8,000,000 bits each. That emission shape
is taken from the stage-1 micro-mechanism fixture's row table
(`CODE/leo_sim/tests/test_micro_mechanism.py`), including its warm-up rationale:
the destination endpoint is created lazily, so a warm-up packet is needed before
measurements.

**This CSV does NOT reproduce the #212 fixture.** That fixture drives the kernel
through `tests/helpers.py` `StaticGeometry` — scripted adjacency and a scripted
visibility predicate. No formal configuration can express scripted geometry:
with no `geometry` argument the kernel builds a real Walker-delta
`model.Constellation` (`CODE/leo_sim/kernel.py`), and the formal CLI never
injects one. Only the row-table shape and the PHY/control parameters are reused;
the two endpoint cells are the known-good reachable pair from
`CODE/leo_sim/profiles/t1_pressure_corridor.yaml`. No #212 or M1/M2/M3
mechanism conclusion may be drawn from a run of this CSV.

The compiled trace manifest records this file's SHA-256, so the exact bytes are
bound into every run's trace identity.

