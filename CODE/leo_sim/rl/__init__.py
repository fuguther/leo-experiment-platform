"""Pluggable learning runtime for leo_sim."""
from __future__ import annotations

from .agents import (
    PerSatelliteTabularQLearning,
    PerSatelliteTensorflowDDQN,
    TabularQLearning,
    TensorflowDDQN,
)
from .models import (
    _configure_tf_threads,
    _effective_cpu_count,
    _extract_dense_mlp,
    _graph_custom_objects,
    _numpy_mlp_forward,
    build_model,
    default_model_name,
    require_tensorflow,
    tf,
    V2GraphEncoder,
)
from .observations import (
    _DEST_DIST_NORM_KM,
    _EARTH_R_KM,
    _bfs_first_dirs,
    _graph_node_features,
    _origin_features,
    _peer_bit_value,
    ACTIONS,
    C1_DIM,
    C1_MAX_NEIGHBORS,
    C3_DIM,
    CONTRACTS,
    CONTRACT_ALIASES,
    CONTRACT_DIMS,
    DEST_FEATURES,
    FLAT_MEAN_DIM,
    GRAPH_CONTRACTS,
    GRAPH_CONTRACT_DIMS,
    GRAPH_DIRS,
    GRAPH_MAX_NODES,
    GRAPH_NODE_FEAT_DIM,
    LAST_HOP_FEATURES,
    NEIGHBOR_1HOP_DIM,
    NEIGHBOR_1HOP_MAX_NEIGHBORS,
    ORIGIN_FEATURES,
    OWN_FEATURES,
    build_action_mask,
    build_graph_observation,
    build_observation,
    canonical_contract,
    contract_dim,
    ddqn_targets,
    destination_features,
    forward_reward,
    graph_state_dim,
    information_set,
    last_hop_features,
    own_state,
    queue_reward,
)
from .persistence import (
    LearningUnavailable,
    _read_checkpoint_metadata,
    _read_json_bytes,
    _require_resume_identity,
    _resume_file_record,
    _resume_identity,
    _resume_json,
    _resume_unjson,
    _verify_checkpoint_metadata,
    _verify_resume_bundle,
    _write_resume_manifest,
)
from .registry import (
    get_agent_class,
    get_model_builder,
    get_observation_builder,
    register_agent,
    register_model,
    register_observation,
    registered_agents,
    registered_models,
    registered_observations,
)
from .replay import _ReplayBuffer


def create_learner(contract: str, learning_cfg: dict, seed: int):
    """Construct the registered learner selected by algorithm and agent scope."""
    if not isinstance(learning_cfg, dict):
        raise TypeError("learning_cfg must be a mapping")
    cfg = dict(learning_cfg)
    algorithm = cfg.get("algorithm")
    if algorithm in (None, "none"):
        return None
    scope = cfg.get("agent_scope", "shared")
    learner_cls = get_agent_class(str(algorithm), scope=str(scope))
    return learner_cls(contract, cfg, int(seed))


__all__ = [
    # Plugin API and factory.
    "create_learner",
    "register_observation", "register_model", "register_agent",
    "get_observation_builder", "get_model_builder", "get_agent_class",
    "registered_observations", "registered_models", "registered_agents",
    "build_model", "default_model_name",
    # Runtime gate and models.
    "tf", "require_tensorflow", "V2GraphEncoder", "_graph_custom_objects",
    "_effective_cpu_count", "_configure_tf_threads", "_extract_dense_mlp",
    "_numpy_mlp_forward",
    # Built-in learners.
    "TensorflowDDQN", "PerSatelliteTensorflowDDQN",
    "TabularQLearning", "PerSatelliteTabularQLearning",
    # Failure and persistence contracts.
    "LearningUnavailable", "_ReplayBuffer",
    "_verify_checkpoint_metadata", "_read_json_bytes",
    "_read_checkpoint_metadata", "_resume_json", "_resume_unjson",
    "_resume_file_record", "_write_resume_manifest",
    "_verify_resume_bundle", "_resume_identity", "_require_resume_identity",
    # Observation/action contracts.
    "_DEST_DIST_NORM_KM", "_EARTH_R_KM", "_peer_bit_value",
    "_origin_features", "_bfs_first_dirs", "_graph_node_features",
    "CONTRACTS", "CONTRACT_ALIASES", "canonical_contract",
    "ORIGIN_FEATURES", "OWN_FEATURES", "DEST_FEATURES",
    "FLAT_MEAN_DIM", "NEIGHBOR_1HOP_MAX_NEIGHBORS", "NEIGHBOR_1HOP_DIM",
    "C3_DIM", "C1_MAX_NEIGHBORS", "C1_DIM",
    "ACTIONS", "GRAPH_MAX_NODES", "GRAPH_NODE_FEAT_DIM", "GRAPH_DIRS",
    "GRAPH_CONTRACTS", "graph_state_dim", "GRAPH_CONTRACT_DIMS",
    "CONTRACT_DIMS", "LAST_HOP_FEATURES", "contract_dim",
    "last_hop_features", "queue_reward", "forward_reward", "own_state",
    "destination_features", "information_set", "build_graph_observation",
    "build_observation", "build_action_mask", "ddqn_targets",
]
