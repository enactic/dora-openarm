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

"""Tests for the node's event loop."""

import datetime
import itertools

import numpy as np
import pyarrow as pa

from dora_openarm.main import QPOS_TYPE, parse_args, run


# Unix time in nanoseconds, like time.time_ns(). The driver sends commands
# from this time, and observations are made 1 second later.
_TIMESTAMP = (
    int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
    * 1_000_000_000
)
_OBSERVATION_TIMESTAMP = _TIMESTAMP + 1_000_000_000


class _Node:
    """Yields the given events and records the outputs."""

    def __init__(self, events):
        self.events = events
        self.outputs = []

    def __iter__(self):
        return iter(self.events)

    def send_output(self, output_id, data, metadata):
        self.outputs.append((output_id, data.to_pylist(), metadata))


class _Driver:
    """Sends targets clipped to [-1, 1]. Fails to start if `started` is False."""

    def __init__(self, started=True):
        self.started = started
        self._clock = itertools.count(_TIMESTAMP)
        self.last_command = np.zeros(8)
        self.last_command_dispatch_timestamp_ns = None

    def start(self):
        # The startup motion sends the current position.
        self.last_command_dispatch_timestamp_ns = next(self._clock)
        return self.started

    def stop(self):
        pass

    def fetch_position(self, refresh=True):
        return np.zeros(8)

    def send_position(self, position):
        self.last_command = np.clip(position, -1, 1)
        self.last_command_dispatch_timestamp_ns = next(self._clock)
        return True


def _input(event_id, value=pa.array([]), **metadata):
    return {"type": "INPUT", "id": event_id, "value": value, "metadata": metadata}


def _move(position, **metadata):
    value = pa.array([{"qpos": position}], type=QPOS_TYPE)
    return _input("move_position", value, **metadata)


def _run(events, argv, driver):
    node = _Node(events)
    # Don't depend on the STOP environment variable.
    run(node, parse_args(["--stop", *argv]), lambda name: driver)
    outputs = []
    for output_id, data, metadata in node.outputs:
        # Replace the wall-clock time with a fixed value.
        if "observation_timestamp" in metadata:
            metadata = {**metadata, "observation_timestamp": _OBSERVATION_TIMESTAMP}
        outputs.append((output_id, data, metadata))
    return outputs


def test_commanded_position():
    """commanded_position reports the target that the driver sent last."""
    outputs = _run(
        [
            _input("request_position", tag="startup"),
            _move([2.0] * 8),
            _input("request_position", tag="move"),
        ],
        ["--no-align", "--start-on-startup"],
        _Driver(),
    )
    assert outputs == [
        ("status", ["started"], {"start_epoch": 1}),
        (
            "position",
            [{"qpos": [0.0] * 8}],
            {
                "tag": "startup",
                "start_epoch": 1,
                "observation_timestamp": _OBSERVATION_TIMESTAMP,
            },
        ),
        (
            "commanded_position",
            [{"qpos": [0.0] * 8}],
            {
                "tag": "startup",
                "start_epoch": 1,
                "observation_timestamp": _OBSERVATION_TIMESTAMP,
                "dispatch_timestamp": _TIMESTAMP,
            },
        ),
        (
            "position",
            [{"qpos": [0.0] * 8}],
            {
                "tag": "move",
                "start_epoch": 1,
                "observation_timestamp": _OBSERVATION_TIMESTAMP,
            },
        ),
        # The driver clipped the target.
        (
            "commanded_position",
            [{"qpos": [1.0] * 8}],
            {
                "tag": "move",
                "start_epoch": 1,
                "observation_timestamp": _OBSERVATION_TIMESTAMP,
                "dispatch_timestamp": _TIMESTAMP + 1,
            },
        ),
    ]


def test_start_failure():
    """A failed start keeps the arm stopped and the epoch unchanged."""
    outputs = _run(
        [
            _input("command", pa.array(["start"])),
            _input("request_position"),
            _move([0.5] * 8),
        ],
        ["--no-align"],
        _Driver(started=False),
    )
    assert outputs == [
        # The initial status.
        ("status", ["stopped"], {"start_epoch": 0}),
        # The status after the start command.
        ("status", ["stopped"], {"start_epoch": 0}),
    ]
