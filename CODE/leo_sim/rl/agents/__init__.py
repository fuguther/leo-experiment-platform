"""Built-in RL agent plugins."""
from .ddqn import PerSatelliteTensorflowDDQN, TensorflowDDQN
from .qlearning import PerSatelliteTabularQLearning, TabularQLearning

__all__ = [
    "TensorflowDDQN", "PerSatelliteTensorflowDDQN",
    "TabularQLearning", "PerSatelliteTabularQLearning",
]
