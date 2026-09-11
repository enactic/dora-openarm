# Copyright 2026 Enactic, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Node to control OpenArm."""

import argparse
import dataclasses
import enum
import dora
import openarm_can
import openarm_driver
import os
import pathlib
import time
import pyarrow as pa
import numpy as np


class ArmStatus(str, enum.Enum):
    """Arm control states."""

    STOPPED = "stopped"
    STARTED = "started"
    ALIGNED = "aligned"


@dataclasses.dataclass
class AlignState:
    """State for alignment."""

    align_target: np.ndarray = None
    step_limit: float = 0.001


def _align(arm, state, new_position, name, threshold, trigger=None):
    """Safety: Align OpenArm with the position."""
    if trigger == "gripper":  # Check if gripper is active (threshold ~ -10 deg)
        gripper_position = new_position[-1]  # Last value is gripper's position
        if name == "right_arm":
            is_gripping = gripper_position > np.deg2rad(-5)
        elif name == "left_arm":
            is_gripping = gripper_position < np.deg2rad(5)
        if not is_gripping:
            return False

    current_position = np.array(arm.fetch_position(), dtype=np.float32)

    if state.align_target is None:
        state.align_target = current_position.copy()

    def is_aligned(position1, position2):
        return np.all(np.abs(position1[:-1] - position2[:-1]) < threshold)

    # If OpenArm is already aligned, we do nothing.
    if is_aligned(new_position, current_position):
        return True
    diff = new_position - state.align_target
    step_move = np.clip(diff, -state.step_limit, state.step_limit)
    state.align_target += step_move

    arm.send_position(state.align_target)

    # Check the physical position on the next command after the arm has moved.
    return False


def _env_flag(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


QPOS_TYPE = pa.struct([("qpos", pa.list_(pa.float32()))])

# Counters openarm_can keeps per interface. They latch, so a consumer that
# wants "during this episode" has to difference them against a baseline of
# its own rather than expect them to fall back to zero.
BUS_TYPE = pa.struct(
    [
        # The only instantaneous field. A bus-off with no auto-restart shows
        # up here; one that auto-restarts only moves the counters.
        ("carrier", pa.bool_()),
        ("bus_off", pa.int64()),
        ("error_passive", pa.int64()),
        ("error_warning", pa.int64()),
        ("ack_error", pa.int64()),
        ("tx_overflow", pa.int64()),
        ("rx_overflow", pa.int64()),
        ("net_down", pa.int64()),
    ]
)

BUS_COUNTERS = (
    "bus_off",
    "error_passive",
    "error_warning",
    "ack_error",
    "tx_overflow",
    "rx_overflow",
)

STATE_TYPE = pa.struct(
    [
        ("qpos", pa.list_(pa.float32())),
        ("qvel", pa.list_(pa.float32())),
        ("qtorque", pa.list_(pa.float32())),
        ("tmos", pa.list_(pa.int32())),
        ("trotor", pa.list_(pa.int32())),
        # One entry per motor, in the same order as qpos: the motor's own
        # status nibble by name, or "SILENT" when it has stopped answering.
        ("motor_status", pa.list_(pa.string())),
        # Whether the last entry of every per-motor list is the gripper.
        # Only the driver's config says so, and a consumer that labels the
        # axes cannot work it out from the lengths alone.
        ("has_gripper", pa.bool_()),
        ("bus", BUS_TYPE),
    ]
)

# An axis quiet for longer than this is reported as SILENT. At the rate a
# leader drives the arm this is many missed replies, not a single one.
AXIS_SILENT_AFTER_S = 0.5

# is_link_running() costs an ioctl, so it is not read on every request.
CARRIER_POLL_INTERVAL_S = 0.5

_EMPTY_BUS = {
    "carrier": True,
    "bus_off": 0,
    "error_passive": 0,
    "error_warning": 0,
    "ack_error": 0,
    "tx_overflow": 0,
    "rx_overflow": 0,
    "net_down": 0,
}


def build_qpos_output(qpos: np.ndarray) -> pa.Array:
    """Wrap a qpos array as a length-1 StructArray: [{"qpos": [...]}]."""
    return pa.array([{"qpos": qpos}], type=QPOS_TYPE)


def _build_arm(name, config, can_interface):
    """Construct a SingleArmDriver, passing can_interface only when asked for.

    Older openarm_driver doesn't accept this keyword at all, so passing it
    unconditionally would break every run on that version -- not just the
    ones that actually want to override the interface. Leaving it out when
    unset keeps this node working against any openarm_driver >= 0.2.0; only
    --can-interface itself needs the newer one.
    """
    if can_interface is None:
        return openarm_driver.SingleArmDriver(name, config)
    return openarm_driver.SingleArmDriver(name, config, can_interface=can_interface)


class HealthReader:
    """Reads openarm_can's bus and per-axis diagnostics, when it has them.

    Reports; it never decides that a fault means stop. The diagnostics are
    newer than the oldest openarm_driver this node accepts, so their absence
    has to be tolerated rather than assumed away.
    """

    def __init__(self, arm):
        """Bind to `arm`, or to nothing when there is no arm yet."""
        self._openarm = getattr(arm, "openarm", None)
        self.has_gripper = bool(getattr(arm, "gripper_posforce", False))
        self._available = hasattr(self._openarm, "get_bus_status") and hasattr(
            openarm_can, "motor_error_to_string"
        )
        self._axes = []
        if self._available:
            try:
                collections = [arm.openarm.get_arm()]
                self._axes = [(0, i) for i in range(arm.num_mit_motors)]
                if getattr(arm, "gripper_posforce", False):
                    collections.append(arm.openarm.get_gripper())
                    self._axes.append((1, 0))
                self._collections = collections
            except Exception:
                self._available = False
        self._carrier = True
        self._carrier_read_at = 0.0

    def _carrier_now(self) -> bool:
        now = time.monotonic()
        if now - self._carrier_read_at >= CARRIER_POLL_INTERVAL_S:
            self._carrier = bool(self._openarm.is_link_running())
            self._carrier_read_at = now
        return self._carrier

    def read(self) -> tuple[list[str], bool, dict]:
        """Per-motor status names, whether the last is the gripper, and the bus."""
        if not self._available:
            return [], self.has_gripper, dict(_EMPTY_BUS)
        try:
            motors = [c.get_motors() for c in self._collections]
            status = []
            for collection_index, motor_index in self._axes:
                collection = self._collections[collection_index]
                link = collection.get_link_stats(motor_index)
                if not link.ever_responded() or link.is_stale(AXIS_SILENT_AFTER_S):
                    status.append("SILENT")
                    continue
                motor = motors[collection_index][motor_index]
                status.append(openarm_can.motor_error_to_string(motor.get_error_code()))

            bus = self._openarm.get_bus_status()
            snapshot = {"carrier": self._carrier_now()}
            for name in BUS_COUNTERS:
                snapshot[name] = int(getattr(bus, name).count)
            snapshot["net_down"] = int(bus.write_net_down.count)
            return status, self.has_gripper, snapshot
        except Exception:
            # Reporting must not be the reason a state message is dropped.
            self._available = False
            return [], self.has_gripper, dict(_EMPTY_BUS)


def build_state_output(state, health: tuple[list[str], bool, dict]) -> pa.Array:
    """Wrap a state dict as a length-1 StructArray: [{"qpos": [...], ...}]."""
    motor_status, has_gripper, bus = health
    return pa.array(
        [
            {
                "qpos": state["qpos"],
                "qvel": state["qvel"],
                "qtorque": state["qtorque"],
                "tmos": state["tmos"],
                "trotor": state["trotor"],
                "motor_status": motor_status,
                "has_gripper": has_gripper,
                "bus": bus,
            }
        ],
        type=STATE_TYPE,
    )


def extract_values(value: pa.Array, key: str) -> np.ndarray:
    """Read `key` from a length-1 StructArray, or a flat array as-is."""
    if pa.types.is_struct(value.type):
        value = value.field(key)[0].values
    return np.array(value, dtype=np.float32)


def main():
    """Move to the given position and output the current position."""
    parser = argparse.ArgumentParser(description="Control OpenArm")
    parser.add_argument(
        "--side",
        choices=["right", "left"],
        default="right",
        help="right or left",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="The configuration file for this OpenArm",
        type=pathlib.Path,
    )
    parser.add_argument(
        "--can-interface",
        default=None,
        help=(
            "SocketCAN interface, overriding the config. Which interface an "
            "arm is on is a property of the machine rather than of the arm, "
            "so a host that names them differently can be handled without "
            "editing the config (default: the config's)."
        ),
    )
    parser.add_argument(
        "--align-trigger",
        choices=["gripper"],
        default=None,
        help="Alignment trigger: gripper (default: None)",
    )
    parser.add_argument(
        "--align-threshold",
        default=0.1,
        help="Alignment threshold [rad] (default: 0.1)",
        type=float,
    )
    parser.add_argument(
        "--align-delta-limit",
        default=0.001,
        help="Maximum joint delta per alignment command [rad] (default: 0.001).",
        type=float,
    )
    parser.add_argument(
        "--align",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Align to incoming position commands after start (default: enabled).",
    )
    parser.add_argument(
        "--stop",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("STOP", True),
        help="Stop the arm on exit.",
    )
    parser.add_argument(
        "--refresh-every-request",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("REFRESH", True),
        help="Refresh OpenArm on every request to make it more accurate.",
    )
    parser.add_argument(
        "--start-on-startup",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Start the arm on startup.",
    )
    args = parser.parse_args()
    if args.align_delta_limit <= 0.0:
        parser.error("--align-delta-limit must be positive")
    node = dora.Node()
    name = f"{args.side}_arm"
    config = openarm_driver.Config(args.config)
    align_threshold = args.align_threshold
    arm = None
    health = HealthReader(None)
    ready_status = ArmStatus.ALIGNED if args.align else ArmStatus.STARTED
    if args.start_on_startup:
        arm = _build_arm(name, config, args.can_interface)
        health = HealthReader(arm)
        arm.start()
        align_state = (
            AlignState(step_limit=args.align_delta_limit) if args.align else None
        )
        status = ArmStatus.STARTED
        node.send_output("status", pa.array([status]))
    else:
        align_state = None
        status = ArmStatus.STOPPED
        node.send_output("status", pa.array([ArmStatus.STOPPED]))
    for event in node:
        if event["type"] != "INPUT":
            continue

        event_id = event["id"]
        if event_id == "command":
            command = event["value"][0].as_py()
            if command == "start":
                if arm is not None:
                    arm.stop()  # Stop the existing session before replacing it
                # Re-initialize the arm to ensure a fresh start
                arm = _build_arm(name, config, args.can_interface)
                health = HealthReader(arm)
                arm.start()
                align_state = (
                    AlignState(step_limit=args.align_delta_limit)
                    if args.align
                    else None
                )
                status = ArmStatus.STARTED
                node.send_output("status", pa.array([status]))
            elif command == "stop":
                status = ArmStatus.STOPPED
                node.send_output("status", pa.array([ArmStatus.STOPPED]))
                if arm is not None:
                    arm.stop()
                    arm = None  # Drop the instance to free resources
                    health = HealthReader(None)
                align_state = None
        elif event_id == "request_position":
            if status is ArmStatus.STOPPED:
                continue
            current_position = arm.fetch_position(
                refresh=args.refresh_every_request,
            )
            node.send_output(
                "position",
                build_qpos_output(np.asarray(current_position, dtype=np.float32)),
            )
        elif event_id == "request_state":
            if status is ArmStatus.STOPPED:
                continue
            state = arm.fetch_state(refresh=args.refresh_every_request)
            node.send_output("state", build_state_output(state, health.read()))
        elif event_id == "move_position":
            if status is ArmStatus.STOPPED:
                continue
            value = event["value"]
            if isinstance(value, pa.StructArray):
                names = value.type.names
                if "qpos" in names:
                    new_position = extract_values(value, "qpos")
                else:
                    new_position = np.array(
                        value.field("new_position"), dtype=np.float32
                    )
                # TODO: We use this for safety check later.
                # other_arm_position = value.field("other_arm_position")
            else:
                new_position = np.array(value, dtype=np.float32)
                # other_arm_position = None

            if status is ready_status:
                arm.send_position(new_position)
            elif status is ArmStatus.STARTED:
                is_aligned = _align(
                    arm,
                    align_state,
                    new_position,
                    name,
                    align_threshold,
                    trigger=args.align_trigger,
                )
                if is_aligned:
                    arm.send_position(new_position)
                    status = ArmStatus.ALIGNED
                    node.send_output("status", pa.array([ArmStatus.ALIGNED]))
    if arm is not None:
        if args.stop:
            arm.stop()
        else:
            arm.move_to_start_position()


if __name__ == "__main__":
    main()
