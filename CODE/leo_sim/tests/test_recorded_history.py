"""Output-only advertisement history caching, without starting a simulation."""
from __future__ import annotations

from CODE.leo_sim import kernel
from CODE.leo_sim.control import CacheEntry, LocalCache


def _entry(origin=0, *, generated=0.0, received=0.1, peer=7,
           downlink_count=0):
    downlinks = {
        f"cell-{index:02d}": {
            "queue_bits": float(index + 1),
            "rate_bps": 2_000_000.0,
            "propagation_s": 0.02,
            "available": True,
            "queue_scope": "per_cell",
            "queue_semantics": "queued_bits",
        }
        for index in range(downlink_count)
    }
    return CacheEntry(
        origin,
        {
            "isl_queue_bits": {
                "E": {
                    "peer": peer,
                    "value": 1234,
                    "generation": 3,
                    "rate_bps": 1_000_000.0,
                    "work_ahead_bits_proxy": 456.0,
                }
            },
            "processing_resources": {"queue_bits": 789.0},
            "downlink_resources": downlinks,
        },
        generated_at=generated,
        received_at=received,
        ttl_s=20.0,
        hops=2,
    )


def _kernel_with_entries(entries, *, decision_sink=None, topology=None):
    """Build only the real cache/history state; no Kernel.__init__ or run."""
    instance = kernel.Kernel.__new__(kernel.Kernel)
    instance.decision_sink = [] if decision_sink is None else decision_sink
    instance.caches = {1: LocalCache()}
    instance.topo = {0: {"E": 7} if topology is None else topology}
    instance._recorded_advertisement_history_cache = {}
    for entry in entries:
        instance.caches[1].put(entry)
    return instance


def test_recorded_history_is_shared_across_observations_with_fresh_lists():
    entry = _entry(downlink_count=99)
    instance = _kernel_with_entries([entry])
    instance._observed_cache_entries = lambda sat, now: {0: entry}
    instance._compute_state_now = lambda sat: {
        "wait_estimate_s": 0.0,
        "service_s": 0.0,
    }
    instance._query_state_at = lambda sat, now, delay: {"at": now}
    instance._candidate_resource_map = lambda pkt, sat, now, candidates: {}
    instance.isls = {1: {}}

    def observe(now):
        return instance._observation_at_start(
            None, 1, now, mode="frozen", source="test",
            own_queue_bits={}, considered=[], legal=[], status=None,
            kind="hold", action=None,
        )["neighbours"]["0"]["advertised_history"]

    first = observe(1.0)
    second = observe(1.0)

    assert first == second
    assert first is not second
    assert first[0] is second[0]
    first.append({"caller_owned": True})
    assert len(second) == 1
    assert len(second[0]["advertised_downlink_resources"]) == 99


def test_recorded_history_matches_force_path_and_keeps_all_downlinks():
    entry = _entry(downlink_count=99)
    instance = _kernel_with_entries([entry])

    expected = instance._advertisement_history(1, 0, 1.0, force=True)
    recorded = instance._recorded_advertisement_history(1, 0, 1.0)

    assert recorded == expected
    assert len(recorded[0]["advertised_downlink_resources"]) == 99
    assert recorded[0]["advertised_downlink_resources"]["cell-98"] == {
        "queue_bits": 99.0,
        "rate_bps": 2_000_000.0,
        "propagation_s": 0.02,
        "available": True,
        "queue_scope": "per_cell",
        "queue_semantics": "queued_bits",
    }


def test_recorded_history_preserves_arrival_order_duplicates_and_filters_future():
    first = _entry(generated=0.0, received=0.2)
    duplicate_sample = _entry(generated=0.0, received=0.3)
    later_generation = _entry(generated=0.15, received=0.4)
    future = _entry(generated=0.25, received=0.7)
    instance = _kernel_with_entries(
        [first, duplicate_sample, later_generation, future])

    rows = instance._recorded_advertisement_history(1, 0, 0.5)

    assert [row["received_at"] for row in rows] == [0.2, 0.3, 0.4]
    assert [row["generated_at"] for row in rows] == [0.0, 0.0, 0.15]
    assert rows == instance._advertisement_history(1, 0, 0.5, force=True)

    after_arrival = instance._recorded_advertisement_history(1, 0, 0.7)
    assert [row["received_at"] for row in after_arrival] == [0.2, 0.3, 0.4, 0.7]
    assert after_arrival == instance._advertisement_history(
        1, 0, 0.7, force=True)


def test_changed_arrival_order_misses_cache_and_reorders_rows():
    first = _entry(generated=0.0, received=0.2)
    second = _entry(generated=0.0, received=0.3)
    instance = _kernel_with_entries([first, second])

    original = instance._recorded_advertisement_history(1, 0, 1.0)
    instance.caches[1].history[0][0], instance.caches[1].history[0][1] = \
        second, first
    reordered = instance._recorded_advertisement_history(1, 0, 1.0)

    assert [row["received_at"] for row in reordered] == [0.3, 0.2]
    assert reordered == instance._advertisement_history(
        1, 0, 1.0, force=True)
    assert reordered[0] is original[1]
    assert reordered[1] is original[0]


def test_topology_rematch_invalidates_recorded_history_cache():
    entry = _entry(peer=7)
    instance = _kernel_with_entries([entry], topology={"E": 7})

    before = instance._recorded_advertisement_history(1, 0, 1.0)
    assert before[0]["advertised_isl_queue_bits"] == {"E": 1234}

    instance.topo[0] = {"E": 8}
    after = instance._recorded_advertisement_history(1, 0, 1.0)

    assert after[0]["advertised_isl_queue_bits"] == {}
    assert after == instance._advertisement_history(1, 0, 1.0, force=True)
    assert after[0] is not before[0]
    assert len(instance._recorded_advertisement_history_cache) == 1


def test_cache_replaces_only_the_current_version_for_each_receiver_origin():
    first = _entry(generated=0.0, received=0.1)
    instance = _kernel_with_entries([first])
    previous = instance._recorded_advertisement_history(1, 0, 1.0)
    previous_sample = previous[0]

    second = _entry(generated=0.2, received=0.3)
    instance.caches[1].put(second)
    current = instance._recorded_advertisement_history(1, 0, 1.0)
    expected = instance._advertisement_history(1, 0, 1.0, force=True)

    assert [row["generated_at"] for row in current] == [0.0, 0.2]
    assert current == expected
    assert current[0] is previous_sample
    assert current[1] is not previous_sample
    assert len(instance._recorded_advertisement_history_cache) == 1
    key, samples = instance._recorded_advertisement_history_cache[(1, 0)]
    assert key[0] == (first, second)
    assert key[0][0] is first
    assert key[0][1] is second
    assert tuple(samples) == tuple(current)


def test_missing_decision_sink_matches_non_forced_helper_without_caching():
    instance = _kernel_with_entries([_entry()], decision_sink=[])
    instance.decision_sink = None

    assert instance._recorded_advertisement_history(1, 0, 1.0) == \
        instance._advertisement_history(1, 0, 1.0)
    assert instance._recorded_advertisement_history_cache == {}


def test_online_force_path_is_independent_of_recorded_history_cache():
    instance = _kernel_with_entries([_entry(downlink_count=99)])
    before = instance._advertisement_history(1, 0, 1.0, force=True)
    instance._recorded_advertisement_history(1, 0, 1.0)
    after = instance._advertisement_history(1, 0, 1.0, force=True)

    assert after == before
    assert after[0] is not before[0]
    assert len(after[0]["advertised_downlink_resources"]) == 99
