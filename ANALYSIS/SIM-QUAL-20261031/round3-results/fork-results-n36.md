# Same-continuation forced-action forks

- forks: 20
- all branch_states_identical: True
- sign: cohort delta = fresh - stale (negative = fresh action faster)

| decision | stratum | baseline->forced | tagged delta ms | cohort mean ms | p10 | p90 | faster/slower | proxy R ms | gamma |
|---|---|---|---|---|---|---|---|---|---|
| 1474 | small_margin | E->W | 0.0 | 0.0 | 0.0 | 0.0 | 0/0 | 5.10 | n/a |
| 22466 | large_gamma | N->E | 306.6 | 0.2 | 0.0 | 0.0 | 0/1 | 15.97 | 1863.595 |
| 2447 | small_margin | E->W | 0.0 | 0.1 | 0.0 | 0.0 | 0/2 | 2.45 | n/a |
| 284 | long_persistence | E->N | 0.2 | 0.0 | 0.0 | 0.0 | 1/4 | 6.58 | 0.001 |
| 28716 | large_gamma | E->N | -43.3 | -0.0 | 0.0 | 0.0 | 1/1 | 5.97 | 1549.632 |
| 30317 | large_gamma | E->S | -177.0 | -0.0 | 0.0 | 0.0 | 2/1 | 26.08 | 3239.858 |
| 45045 | large_gamma | W->S | -10.5 | -0.1 | 0.0 | 0.0 | 47/70 | 3.84 | 1251.229 |
| 45316 | random_control | S->E | 101.9 | 0.1 | 0.0 | 0.0 | 0/1 | 12.43 | 10.479 |
| 4537 | large_proxy_R | N->E | -486.9 | 2.9 | -5.3 | 10.0 | 320/393 | 47.03 | 5.217 |
| 4596 | large_proxy_R | N->E | -117.8 | -0.2 | 0.0 | 0.0 | 33/25 | 44.18 | 4.742 |
| 5280 | large_proxy_R | N->W | -472.6 | -0.2 | 0.0 | 0.0 | 4/1 | 43.35 | 2.840 |
| 976 | small_margin | E->W | 0.0 | 0.0 | 0.0 | 0.0 | 0/0 | 5.10 | n/a |
| 22724 | long_persistence | E->N | -16.0 | -0.0 | 0.0 | 0.0 | 1/0 | 4.80 | 0.146 |
| 403 | small_margin | E->W | 0.0 | 0.0 | 0.0 | 0.0 | 0/1 | 5.09 | n/a |
| 43051 | large_gamma | E->S | 135.4 | -0.0 | 0.0 | 0.0 | 2/2 | 10.00 | 5908.774 |
| 45436 | large_proxy_R | N->W | n/a | 0.1 | 0.0 | 0.0 | 2/21 | 27.85 | n/a |
| 146 | long_persistence | E->N | -16.5 | -0.0 | 0.0 | 0.0 | 3/0 | 5.61 | 1.770 |
| 178 | small_margin | E->W | 166.3 | 0.0 | 0.0 | 0.0 | 1/8 | 10.00 | n/a |
| 33026 | large_proxy_R | N->E | -297.5 | -0.5 | 0.0 | 0.0 | 16/15 | 31.56 | 0.285 |
| 39513 | large_gamma | S->E | -132.5 | 0.0 | 0.0 | 0.0 | 1/3 | 6.91 | 7509.093 |
