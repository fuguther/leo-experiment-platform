from __future__ import annotations

import io
import json
import random

import pytest

from CODE.experiment_platform import replay_codec


def _encode(value):
    stream = io.StringIO()
    replay_codec.dump_graph(value, stream)
    return stream.getvalue()


def _graph(root, nodes):
    return {
        "schema": "t1-lossless-json-graph/v1",
        "root": root,
        "nodes": nodes,
    }


def test_nested_shared_observation_is_stored_once_and_round_trips_aliases():
    shared = {
        "observation": {
            "vectors": [list(range(32))],
            "markers": ["卫星", "链路", "队列"],
        }
    }
    value = {"rows": [shared] * 1000, "also": shared}

    encoded = _encode(value)
    parsed = json.loads(encoded)
    restored = replay_codec.load_graph(io.StringIO(encoded))
    expanded_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    assert parsed["schema"] == "t1-lossless-json-graph/v1"
    assert len(parsed["nodes"]) == 7
    assert len(encoded) < expanded_size // 4
    assert restored == json.loads(json.dumps(value, ensure_ascii=False))
    assert restored["rows"][0] is restored["also"]
    assert restored["rows"][0] is restored["rows"][999]


def test_round_trip_preserves_json_scalars_unicode_empty_containers_and_ref_text():
    value = {
        "integer": 9,
        "float": -0.25,
        "boolean": False,
        "null": None,
        "文字": "轨迹🌍",
        "$t1ref": "literal tag-looking string",
        "nested": {"$t1ref": 0, "empty": {}, "empty_list": []},
    }

    restored = replay_codec.load_graph(io.StringIO(_encode(value)))

    assert restored == json.loads(json.dumps(value, ensure_ascii=False))
    assert restored["$t1ref"] == "literal tag-looking string"
    assert restored["nested"]["$t1ref"] == 0
    assert restored["nested"]["empty"] == {}
    assert restored["nested"]["empty_list"] == []


def test_integer_keys_and_tuple_follow_standard_json_semantics():
    value = {1: (1, 2), True: "bool", None: "null", 1.5: "float"}
    assert replay_codec.load_graph(io.StringIO(_encode(value))) == json.loads(json.dumps(value))


def test_key_normalization_collision_is_rejected_without_losing_fields():
    with pytest.raises(ValueError, match="collide"):
        _encode({"1": "first", 1: "last"})


def test_non_shared_random_json_values_match_standard_json_round_trip():
    rng = random.Random(481516)

    def make_value(depth):
        if depth == 0 or rng.random() < 0.35:
            return rng.choice([
                None,
                True,
                False,
                rng.randint(-1000, 1000),
                rng.random() * 10 - 5,
                "星间链路🌐",
            ])
        if rng.random() < 0.5:
            return [make_value(depth - 1) for _ in range(rng.randrange(4))]
        result = {}
        for index in range(rng.randrange(4)):
            key = f"k{index}" if rng.random() < 0.5 else index + 10
            result[key] = make_value(depth - 1)
        return result

    for _ in range(40):
        value = make_value(4)
        expected = json.loads(json.dumps(value, ensure_ascii=False))
        actual = replay_codec.load_graph(io.StringIO(_encode(value)))
        assert actual == expected


@pytest.mark.parametrize("root,nodes", [
    ({"$t1ref": 1}, [{"kind": "list", "items": []}]),
    ({"$t1ref": 0}, [{"kind": "list", "items": [{"$t1ref": 0}]}]),
    ({"$t1ref": 0}, [{"kind": "dict", "pairs": [["x", 1], ["x", 2]]}]),
    ({"$t1ref": 0}, [
        {"kind": "list", "items": []},
        {"kind": "list", "items": []},
    ]),
    ({"$t1ref": 0}, [{"kind": "unknown", "items": []}]),
    ({"$t1ref": 0}, [{"kind": "dict", "pairs": [[1, "value"]]}]),
])
def test_decoder_rejects_damaged_references_cycles_duplicate_keys_and_nodes(root, nodes):
    with pytest.raises(ValueError):
        replay_codec.decode_graph(_graph(root, nodes))


@pytest.mark.parametrize("value", [
    {1, 2},
    {("tuple",): "unsupported key"},
    float("nan"),
])
def test_encoder_rejects_values_outside_its_strict_json_subset(value):
    with pytest.raises((TypeError, ValueError)):
        _encode(value)


def test_encoder_rejects_container_cycles():
    value = []
    value.append(value)

    with pytest.raises(ValueError, match="cycle"):
        _encode(value)


def test_indexer_references_the_original_containers_instead_of_copying_them():
    """A replay graph must never be duplicated just to serialize it.

    The previous encoder copied every dictionary into key/value pairs and every
    sequence into a same-length list, so a real 96-star replay doubled its own
    memory and was killed by the container limit.  The indexer must return the
    original objects.
    """
    shared = {"values": list(range(64))}
    value = {"rows": [shared] * 500}

    index_by_identity, containers = replay_codec._index_graph(value)

    assert containers[0] is value
    assert containers[1] is value["rows"]
    assert containers[2] is shared
    assert containers[3] is shared["values"]
    assert len(containers) == 4
    assert index_by_identity[id(value)] == 0
    assert index_by_identity[id(shared)] == 2


def test_stream_decoder_rejects_duplicate_json_object_fields():
    encoded = (
        '{"schema":"t1-lossless-json-graph/v1",'
        '"schema":"t1-lossless-json-graph/v1",'
        '"root":null,"nodes":[]}'
    )

    with pytest.raises(ValueError, match="duplicate"):
        replay_codec.load_graph(io.StringIO(encoded))
