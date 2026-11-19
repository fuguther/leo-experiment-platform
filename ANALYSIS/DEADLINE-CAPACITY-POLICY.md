# Deadline-capacity routing policy (v0 implementation note)

This policy is an **engineering baseline**, not a new LEO routing invention. It
is a deterministic portfolio of two existing policies already implemented in
`CODE/leo_sim/routing.py`:

- `hop`: shortest number of ISL hops using arrived destination/service
  advertisements;
- `capacity`: hop/delay plus advertised queue delay, with unknown queues treated
  as unavailable.

The portfolio adds one packet-local signal: if the packet's remaining deadline
is at most 5 simulated seconds, it executes the `hop` ordering first; otherwise
it executes the `capacity` ordering first. The other ordering is preserved as a
fallback/tie-break. The owner still performs the normal legal-direction,
waiting-room, current-service and link-generation checks.

## Input to the routing function

`choose_next_hop(policy="deadline_capacity", sat, dst_cell, now, ...,
deadline_s=pkt.deadline)` receives the same information as the existing
`hop`/`capacity` policy:

- current satellite and destination cell;
- arrived, valid destination advertisements in the local control cache;
- directly observed local per-direction queue bits;
- advertised remote queue/propagation estimates where available;
- current topology and candidate directions;
- packet deadline (only for the portfolio switch).

It does not read a future trace, the simulator's hidden global queue state or a
packet's future fate.

## Output

The function returns an ordered list of legal candidate directions plus a
status (`ok`, `no_info`, or `unreachable`). The kernel then applies its existing
legal-direction mask, loop avoidance, room/current checks and owner admission.
No new admission authority is created.

## Explicit limitations

- The 5-second switch is a design parameter, not an empirically validated
  threshold.
- `hop` is not guaranteed to minimize propagation/queue delay, and `capacity`
  is not guaranteed to satisfy deadlines.
- The policy does not optimize complete offered-cohort p99; it does not use
  cohort completion counts, APP completion evidence or a joint batch map.
- It must not be reported as a solution to the p99/cohort problem or as a
  contribution over the existing `hop` and `capacity` algorithms.
- It is useful only as a runnable v0 baseline for later cohort-aware work.

## Reproduction

```bash
python3 -m CODE.leo_sim config validate \
  CODE/leo_sim/profiles/comparison_deadline_capacity.yaml
python3 -m CODE.leo_sim run \
  --config CODE/leo_sim/profiles/comparison_deadline_capacity.yaml \
  --out /path/to/new/output
```

For a matched comparison, use the same world with `comparison_hop_deadline.yaml`
(which differs only in `routing.policy`).
