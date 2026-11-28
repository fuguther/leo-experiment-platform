# Pigou Protocol-Causality Diagnostic

Status: `PLANNED_NOT_RUN` for the new endpoint-timeout arms. The earlier Gate-1
B1/B2 receipts remain valid historical evidence, but they executed the legacy
local-failure retry clock.

## Question

Does queue loss and retry amplification still make dynamic queue routing worse
than SP-Hop after removing two non-causal shortcuts?

1. an intermediate satellite no longer discards a packet from an application
   deadline it cannot observe;
2. a shared-queue overflow grants no instantaneous source feedback. Loss becomes
   visible only when the source's current attempt clock ends.

## Implemented experiment semantics

- `retry.deadline_policy=endpoint_only` detaches transport forwarding from the
  application deadline. Delivery checks the application deadline at the
  destination, and the source checks it before admission.
- `retry.discovery=endpoint_timeout` schedules the source retry after the current
  attempt clock, never at the overflow instant.
- `retry.backoff_policy=fixed` uses the configured base backoff.
- `retry.backoff_policy=exponential` doubles both the attempt clock and backoff
  after each retry.
- Existing configurations that omit these optional fields retain the historical
  router-timer/local-failure/fixed behavior and config identity.

This is deliberate causal emulation, not a complete QUIC/TCP/CoAP stack. ACK
delay, an RTT/PTO estimator, receiver reassembly and duplicate suppression remain
unsupported.

## Arm plan

Generate the deterministic Scene-A plan:

```bash
python3 CODE/scripts/pigou_protocol_diagnostic.py \
  --out /private/tmp/pigou-protocol-plan-101 \
  --seed 101
```

The generator writes 10 arms plus `manifest.json`.

Main contrast, 64 kbit, 235 Mbps, 2.8x pulse:

- `sp-retry-off`
- `sp-rto-exp`
- `dyn-retry-off`
- `dyn-rto-exp`

Separate sensitivities:

- `sp-rto-fixed`, `dyn-rto-fixed`: fixed versus 2x exponential backoff.
- `sp-granularity-64k`, `dyn-granularity-64k`,
  `sp-granularity-12k`, `dyn-granularity-12k`: matched 1.4x cohort with
  packet-count granularity changed from 64 kbit to 12 kbit.

The sensitivity arms must not be merged into the main headline.

## Metrics fixed before seeing results

`CODE/scripts/pigou_study.py` now reports:

- `tx_goodput` and `first_attempt_pdr`;
- `load_amplification` and `retry_reinjections`;
- `t_rec_onset_s` (start of the recovery streak);
- `t_rec_clear_s` (end of the first complete recovery streak);
- `t_rec_right_censored`, with threshold, bucket and streak length exported;
- `rgd_fraction_seconds`, plus observed bucket coverage;
- `hir`, measured as attempted ISL service hops divided by the current-time
  nominal shortest-path hops;
- `whr`, failed-transaction ISL service hops divided by all ISL service hops.

The recovery endpoint is the end of the streak, not its start. Missing recovery
buckets do not silently count as successful or failed traffic.

## Survival rule

The main problem survives this diagnostic only if the predeclared main contrast
shows a material Dyn-Queue TxGoodput deficit together with excess load
amplification or recovery impairment under endpoint-visible loss. The result must
also survive fixed/exponential backoff and packet-granularity sensitivity.

If the effect disappears, record `GATE1_PHENOMENON_NOT_SUPPORTED` and do not tune
retry fraction, retry count or load until the story reappears.

One seed is diagnostic only. Promote to at least five predeclared test seeds only
after the paired main contrast survives. No result may be called protocol-compliant
until an RTT/PTO estimator and ACK semantics are implemented.
