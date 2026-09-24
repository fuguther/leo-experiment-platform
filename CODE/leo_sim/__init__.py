"""leo_sim: formal LEO simulation platform V2 runtime.

Formal data path:
immutable demand trace -> sparse geographic TrafficEndpoint -> finite satellite
access service -> satellite ingress -> ISL routing -> local destination
visibility discovery -> finite downlink -> destination TrafficEndpoint.

The Gateway concept from the legacy runtime does not exist in this package.
"""

__version__ = "2.0.0"
