"""Tabular Q-learning agents registered under ``algorithm=qlearning``."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ..observations import ACTIONS, CONTRACT_DIMS, contract_dim
from ..persistence import (
    LearningUnavailable,
    _read_checkpoint_metadata,
    _read_json_bytes,
    _require_resume_identity,
    _resume_json,
    _resume_unjson,
    _verify_checkpoint_metadata,
    _verify_resume_bundle,
    _write_resume_manifest,
)
from ..registry import register_agent


@register_agent("qlearning", scope="shared")
class TabularQLearning:
    """Tabular Q-learning baseline — migration of the legacy M1 QLearning
    class (SimulationRL.py:5682) onto the V2 learning contract.

    Semantics kept from the legacy implementation (line numbers cited):
    - Q values are initialized uniform in [0, 1) (__init__, 5703-5704;
      createQTable at 10238 is dead code and is NOT the reference);
    - exploration picks uniformly among LEGAL actions, exploitation argmaxes
      over legal actions only (5758-5769; the legacy code masks unavailable
      directions to -inf, which is argmax-equivalent to the legal set);
    - non-terminal update Q <- (1-alpha)*Q + alpha*(r + gamma*max_a' Q(s',a'))
      over legal next actions (5791-5794);
    - a terminal transition writes the terminal reward DIRECTLY into
      Q(s, a) (5743);
    - alpha default 0.25 (558), gamma default 0.99 (274).

    Deliberate contract adaptations (documented, not drift):
    - the legacy discrete 5-tuple state (getState, 9443) has no V2 analog;
      table keys are the exact float64 bytes of the V2 contract observation;
    - epsilon follows the shared V2 schedule (epsilon_start/end/decay_s),
      not the legacy LAMBDA/decayRate/GT^2 formula (5810);
    - rewards are the V2 kernel's M1 queue reward + arrive_reward (task-1
      baseline); the legacy QLearning distance reward V1 (5783) is excluded
      from v1 per plan (ANALYSIS/LEO-V2-ORIGINAL-PLAN.md:86).
    Pure numpy: no TensorFlow required.
    """

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract not in CONTRACT_DIMS:
            raise ValueError(f"unknown learning contract {contract!r}")
        self.contract = contract
        self.include_last_hop = bool(cfg.get("include_last_hop", False))
        self.input_dim = contract_dim(contract, self.include_last_hop)
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.mode = cfg["mode"]
        self.alpha = float(cfg.get("qlearning_alpha", 0.25))
        self.gamma = float(cfg["gamma"])
        self.table: dict[bytes, np.ndarray] = {}
        checkpoint = cfg.get("checkpoint_path")
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        if checkpoint:
            path = Path(checkpoint)
            if not path.is_file():
                raise LearningUnavailable(f"Q-learning checkpoint not found: {path}")
            data = path.read_bytes()
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha != cfg.get("checkpoint_sha256"):
                raise LearningUnavailable(
                    "Q-learning checkpoint SHA-256 differs from resolved config")
            payload = _read_json_bytes(data, "Q-learning checkpoint")
            if not isinstance(payload, dict):
                raise LearningUnavailable(
                    "Q-learning checkpoint payload is not a mapping")
            if payload.get("schema") != "leo-sim-qlearning-table/v1":
                raise LearningUnavailable(
                    "Q-learning checkpoint schema "
                    f"{payload.get('schema')!r} != "
                    "'leo-sim-qlearning-table/v1'")
            canonical_keys = {"schema", "contract", "entries"}
            legacy_keys = {"schema", "entries"}
            if set(payload) not in (canonical_keys, legacy_keys):
                raise LearningUnavailable(
                    "Q-learning checkpoint payload has unknown top-level "
                    f"fields or misses required ones: {sorted(payload)}")
            loaded_meta_sha = None
            if "contract" not in payload:
                # Legacy v1 tables have no contract field in the payload.
                # Migrate only when the sibling metadata.json independently
                # binds contract + filename + SHA and is itself pinned in the
                # resolved config; otherwise fail closed (never accept an
                # unverifiable provenance).
                meta_path = path.parent / "metadata.json"
                if not meta_path.is_file() or meta_path.is_symlink():
                    raise LearningUnavailable(
                        "Q-learning checkpoint contract mismatch: payload "
                        "has no contract field, resolved config "
                        f"wants {self.contract!r}")
                meta, loaded_meta_sha = _read_checkpoint_metadata(
                    meta_path, cfg.get("checkpoint_metadata_sha256"),
                    "Q-learning checkpoint metadata.json")
                _verify_checkpoint_metadata(
                    meta, self.contract, path.name, actual_sha,
                    "leo-sim-qlearning/v1", "qlearning")
            elif payload["contract"] != self.contract:
                raise LearningUnavailable(
                    "Q-learning checkpoint contract mismatch: payload "
                    f"says {payload['contract']!r}, resolved config "
                    f"wants {self.contract!r}")
            elif cfg.get("checkpoint_metadata_sha256") is not None:
                # Canonical table with an explicit metadata pin: verify and
                # record it so loader/receipt provenance semantics stay
                # consistent (a configured pin must never be ignored).
                meta_path = path.parent / "metadata.json"
                meta, loaded_meta_sha = _read_checkpoint_metadata(
                    meta_path, cfg.get("checkpoint_metadata_sha256"),
                    "Q-learning checkpoint metadata.json")
                _verify_checkpoint_metadata(
                    meta, self.contract, path.name, actual_sha,
                    "leo-sim-qlearning/v1", "qlearning")
            entries = payload.get("entries")
            if not isinstance(entries, list):
                raise LearningUnavailable("Q-learning checkpoint lacks entries")
            expected_key_bytes = self.input_dim * 8
            expected_key_dims = self.input_dim
            seen_keys = set()
            for entry in entries:
                if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                        or not isinstance(entry[0], str)):
                    raise LearningUnavailable(
                        "Q-learning checkpoint entry must be "
                        "[key_hex, values]")
                try:
                    key = bytes.fromhex(entry[0])
                except ValueError as exc:
                    raise LearningUnavailable(
                        "Q-learning checkpoint state key is not valid hex: "
                        f"{exc}") from exc
                if len(key) != expected_key_bytes:
                    raise LearningUnavailable(
                        "Q-learning checkpoint state key width "
                        f"{len(key)} bytes != contract {self.contract} "
                        f"observation width {expected_key_bytes}")
                key_view = np.frombuffer(key, dtype="<f8")
                if key_view.shape != (expected_key_dims,) \
                        or not np.all(np.isfinite(key_view)):
                    # _key() always serializes a finite observation vector;
                    # an unreachable representation (NaN/Inf/extra dims)
                    # would silently miss every lookup and degrade to the
                    # zero-row fallback.
                    raise LearningUnavailable(
                        "Q-learning checkpoint state key is not a finite "
                        f"float64 {expected_key_dims}-dim observation")
                if key in seen_keys:
                    raise LearningUnavailable(
                        "Q-learning checkpoint contains duplicate state keys")
                seen_keys.add(key)
                try:
                    arr = np.asarray(entry[1], dtype=np.float64)
                except (TypeError, ValueError) as exc:
                    raise LearningUnavailable(
                        f"Q-learning checkpoint row unreadable: {exc}") from exc
                if arr.shape != (len(ACTIONS),) \
                        or not np.all(np.isfinite(arr)):
                    raise LearningUnavailable(
                        "Q-learning checkpoint row width or finiteness "
                        "mismatch")
                self.table[key] = arr
            self.loaded_checkpoint = str(path.resolve())
            self.loaded_checkpoint_sha256 = actual_sha
            self.loaded_checkpoint_metadata_sha256 = loaded_meta_sha
        else:
            self.loaded_checkpoint = None
            self.loaded_checkpoint_sha256 = None
            self.loaded_checkpoint_metadata_sha256 = None
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0  # tabular: one Q-table update per train step
        self._resume_time_offset_s = 0.0
        self._last_now = 0.0
        if cfg.get("resume_path") is not None:
            self._load_resume_bundle(
                Path(cfg["resume_path"]), cfg.get("resume_sha256"))

    @staticmethod
    def _key(observation) -> bytes:
        # Canonical little-endian float64 bytes: identical on LE hosts and
        # portable across endianness, so a checkpoint's key representation is
        # schema-bound (N3-STATE-KEY-ENDIAN).
        return np.ascontiguousarray(
            np.asarray(observation, dtype=np.float64).astype("<f8"),
        ).tobytes()

    def _row(self, observation) -> np.ndarray:
        key = self._key(observation)
        row = self.table.get(key)
        if row is None:
            if self.mode == "eval":
                # Eval must evaluate a fixed policy: an unseen state gets a
                # deterministic all-zero row (argmax = first legal action),
                # never a random-init row, and the table is not mutated.
                return np.zeros(len(ACTIONS))
            # legacy init: np.random.rand per (state, action) (5703-5704)
            row = self.rng.random(len(ACTIONS))
            self.table[key] = row
        return row

    @staticmethod
    def _legal(mask: dict) -> np.ndarray:
        legal = np.flatnonzero([bool(mask.get(a, False)) for a in ACTIONS])
        if not len(legal):
            raise ValueError("Q-learning decision requires at least one legal action")
        return legal

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        legal = self._legal(mask)
        self._last_now = max(self._last_now, float(now))
        if self.mode != "eval" and self.rng.random() < self.epsilon(now):
            chosen = int(self.rng.choice(legal))
        else:
            row = self._row(observation)
            chosen = int(legal[np.argmax(row[legal])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown Q-learning action {action!r}")
        row = self._row(state)
        idx = ACTIONS.index(action)
        if self.mode == "train":
            if done:
                # legacy: terminal writes the reward directly (5743)
                row[idx] = float(reward)
            else:
                next_row = self._row(next_state)
                legal_next = self._legal(next_mask)
                max_next = float(np.max(next_row[legal_next]))
                row[idx] = ((1.0 - self.alpha) * row[idx]
                            + self.alpha * (float(reward) + self.gamma * max_next))
            self.train_steps += 1
        self.transitions += 1

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        table_path = out / "q_table.json"
        resume = self.save_resume_and_verify(out / "resume")
        # F4-RUNTIME-STATE-KEY: save must never produce an artifact that the
        # eval loader would reject.  Reuse the loader's exact semantic
        # contract: canonical key width, finite little-endian float64 keys,
        # and finite 5-action rows.
        expected_key_bytes = self.input_dim * 8
        expected_key_dims = self.input_dim
        for key, row in self.table.items():
            if len(key) != expected_key_bytes:
                raise LearningUnavailable(
                    "cannot save Q-learning checkpoint: state key width "
                    f"{len(key)} != contract observation width "
                    f"{expected_key_bytes}")
            key_view = np.frombuffer(key, dtype="<f8")
            if key_view.shape != (expected_key_dims,) \
                    or not np.all(np.isfinite(key_view)):
                raise LearningUnavailable(
                    "cannot save Q-learning checkpoint: state key is not a "
                    f"finite float64 {expected_key_dims}-dim observation")
            if row.shape != (len(ACTIONS),) \
                    or not np.all(np.isfinite(row)):
                raise LearningUnavailable(
                    "cannot save Q-learning checkpoint: Q row width or "
                    "finiteness mismatch")
        payload = {
            "schema": "leo-sim-qlearning-table/v1",
            "contract": self.contract,
            "entries": [[key.hex(), [float(v) for v in row]]
                        for key, row in sorted(self.table.items())],
        }
        table_path.write_text(json.dumps(payload, sort_keys=True) + "\n",
                              encoding="utf-8")
        # F1-RESIDUAL: hash and parse the SAME bytes (single read) so the
        # verification step has no hash-then-reopen window on the save side.
        data = table_path.read_bytes()
        checkpoint_sha = hashlib.sha256(data).hexdigest()
        loaded = _read_json_bytes(data, "Q-learning checkpoint")
        verified = loaded == payload
        metadata = self.diagnostics()
        metadata.update({
            "schema": "leo-sim-qlearning/v1",
            "checkpoint": table_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified,
            **resume,
        })
        (out / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if not verified:
            raise LearningUnavailable(
                "saved Q-learning checkpoint failed reload verification")
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / "metadata.json").read_bytes()).hexdigest()
        return result

    def _q_resume_identity(self) -> dict:
        ident = {
            "algorithm": "qlearning",
            "contract": self.contract,
            "seed": self.seed,
            "gamma": self.gamma,
            "alpha": self.alpha,
        }
        if self.include_last_hop:
            ident["include_last_hop"] = True
        return ident

    def _load_resume_bundle(self, root: Path, expected_sha: str) -> None:
        if not isinstance(expected_sha, str):
            raise LearningUnavailable("resume_sha256 is required for exact resume")
        manifest = _verify_resume_bundle(
            root, expected_sha, "leo-sim-qlearning-resume/v1")
        _require_resume_identity(manifest, self._q_resume_identity())
        if "q_table.json" not in manifest["files"] or "state.json" not in manifest["files"]:
            raise LearningUnavailable("Q-learning resume bundle is incomplete")
        payload = _read_json_bytes(
            (root / "q_table.json").read_bytes(), "Q-learning resume table")
        if payload.get("schema") != "leo-sim-qlearning-table/v1" \
                or payload.get("contract") != self.contract:
            raise LearningUnavailable("Q-learning resume table contract mismatch")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise LearningUnavailable("Q-learning resume table lacks entries")
        expected_key_bytes = self.input_dim * 8
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise LearningUnavailable("Q-learning resume table entry is invalid")
            try:
                key = bytes.fromhex(entry[0])
                values = np.asarray(entry[1], dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise LearningUnavailable(
                    f"Q-learning resume table entry is unreadable: {exc}") from exc
            if (len(key) != expected_key_bytes
                    or values.shape != (len(ACTIONS),)
                    or not np.all(np.isfinite(values))):
                raise LearningUnavailable("Q-learning resume table shape mismatch")
            self.table[key] = values
        state = _read_json_bytes(
            (root / "state.json").read_bytes(), "Q-learning resume state")
        try:
            self.rng.bit_generator.state = _resume_unjson(state["numpy_rng_state"])
            counters = state["counters"]
            self.decisions = int(counters["decisions"])
            self.transitions = int(counters["transitions"])
            self.train_steps = int(counters["train_steps"])
            self._resume_time_offset_s = float(state.get("elapsed_time_s", 0.0))
            if min(self.decisions, self.transitions, self.train_steps) < 0 \
                    or not math.isfinite(self._resume_time_offset_s) \
                    or self._resume_time_offset_s < 0:
                raise ValueError("negative or non-finite training counter/time")
        except (KeyError, TypeError, ValueError) as exc:
            raise LearningUnavailable(f"Q-learning resume state is invalid: {exc}") from exc
        self.loaded_resume_path = str(root.resolve())
        self.loaded_resume_sha256 = expected_sha
        self.resume_manifest_sha256 = expected_sha
        self.resume_verified = True

    def save_resume_and_verify(self, directory: str | Path) -> dict:
        """Save the complete tabular continuation state and its hash manifest."""
        out = Path(directory)
        if out.is_symlink():
            raise LearningUnavailable("resume output may not be symbolic")
        out.mkdir(parents=True, exist_ok=True)
        if any(out.iterdir()):
            raise LearningUnavailable("resume output directory must be empty")
        payload = {
            "schema": "leo-sim-qlearning-table/v1",
            "contract": self.contract,
            "entries": [[key.hex(), [float(v) for v in row]]
                        for key, row in sorted(self.table.items())],
        }
        (out / "q_table.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        state = {
            "numpy_rng_state": _resume_json(self.rng.bit_generator.state),
            "elapsed_time_s": float(self._resume_time_offset_s + self._last_now),
            "counters": {
                "decisions": int(self.decisions),
                "transitions": int(self.transitions),
                "train_steps": int(self.train_steps),
            },
        }
        (out / "state.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sha = _write_resume_manifest(
            out, "leo-sim-qlearning-resume/v1", self._q_resume_identity())
        verified = _verify_resume_bundle(
            out, sha, "leo-sim-qlearning-resume/v1")
        self.resume_manifest_sha256 = sha
        self.resume_verified = True
        return {
            "resume_path": str(out),
            "resume_sha256": sha,
            "resume_verified": bool(verified),
        }

    def diagnostics(self) -> dict:
        return {
            "algorithm": "qlearning",
            "contract": self.contract,
            "mode": self.mode,
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": self.resume_verified,
            "actions": list(ACTIONS),
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "table_size": len(self.table),
            "seed": self.seed,
            "agent_scope": str(self.cfg.get("agent_scope", "shared")),
            "include_last_hop": self.include_last_hop,
        }


@register_agent("qlearning", scope="per_satellite")
class PerSatelliteTabularQLearning:
    """Independent per-satellite tabular Q-learning (no Federated sync)."""

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract not in CONTRACT_DIMS:
            raise ValueError(f"unknown learning contract {contract!r}")
        self.contract = contract
        self.include_last_hop = bool(cfg.get("include_last_hop", False))
        self.input_dim = contract_dim(contract, self.include_last_hop)
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.mode = cfg["mode"]
        self.alpha = float(cfg.get("qlearning_alpha", 0.25))
        self.gamma = float(cfg["gamma"])
        self.tables: dict[int, dict[bytes, np.ndarray]] = {}
        self.rngs: dict[int, np.random.Generator] = {}
        self.loaded_checkpoint = None
        self.loaded_checkpoint_sha256 = None
        self.loaded_checkpoint_metadata_sha256 = None
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self._resume_time_offset_s = 0.0
        self._last_now = 0.0

    @property
    def table_size(self) -> int:
        return sum(len(tbl) for tbl in self.tables.values())

    def _agent(self, sat_id: int) -> tuple[dict[bytes, np.ndarray], np.random.Generator]:
        sid = int(sat_id)
        if sid < 0:
            raise ValueError(f"satellite id must be non-negative, got {sid}")
        tbl = self.tables.get(sid)
        if tbl is None:
            tbl = {}
            self.tables[sid] = tbl
            self.rngs[sid] = np.random.default_rng(
                np.random.SeedSequence([self.seed, sid]))
        return tbl, self.rngs[sid]

    def _row(self, sat_id: int, observation) -> np.ndarray:
        tbl, rng = self._agent(sat_id)
        key = TabularQLearning._key(observation)
        row = tbl.get(key)
        if row is None:
            if self.mode == "eval":
                return np.zeros(len(ACTIONS))
            row = rng.random(len(ACTIONS))
            tbl[key] = row
        return row

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def choose_for(self, sat_id: int, observation: np.ndarray,
                   mask: dict, now: float) -> str:
        legal = TabularQLearning._legal(mask)
        _tbl, rng = self._agent(sat_id)
        self._last_now = max(self._last_now, float(now))
        if self.mode != "eval" and rng.random() < self.epsilon(now):
            chosen = int(rng.choice(legal))
        else:
            row = self._row(sat_id, observation)
            chosen = int(legal[np.argmax(row[legal])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember_for(self, sat_id: int, state, action: str, reward: float,
                     next_state, next_mask: dict, done: bool) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown Q-learning action {action!r}")
        row = self._row(sat_id, state)
        idx = ACTIONS.index(action)
        if self.mode == "train":
            if done:
                row[idx] = float(reward)
            else:
                next_row = self._row(sat_id, next_state)
                legal_next = TabularQLearning._legal(next_mask)
                max_next = float(np.max(next_row[legal_next]))
                row[idx] = ((1.0 - self.alpha) * row[idx]
                            + self.alpha * (float(reward) + self.gamma * max_next))
            self.train_steps += 1
        self.transitions += 1

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        return self.choose_for(0, observation, mask, now)

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        self.remember_for(0, state, action, reward, next_state, next_mask, done)

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        expected_key_bytes = self.input_dim * 8
        expected_key_dims = self.input_dim
        for tbl in self.tables.values():
            for key, row in tbl.items():
                if len(key) != expected_key_bytes:
                    raise LearningUnavailable(
                        "cannot save Q-learning checkpoint: state key width "
                        f"{len(key)} != contract observation width "
                        f"{expected_key_bytes}")
                key_view = np.frombuffer(key, dtype="<f8")
                if key_view.shape != (expected_key_dims,) \
                        or not np.all(np.isfinite(key_view)):
                    raise LearningUnavailable(
                        "cannot save Q-learning checkpoint: state key is not a "
                        f"finite float64 {expected_key_dims}-dim observation")
                if row.shape != (len(ACTIONS),) or not np.all(np.isfinite(row)):
                    raise LearningUnavailable(
                        "cannot save Q-learning checkpoint: Q row width or "
                        "finiteness mismatch")

        rep_sat = min(self.tables) if self.tables else None
        rep_tbl = self.tables[rep_sat] if rep_sat is not None else {}
        payload = {
            "schema": "leo-sim-qlearning-table/v1",
            "contract": self.contract,
            "entries": [[key.hex(), [float(v) for v in row]]
                        for key, row in sorted(rep_tbl.items())],
        }
        table_path = out / "q_table.json"
        table_path.write_text(json.dumps(payload, sort_keys=True) + "\n",
                              encoding="utf-8")
        data = table_path.read_bytes()
        checkpoint_sha = hashlib.sha256(data).hexdigest()
        loaded = _read_json_bytes(data, "Q-learning checkpoint")
        verified = loaded == payload

        all_payload = {
            "schema": "leo-sim-per-sat-qlearning-tables/v1",
            "contract": self.contract,
            "include_last_hop": self.include_last_hop,
            "satellites": {
                str(sid): [[key.hex(), [float(v) for v in row]]
                           for key, row in sorted(tbl.items())]
                for sid, tbl in sorted(self.tables.items())
            },
        }
        all_path = out / "per_satellite_q_tables.json"
        all_path.write_text(json.dumps(all_payload, sort_keys=True) + "\n",
                            encoding="utf-8")
        all_data = all_path.read_bytes()
        all_sha = hashlib.sha256(all_data).hexdigest()
        all_loaded = _read_json_bytes(all_data, "Per-satellite Q-learning tables")
        all_verified = all_loaded == all_payload

        metadata = self.diagnostics()
        metadata.update({
            "schema": "leo-sim-qlearning/v1",
            "checkpoint": table_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified and all_verified,
            "representative_sat": rep_sat,
            "per_satellite_q_tables": all_path.name,
            "per_satellite_q_tables_sha256": all_sha,
            "per_satellite_verified": all_verified,
        })
        (out / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if not (verified and all_verified):
            raise LearningUnavailable(
                "saved per-satellite Q-learning checkpoint failed verification")
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / "metadata.json").read_bytes()).hexdigest()
        return result

    def diagnostics(self) -> dict:
        return {
            "algorithm": "qlearning",
            "contract": self.contract,
            "mode": self.mode,
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": self.resume_verified,
            "actions": list(ACTIONS),
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "table_size": self.table_size,
            "seed": self.seed,
            "agent_scope": "per_satellite",
            "n_agents": len(self.tables),
            "include_last_hop": self.include_last_hop,
        }


__all__ = ["TabularQLearning", "PerSatelliteTabularQLearning"]
