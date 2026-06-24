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
import openarm_driver
import os
import pathlib
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
            is_gripping = gripper_position.as_py() > np.deg2rad(-5)
        elif name == "left_arm":
            is_gripping = gripper_position.as_py() < np.deg2rad(5)
        if not is_gripping:
            return False

    def current_position():
        return np.array(arm.fetch_position(), dtype=np.float32)

    if state.align_target is None:
        state.align_target = current_position()

    def is_aligned(position1, position2):
        return np.all(np.abs(position1 - position2) < threshold)

    # If OpenArm is already aligned, we do nothing.
    if is_aligned(new_position, current_position()):
        return True
    diff = new_position - state.align_target
    step_move = np.clip(diff, -state.step_limit, state.step_limit)
    state.align_target += step_move

    arm.send_position(state.align_target)

    return is_aligned(new_position, current_position())


def _env_flag(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


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
    node = dora.Node()
    name = f"{args.side}_arm"
    config = openarm_driver.Config(args.config)
    align_threshold = args.align_threshold
    arm = openarm_driver.SingleArmDriver(name, config)
    if args.start_on_startup:
        arm.start()
        align_state = AlignState()
        status = ArmStatus.STARTED
        node.send_output("status", pa.array([ArmStatus.STARTED]))
    else:
        status = ArmStatus.STOPPED
        node.send_output("status", pa.array([ArmStatus.STOPPED]))
    for event in node:
        if event["type"] != "INPUT":
            continue

        event_id = event["id"]
        if event_id == "command":
            command = event["value"][0].as_py()
            if command == "start":
                arm.start()
                align_state = AlignState()
                status = ArmStatus.STARTED
                node.send_output("status", pa.array([ArmStatus.STARTED]))
            elif command == "stop":
                status = ArmStatus.STOPPED
                node.send_output("status", pa.array([ArmStatus.STOPPED]))
                arm.stop()
        elif event_id == "request_position":
            current_position = arm.fetch_position(
                refresh=args.refresh_every_request,
            )
            node.send_output(
                "position",
                pa.array(current_position, type=pa.float32()),
            )
        elif event_id == "request_state":
            state = arm.fetch_state(refresh=args.refresh_every_request)
            node.send_output(
                "state",
                pa.StructArray.from_arrays(
                    [
                        pa.array(state["qpos"], type=pa.float32()),
                        pa.array(state["qvel"], type=pa.float32()),
                        pa.array(state["qtorque"], type=pa.float32()),
                        pa.array(state["tmos"], type=pa.int32()),
                        pa.array(state["trotor"], type=pa.int32()),
                    ],
                    names=["qpos", "qvel", "qtorque", "tmos", "trotor"],
                ),
            )
        elif event_id == "move_position":
            if status is ArmStatus.STOPPED:
                continue
            value = event["value"]
            if isinstance(value, pa.StructArray):
                new_position = value.field("new_position")
                # TODO: We use this for safety check later.
                # other_arm_position = value.field("other_arm_position")
            else:
                new_position = value
                # other_arm_position = None

            if status is ArmStatus.ALIGNED:
                diverged = np.any(
                    np.abs(
                        np.asarray(new_position, dtype=np.float32)[:-1]
                        - arm.last_command[:-1]
                    )
                    > align_threshold
                )
                if diverged:
                    status = ArmStatus.STARTED
                    align_state = AlignState()
                    node.send_output("status", pa.array([ArmStatus.STARTED]))
                else:
                    arm.send_position(new_position)

            if status is ArmStatus.STARTED:
                is_aligned = _align(
                    arm,
                    align_state,
                    new_position,
                    name,
                    align_threshold,
                    trigger=args.align_trigger,
                )
                if is_aligned:
                    status = ArmStatus.ALIGNED
                    node.send_output("status", pa.array([ArmStatus.ALIGNED]))
    if args.stop:
        arm.stop()
    else:
        arm.on_start()


if __name__ == "__main__":
    main()
