from types import SimpleNamespace

from CODE.leo_sim.kernel import Kernel


class _Link:
    def __init__(self, peer, *, room=True, data_bits=0, ctrl_bits=0):
        self.peer = peer
        self.gen = 0
        self._room = room
        self.data_bits = data_bits
        self.ctrl_bits = ctrl_bits

    def room(self, bits):
        return self._room


def _kernel_for_audit():
    kernel = object.__new__(Kernel)
    kernel.cfg_links = {"geometry_loss": False, "isl_queue_bits": 1000}
    kernel.rate_model = "constant"
    kernel.topo = {0: {"N": 1, "E": 2, "S": 3, "W": 4}}
    kernel.isls = [{
        "N": _Link(1), "E": _Link(2), "S": _Link(3),
        "W": _Link(4, room=False, data_bits=1000),
    }]
    kernel._link_rate = lambda kind, now, sat, peer=None: (
        0.0 if peer == 2 else 5_000_000.0)
    return kernel


def test_four_direction_audit_preserves_platform_masks_and_reasons():
    kernel = _kernel_for_audit()
    packet = SimpleNamespace(bits=100, path=[0, 3])

    audit = kernel._four_direction_audit(
        packet, 0, 1.25,
        route_candidates=["N", "E", "S", "W"],
        loop_free_candidates=["N", "E", "W"],
        legal=["N"], route_status="ok", kind="forward")

    assert audit["direction_order"] == ["N", "E", "S", "W"]
    assert audit["four_direction_peer_map"] == {
        "N": 1, "E": 2, "S": 3, "W": 4}
    assert audit["route_candidate_mask"] == {
        "N": True, "E": True, "S": True, "W": True}
    assert audit["physical_legal_mask"] == {
        "N": True, "E": False, "S": True, "W": True}
    assert audit["final_legal_mask"] == {
        "N": True, "E": False, "S": False, "W": False}
    assert audit["final_mask_matches_committed_set"] is True
    assert "loop_to_visited_satellite" in audit[
        "filter_reason_by_direction"]["S"]
    assert "zero_or_unavailable_rate" in audit[
        "filter_reason_by_direction"]["E"]
    assert "insufficient_queue_room" in audit[
        "filter_reason_by_direction"]["W"]


def test_queue_state_trace_contains_exact_resource_counts():
    kernel = object.__new__(Kernel)
    kernel.timeline_sink = []
    kernel.env = SimpleNamespace(now=3.5)
    link = _Link(7, data_bits=300, ctrl_bits=50)
    link.sat = 2
    link.dir = "E"
    link.retired = False
    link.current = None
    link.data_q = [1, 2]
    link.ctrl_q = [3]
    link._svc_phase = None

    kernel._record_isl_queue_state(link, "enqueue_data", pid=10)

    assert kernel.timeline_sink == [{
        "milestone": "queue_state", "at": 3.5,
        "event": "enqueue_data", "pid": None, "trigger_pid": 10,
        "control_iid": None, "resource_id": "isl:2:7",
        "resource_kind": "isl_egress", "sat": 2, "direction": "E",
        "peer": 7, "generation": 0, "retired": False,
        "queued_data_bits": 300, "queued_control_bits": 50,
        "queued_bits": 350, "queued_data_packets": 2,
        "queued_control_packets": 1, "in_service": False,
        "in_service_kind": None, "in_service_pid": None,
        "in_service_control_iid": None, "in_service_bits": 0,
        "in_service_phase": None, "outcome": None,
    }]
