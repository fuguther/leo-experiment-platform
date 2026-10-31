# realized-vs-counterfactual freshness comparison

- advertised cell: m3-c2-queue_greedy-h1000-s7
- fresh (ideal_current) cell: m3-real-c2-queue_greedy-h1000-s7
- same trace: True  policy: queue_greedy -> queue_greedy
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 923.394 Mbps
- max |Δλ|: 16.000 Mbps
- z_realized p50 / max: 0.013819095477386936 / 0.3637242426566655
- paired E2E (fresh - advertised): mean -1.0715329848403552 ms, fresh faster 887 / slower 918

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:5:25 | 73.344 | 58.106 | 15.238 | 41.894 | 0.364 |
| isl:45:65 | 76.183 | 64.502 | 11.682 | 35.498 | 0.329 |
| isl:65:85 | 76.253 | 64.900 | 11.353 | 35.100 | 0.323 |
| isl:85:105 | 76.123 | 64.800 | 11.323 | 35.200 | 0.322 |
| isl:126:6 | 65.488 | 51.600 | 13.888 | 48.400 | 0.287 |
| isl:64:44 | 28.400 | 43.900 | -15.500 | 56.100 | 0.276 |
| isl:105:125 | 53.692 | 40.800 | 12.892 | 59.200 | 0.218 |
| isl:25:5 | 34.500 | 45.900 | -11.400 | 54.100 | 0.211 |
| isl:36:56 | 50.865 | 39.800 | 11.065 | 60.200 | 0.184 |
| isl:24:44 | 51.000 | 40.603 | 10.397 | 59.397 | 0.175 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-c2-queue_greedy_ewma-h1000-s7
- fresh (ideal_current) cell: m3-real-c2-queue_greedy_ewma-h1000-s7
- same trace: True  policy: queue_greedy_ewma -> queue_greedy_ewma
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 2020.057 Mbps
- max |Δλ|: 32.004 Mbps
- z_realized p50 / max: 0.028688524590163935 / 0.6817189479552296
- paired E2E (fresh - advertised): mean 10.966669875162479 ms, fresh faster 835 / slower 934

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:45:65 | 88.702 | 64.502 | 24.200 | 35.498 | 0.682 |
| isl:65:85 | 88.765 | 64.900 | 23.865 | 35.100 | 0.680 |
| isl:85:105 | 88.634 | 64.800 | 23.834 | 35.200 | 0.677 |
| isl:125:5 | 86.274 | 60.521 | 25.753 | 39.479 | 0.652 |
| isl:105:125 | 72.804 | 40.800 | 32.004 | 59.200 | 0.541 |
| isl:126:6 | 76.888 | 51.600 | 25.288 | 48.400 | 0.522 |
| isl:6:26 | 74.100 | 47.517 | 26.583 | 52.483 | 0.507 |
| isl:45:25 | 25.989 | 49.700 | -23.711 | 50.300 | 0.471 |
| isl:125:105 | 28.215 | 50.511 | -22.295 | 49.489 | 0.451 |
| isl:105:85 | 28.183 | 50.409 | -22.226 | 49.591 | 0.448 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-c2-queue_softmin-h1000-s7
- fresh (ideal_current) cell: m3-real-c2-queue_softmin-h1000-s7
- same trace: True  policy: queue_softmin -> queue_softmin
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 386.653 Mbps
- max |Δλ|: 4.922 Mbps
- z_realized p50 / max: 0.008968609865470852 / 0.08340376134225877
- paired E2E (fresh - advertised): mean -4.961952167020457 ms, fresh faster 697 / slower 638

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:57:58 | 56.420 | 59.775 | -3.355 | 40.225 | 0.083 |
| isl:56:57 | 56.670 | 59.900 | -3.230 | 40.100 | 0.081 |
| isl:58:59 | 56.100 | 59.302 | -3.202 | 40.698 | 0.079 |
| isl:36:56 | 32.900 | 37.600 | -4.700 | 62.400 | 0.075 |
| isl:59:40 | 55.800 | 58.829 | -3.029 | 41.171 | 0.074 |
| isl:64:63 | 33.700 | 28.900 | 4.800 | 71.100 | 0.068 |
| isl:62:61 | 33.600 | 28.800 | 4.800 | 71.200 | 0.067 |
| isl:63:62 | 33.600 | 28.800 | 4.800 | 71.200 | 0.067 |
| isl:60:79 | 33.491 | 28.700 | 4.791 | 71.300 | 0.067 |
| isl:61:60 | 33.500 | 28.800 | 4.700 | 71.200 | 0.066 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-c2-queue_greedy_sticky-h1000-s7
- fresh (ideal_current) cell: m3-real-c2-queue_greedy_sticky-h1000-s7
- same trace: True  policy: queue_greedy_sticky -> queue_greedy_sticky
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 1126.198 Mbps
- max |Δλ|: 29.613 Mbps
- z_realized p50 / max: 0.013259668508287293 / 0.3845218739543421
- paired E2E (fresh - advertised): mean -21.213178133442188 ms, fresh faster 875 / slower 812

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:6:26 | 52.600 | 22.987 | 29.613 | 77.013 | 0.385 |
| isl:126:6 | 64.158 | 46.558 | 17.600 | 53.442 | 0.329 |
| isl:134:14 | 57.270 | 39.670 | 17.600 | 60.330 | 0.292 |
| isl:126:125 | 16.300 | 34.200 | -17.900 | 65.800 | 0.272 |
| isl:24:44 | 41.584 | 53.764 | -12.180 | 46.236 | 0.263 |
| isl:134:135 | 12.900 | 30.300 | -17.400 | 69.700 | 0.250 |
| isl:76:56 | 22.125 | 37.100 | -14.975 | 62.900 | 0.238 |
| isl:26:46 | 38.511 | 19.315 | 19.195 | 80.685 | 0.238 |
| isl:125:124 | 8.400 | 25.500 | -17.100 | 74.500 | 0.230 |
| isl:46:45 | 36.100 | 17.400 | 18.700 | 82.600 | 0.226 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-b-queue_greedy-h1000-s7
- fresh (ideal_current) cell: m3-real-b-queue_greedy-h1000-s7
- same trace: True  policy: queue_greedy -> queue_greedy
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 695.295 Mbps
- max |Δλ|: 19.600 Mbps
- z_realized p50 / max: 0.0071090047393364926 / 0.3835941409164008
- paired E2E (fresh - advertised): mean 4.650217135462094 ms, fresh faster 692 / slower 732

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:126:6 | 69.468 | 50.468 | 19.000 | 49.532 | 0.384 |
| isl:126:125 | 10.700 | 30.300 | -19.600 | 69.700 | 0.281 |
| isl:105:125 | 51.300 | 36.484 | 14.816 | 63.516 | 0.233 |
| isl:36:56 | 45.600 | 31.068 | 14.532 | 68.932 | 0.211 |
| isl:105:104 | 1.800 | 18.400 | -16.600 | 81.600 | 0.203 |
| isl:104:84 | 1.200 | 17.200 | -16.000 | 82.800 | 0.193 |
| isl:20:39 | 39.400 | 25.100 | 14.300 | 74.900 | 0.191 |
| isl:37:36 | 39.400 | 25.100 | 14.300 | 74.900 | 0.191 |
| isl:38:37 | 39.400 | 25.100 | 14.300 | 74.900 | 0.191 |
| isl:39:38 | 39.400 | 25.100 | 14.300 | 74.900 | 0.191 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-b-queue_greedy_ewma-h1000-s7
- fresh (ideal_current) cell: m3-real-b-queue_greedy_ewma-h1000-s7
- same trace: True  policy: queue_greedy_ewma -> queue_greedy_ewma
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 1519.398 Mbps
- max |Δλ|: 43.882 Mbps
- z_realized p50 / max: 0.01026694045174538 / 0.7348856173345786
- paired E2E (fresh - advertised): mean 3.9449950938013694 ms, fresh faster 678 / slower 714

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:126:6 | 86.868 | 50.468 | 36.400 | 49.532 | 0.735 |
| isl:105:125 | 80.366 | 36.484 | 43.882 | 63.516 | 0.691 |
| isl:45:65 | 83.205 | 59.000 | 24.205 | 41.000 | 0.590 |
| isl:65:85 | 83.127 | 59.045 | 24.082 | 40.955 | 0.588 |
| isl:85:105 | 82.996 | 58.900 | 24.096 | 41.100 | 0.586 |
| isl:25:24 | 69.500 | 29.500 | 40.000 | 70.500 | 0.567 |
| isl:6:26 | 72.700 | 46.200 | 26.500 | 53.800 | 0.493 |
| isl:25:5 | 14.100 | 41.200 | -27.100 | 58.800 | 0.461 |
| isl:5:25 | 74.524 | 53.900 | 20.624 | 46.100 | 0.447 |
| isl:45:25 | 21.700 | 45.875 | -24.175 | 54.125 | 0.447 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-c2-queue_greedy-h20-s7
- fresh (ideal_current) cell: m3-real-c2-queue_greedy-h20-s7
- same trace: True  policy: queue_greedy -> queue_greedy
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 645.380 Mbps
- max |Δλ|: 12.800 Mbps
- z_realized p50 / max: 0.010227272727272727 / 0.2057877813504823
- paired E2E (fresh - advertised): mean -20.301199308619832 ms, fresh faster 1018 / slower 770

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:105:125 | 50.600 | 37.800 | 12.800 | 62.200 | 0.206 |
| isl:24:44 | 53.274 | 44.742 | 8.532 | 55.258 | 0.154 |
| isl:37:36 | 43.600 | 34.819 | 8.781 | 65.181 | 0.135 |
| isl:22:21 | 44.200 | 35.514 | 8.686 | 64.486 | 0.135 |
| isl:21:20 | 44.127 | 35.500 | 8.627 | 64.500 | 0.134 |
| isl:23:22 | 44.200 | 35.617 | 8.583 | 64.383 | 0.133 |
| isl:38:37 | 43.800 | 35.159 | 8.641 | 64.841 | 0.133 |
| isl:20:39 | 44.002 | 35.400 | 8.602 | 64.600 | 0.133 |
| isl:24:23 | 44.243 | 35.700 | 8.543 | 64.300 | 0.133 |
| isl:39:38 | 43.830 | 35.300 | 8.530 | 64.700 | 0.132 |

# realized-vs-counterfactual freshness comparison

- advertised cell: m3-c2-queue_greedy_ewma-h20-s7
- fresh (ideal_current) cell: m3-real-c2-queue_greedy_ewma-h20-s7
- same trace: True  policy: queue_greedy_ewma -> queue_greedy_ewma
- state_source of fresh arm: ideal_current
- sum |Δλ| over directed ISLs: 1962.292 Mbps
- max |Δλ|: 39.447 Mbps
- z_realized p50 / max: 0.016210739614994935 / 0.6341909646399128
- paired E2E (fresh - advertised): mean -10.118347047166752 ms, fresh faster 992 / slower 809

| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |
|---|---|---|---|---|---|
| isl:105:125 | 77.247 | 37.800 | 39.447 | 62.200 | 0.634 |
| isl:64:44 | 7.500 | 41.500 | -34.000 | 58.500 | 0.581 |
| isl:126:6 | 81.300 | 60.700 | 20.600 | 39.300 | 0.524 |
| isl:65:85 | 81.986 | 65.266 | 16.721 | 34.734 | 0.481 |
| isl:85:105 | 81.856 | 65.036 | 16.820 | 34.964 | 0.481 |
| isl:76:56 | 10.400 | 39.200 | -28.800 | 60.800 | 0.474 |
| isl:45:65 | 81.496 | 65.300 | 16.196 | 34.700 | 0.467 |
| isl:125:5 | 76.700 | 56.800 | 19.900 | 43.200 | 0.461 |
| isl:6:26 | 73.100 | 50.210 | 22.890 | 49.790 | 0.460 |
| isl:24:44 | 69.074 | 44.742 | 24.332 | 55.258 | 0.440 |
