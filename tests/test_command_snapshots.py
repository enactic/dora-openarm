"""Focused snapshot contract checks with mocked Dora and driver instances."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
import pytest
import dora_openarm.main as arm_node


@pytest.fixture
def runtime(monkeypatch):
    """Run the real node loop without creating a CAN connection."""
    arm, node = MagicMock(), MagicMock()
    arm.last_command = np.zeros(8)
    arm.last_command_dispatch_timestamp_ns = None
    arm.fetch_position.return_value = np.zeros(8)
    arm.fetch_state.return_value = {
        key: np.zeros(8) for key in ("qpos", "qvel", "qtorque", "tmos", "trotor")
    }
    arm.get_health.return_value = (["ENABLED"] * 8, {})
    arm.start.return_value = True
    arm.send_position.return_value = True

    def send_position(position):
        if arm.send_position.return_value:
            arm.last_command = np.clip(position, -1, 1)
            arm.last_command_dispatch_timestamp_ns = 200
        return arm.send_position.return_value

    arm.send_position.side_effect = send_position
    monkeypatch.setattr(arm_node.dora, "Node", lambda: node)
    monkeypatch.setattr(arm_node.openarm_driver, "Config", lambda _: None)
    monkeypatch.setattr(
        arm_node.openarm_driver, "SingleArmDriver", lambda *args, **kwargs: arm
    )

    def run(events, *, auto=True, align=False):
        argv = ["dora-openarm", "--align" if align else "--no-align"]
        if auto:
            argv.append("--start-on-startup")
        monkeypatch.setattr("sys.argv", argv)
        node.__iter__.return_value = events
        arm_node.main()
        return [call.args for call in node.send_output.call_args_list]

    return SimpleNamespace(arm=arm, run=run)


def _event(name, value=None, **metadata):
    return {
        "type": "INPUT",
        "id": name,
        "value": (
            pa.array([{"qpos": value}], type=arm_node.QPOS_TYPE)
            if name == "move_position"
            else pa.array(value or [])
        ),
        "metadata": metadata,
    }


def test_publish_tick_preserves_legacy_requests_and_accepted_metadata(runtime):
    """A tick reads state once; rejection cannot replace accepted provenance."""
    metadata = {"timestamp": 10, "chunk_id": "A", "blended_chunk_id": "previous"}

    def events():
        yield _event("request_state", timestamp=1)
        yield _event("request_position", timestamp=2)
        yield _event("move_position", [2.0] * 8, **metadata)
        yield _event("publish_tick", timestamp=3)
        runtime.arm.send_position.return_value = False
        yield _event("move_position", [3.0] * 8, timestamp=11, chunk_id="B")
        yield _event("publish_tick", timestamp=4)
        yield _event("command", ["stop"])
        yield _event("publish_tick")

    outputs = runtime.run(events())
    assert [out[0] for out in outputs] == [
        "status",
        "state",
        "position",
        "state",
        "latest_command",
        "state",
        "latest_command",
        "status",
    ]
    assert runtime.arm.fetch_state.call_count == 3
    assert runtime.arm.fetch_position.call_count == 1
    commands = [out for out in outputs if out[0] == "latest_command"]
    assert commands[0][1].to_pylist() == [{"qpos": [1.0] * 8}]
    assert (
        commands[0][2]
        == commands[1][2]
        == {
            **metadata,
            "executed_timestamp": 200,
            "start_epoch": 1,
        }
    )
    assert metadata == {
        "timestamp": 10,
        "chunk_id": "A",
        "blended_chunk_id": "previous",
    }
    assert all(
        "observation_timestamp" in out[2] and "timestamp" not in out[2]
        for out in outputs
        if out[0] in {"state", "position"}
    )


@pytest.mark.parametrize("accepted", [True, False])
def test_alignment_uses_the_acceptance_and_cache_path(runtime, accepted):
    """Do not publish aligned or replace command metadata for a rejected target."""
    runtime.arm.send_position.return_value = accepted
    outputs = runtime.run(
        [
            _event("move_position", [0.0] * 8, timestamp=10),
            _event("publish_tick"),
        ],
        align=True,
    )
    assert runtime.arm.send_position.call_count == 1
    assert (
        any(out[0] == "status" and out[1].to_pylist() == ["aligned"] for out in outputs)
        == accepted
    )
    assert any(out[0] == "latest_command" for out in outputs) == accepted


@pytest.mark.parametrize("auto", [True, False])
@pytest.mark.parametrize(
    "first_ok,restart_ok", [(True, True), (False, True), (True, False)]
)
def test_startup_and_restart_cache(runtime, auto, first_ok, restart_ok):
    """Only successful starts advance the epoch and expose startup commands."""
    starts = iter(((100, first_ok), (None if restart_ok else 200, restart_ok)))

    def start():
        timestamp, accepted = next(starts)
        runtime.arm.last_command_dispatch_timestamp_ns = timestamp
        return accepted

    runtime.arm.start.side_effect = start
    events = [] if auto else [_event("command", ["start"])]
    events += [
        _event("publish_tick"),
        _event("command", ["stop"]),
        _event("command", ["start"]),
        _event("publish_tick"),
    ]
    outputs = runtime.run(events, auto=auto)
    commands = [out for out in outputs if out[0] == "latest_command"]
    assert len(commands) == int(first_ok)
    if first_ok:
        assert commands[0][2] == {
            "timestamp": 100,
            "executed_timestamp": 100,
            "start_epoch": 1,
        }
    epochs = np.cumsum([first_ok, restart_ok]).tolist()
    statuses = [out for out in outputs if out[0] == "status"]
    for out, accepted, epoch in zip(
        [statuses[0 if auto else 1], statuses[-1]], [first_ok, restart_ok], epochs
    ):
        assert out[1].to_pylist() == ["started" if accepted else "stopped"]
        assert out[2]["start_epoch"] == epoch
    assert [out[2]["start_epoch"] for out in outputs if out[0] == "state"] == [
        epoch for epoch, accepted in zip(epochs, [first_ok, restart_ok]) if accepted
    ]


@pytest.mark.parametrize(
    "value",
    [
        pa.array([{"qpos": [0.25] * 8}]),
        pa.array([{"new_position": [0.25] * 8}]),
        pa.StructArray.from_arrays([pa.array([0.25] * 8)], names=["new_position"]),
        pa.array([0.25] * 8),
    ],
)
def test_position_payload_compatibility(runtime, value):
    """Canonical, legacy struct, and flat inputs reach the same command path."""
    event = _event("move_position", [0.25] * 8)
    event["value"] = value
    outputs = runtime.run([event, _event("publish_tick")])
    runtime.arm.send_position.assert_called_once()
    commands = [out for out in outputs if out[0] == "latest_command"]
    assert len(commands) == 1
    assert commands[0][1].to_pylist() == [{"qpos": [0.25] * 8}]
