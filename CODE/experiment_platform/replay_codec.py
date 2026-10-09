"""Lossless, versioned JSON storage for object graphs with shared containers."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, TextIO


SCHEMA = "t1-lossless-json-graph/v1"
_REF_TAG = "$t1ref"
_VISITING = 1
_COMPLETE = 2


@dataclass(slots=True)
class _Reference:
    index: int


@dataclass(slots=True)
class _Node:
    kind: str
    content: list[Any]


def _scalar(value: Any) -> bool:
    return value is None or type(value) in (bool, int, float, str)


def _validate_scalar(value: Any) -> None:
    if type(value) is float and not math.isfinite(value):
        raise ValueError("non-finite floats are not valid JSON values")


def _normalize_key(key: Any) -> str:
    if type(key) is str:
        return key
    if type(key) is int:
        return str(key)
    if type(key) is bool:
        return "true" if key else "false"
    if key is None:
        return "null"
    if type(key) is float:
        if not math.isfinite(key):
            raise ValueError("non-finite dictionary keys are not valid JSON")
        return json.dumps(key, allow_nan=False)
    raise TypeError("JSON graph dictionary keys must be strings or integers")


def _normalized_pairs(value: dict[Any, Any]) -> list[list[Any]]:
    """Apply json.dumps/json.loads key normalization, rejecting normalization collisions."""
    positions: dict[str, int] = {}
    pairs: list[list[Any]] = []
    for key, child in value.items():
        normalized = _normalize_key(key)
        if normalized not in positions:
            positions[normalized] = len(pairs)
            pairs.append([normalized, child])
        else:
            raise ValueError(f"dictionary keys collide after JSON normalization: {normalized!r}")
    return pairs


def _graph(root: Any) -> tuple[Any, list[_Node]]:
    nodes: list[_Node] = []
    states: list[int] = []
    index_by_identity: dict[int, int] = {}
    frames: list[tuple[int, Any, str]] = []

    def token(value: Any) -> Any:
        if _scalar(value):
            _validate_scalar(value)
            return value
        if type(value) not in (dict, list, tuple):
            raise TypeError(f"unsupported JSON graph value: {type(value).__name__}")

        identity = id(value)
        known = index_by_identity.get(identity)
        if known is not None:
            if states[known] == _VISITING:
                raise ValueError("container cycle detected")
            return _Reference(known)

        index = len(nodes)
        index_by_identity[identity] = index
        states.append(_VISITING)
        if type(value) is dict:
            node = _Node("dict", _normalized_pairs(value))
            nodes.append(node)
            frames.append((index, iter(enumerate(node.content)), "dict"))
        else:
            node = _Node("list", [None] * len(value))
            nodes.append(node)
            frames.append((index, iter(enumerate(value)), "list"))
        return _Reference(index)

    root_token = token(root)
    while frames:
        index, children, kind = frames[-1]
        try:
            position, child = next(children)
        except StopIteration:
            states[index] = _COMPLETE
            frames.pop()
            continue

        child_token = token(child[1] if kind == "dict" else child)
        if kind == "dict":
            nodes[index].content[position][1] = child_token
        else:
            nodes[index].content[position] = child_token

    return root_token, nodes


def _write_json(stream: TextIO, encoder: json.JSONEncoder, value: Any) -> None:
    for chunk in encoder.iterencode(value):
        stream.write(chunk)


def _write_token(stream: TextIO, encoder: json.JSONEncoder, value: Any) -> None:
    if isinstance(value, _Reference):
        stream.write('{"')
        stream.write(_REF_TAG)
        stream.write('":')
        stream.write(str(value.index))
        stream.write("}")
    else:
        _write_json(stream, encoder, value)


def dump_graph(value: Any, text_stream: TextIO) -> None:
    """Write a compact object-graph JSON document without building its string."""
    root, nodes = _graph(value)
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )

    text_stream.write('{"schema":"')
    text_stream.write(SCHEMA)
    text_stream.write('","root":')
    _write_token(text_stream, encoder, root)
    text_stream.write(',"nodes":[')

    for index, node in enumerate(nodes):
        if index:
            text_stream.write(",")
        if node.kind == "list":
            text_stream.write('{"kind":"list","items":[')
            for item_index, item in enumerate(node.content):
                if item_index:
                    text_stream.write(",")
                _write_token(text_stream, encoder, item)
            text_stream.write("]}")
        else:
            text_stream.write('{"kind":"dict","pairs":[')
            for pair_index, (key, item) in enumerate(node.content):
                if pair_index:
                    text_stream.write(",")
                text_stream.write("[")
                _write_json(text_stream, encoder, key)
                text_stream.write(",")
                _write_token(text_stream, encoder, item)
                text_stream.write("]")
            text_stream.write("]}")
    text_stream.write("]}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object field: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-JSON numeric constant: {value}")


def load_graph(text_stream: TextIO) -> Any:
    """Read and validate a graph document, then rebuild its shared containers."""
    parsed = json.load(
        text_stream,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    return decode_graph(parsed)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_token(value: Any, node_count: int) -> Any:
    if type(value) is dict:
        _require(set(value) == {_REF_TAG}, "invalid reference token")
        index = value[_REF_TAG]
        _require(type(index) is int, "reference index must be an integer")
        _require(0 <= index < node_count, "reference index is out of range")
        return _Reference(index)
    if _scalar(value):
        _validate_scalar(value)
        return value
    raise ValueError("node values must be JSON scalars or reference tokens")


def _references(token: Any):
    if isinstance(token, _Reference):
        yield token.index


def _node_references(node: tuple[str, list[Any]]):
    kind, content = node
    if kind == "list":
        for item in content:
            yield from _references(item)
    else:
        for _key, item in content:
            yield from _references(item)


def _validate_acyclic_and_reachable(root: Any, nodes: list[tuple[str, list[Any]]]) -> None:
    if not isinstance(root, _Reference):
        _require(not nodes, "graph contains orphan nodes")
        return

    states = [0] * len(nodes)
    states[root.index] = _VISITING
    stack = [(root.index, iter(_node_references(nodes[root.index])))]
    while stack:
        index, children = stack[-1]
        try:
            child = next(children)
        except StopIteration:
            states[index] = _COMPLETE
            stack.pop()
            continue

        if states[child] == _VISITING:
            raise ValueError("graph contains a container cycle")
        if states[child] == 0:
            states[child] = _VISITING
            stack.append((child, iter(_node_references(nodes[child]))))

    _require(all(state == _COMPLETE for state in states), "graph contains orphan nodes")


def decode_graph(parsed: Any) -> Any:
    """Decode a parsed graph document, rejecting malformed or ambiguous data."""
    _require(type(parsed) is dict, "graph document must be an object")
    _require(set(parsed) == {"schema", "root", "nodes"}, "invalid graph document fields")
    _require(parsed["schema"] == SCHEMA, "unsupported graph schema")
    raw_nodes = parsed["nodes"]
    _require(type(raw_nodes) is list, "graph nodes must be an array")
    node_count = len(raw_nodes)

    records: list[tuple[str, list[Any]]] = []
    for raw in raw_nodes:
        _require(type(raw) is dict, "graph node must be an object")
        kind = raw.get("kind")
        if kind == "list":
            _require(set(raw) == {"kind", "items"}, "invalid list node fields")
            items = raw["items"]
            _require(type(items) is list, "list node items must be an array")
            records.append(("list", [_read_token(item, node_count) for item in items]))
        elif kind == "dict":
            _require(set(raw) == {"kind", "pairs"}, "invalid dictionary node fields")
            pairs = raw["pairs"]
            _require(type(pairs) is list, "dictionary node pairs must be an array")
            seen_keys: set[str] = set()
            decoded_pairs = []
            for pair in pairs:
                _require(type(pair) is list and len(pair) == 2,
                         "dictionary node pair must contain a key and value")
                key, item = pair
                _require(type(key) is str, "dictionary node keys must be normalized strings")
                _require(key not in seen_keys, "duplicate dictionary node key")
                seen_keys.add(key)
                decoded_pairs.append((key, _read_token(item, node_count)))
            records.append(("dict", decoded_pairs))
        else:
            raise ValueError("invalid graph node kind")

    root = _read_token(parsed["root"], node_count)
    _validate_acyclic_and_reachable(root, records)

    containers: list[Any] = [
        [] if kind == "list" else {} for kind, _content in records
    ]
    for index, (kind, content) in enumerate(records):
        if kind == "list":
            containers[index].extend(
                containers[item.index] if isinstance(item, _Reference) else item
                for item in content
            )
        else:
            for key, item in content:
                containers[index][key] = (
                    containers[item.index] if isinstance(item, _Reference) else item
                )

    return containers[root.index] if isinstance(root, _Reference) else root
