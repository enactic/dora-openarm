# dora-openarm

A [Dora](https://dora-rs.ai/) node that controls OpenArm.

## Usage

Use this node from a dora-rs dataflow configuration. For a full configuration
example, see
[enactic/dora-openarm-data-collection](https://github.com/enactic/dora-openarm-data-collection).

```yaml
nodes:
  # ...
  - id: follower-right
    build: pip install dora-openarm
    path: dora-openarm
    args: "--side right --align-trigger gripper"
    inputs:
      # Only the event ID is used. The event value is ignored.
      request_position: leader/right_follower_position
      move_position: leader/right_follower_position
    outputs:
      - position
      - status

  - id: follower-left
    build: pip install dora-openarm
    path: dora-openarm
    args: "--side left --align-trigger gripper"
    inputs:
      # Only the event ID is used. The event value is ignored.
      request_position: leader/left_follower_position
      move_position: leader/left_follower_position
    outputs:
      - position
      - status
  # ...
```

### Node arguments

| Argument | Description |
| --- | --- |
| `--side` | OpenArm side to control. Default: `right`. |
| `--config` | Path to the OpenArm configuration file. Default: `openarm_cell.yaml`. |
| `--can-interface` | SocketCAN interface to use, overriding the one in the configuration file. Default: the configuration file's value. |
| `--align-trigger` | Optional trigger for the initial alignment step. Supported value: `gripper`. |
| `--align-threshold` | Alignment threshold in radians. Default: `0.1`. |
| `--align-delta-limit` | Maximum joint delta per initial-alignment command in radians. Default: `0.001`. |
| `--[no-]align` | Whether to align to incoming position commands after the arm starts. Default: enabled. |
| `--[no-]stop` | Whether to stop the arm when the node exits. Default: controlled by the `STOP` environment variable, or `true` when it is unset. |
| `--[no-]refresh-every-request` | Whether to refresh OpenArm state before each request. Default: controlled by the `REFRESH` environment variable, or `true` when it is unset. |

### Inputs

| Input | Description |
| --- | --- |
| `request_position` | Requests the current arm position. The event ID is used and the event value is ignored. |
| `request_state` | Requests the current arm state. The event ID is used and the event value is ignored. |
| `move_position` | Sends a new target position to the arm. The value may be a struct containing `qpos` (`[{"qpos": [...]}]`), a position array directly, or a legacy struct containing `new_position`. When initial alignment is enabled, this input drives the alignment until it completes. |

### Outputs

| Output | Description |
| --- | --- |
| `position` | Current arm position as a length-1 struct containing a float32 array: `[{"qpos": [...]}]`. |
| `state` | Current arm state as a length-1 struct: `[{"qpos": [...], "qvel": [...], "qtorque": [...], "tmos": [...], "trotor": [...], "motor_status": [...], "bus": {...}}]`. `qpos`, `qvel`, and `qtorque` are float32 lists; `tmos` (MOS temperature) and `trotor` (rotor temperature) are int32 lists per motor, in °C. `motor_status` is a string list with one entry per motor in `qpos` order: the motor's own status name, or `SILENT` if it has stopped answering. `bus` is a struct describing the CAN interface: `carrier` (bool, whether the link is up) and the cumulative fault counters `bus_off`, `error_passive`, `error_warning`, `ack_error`, `tx_overflow`, `rx_overflow`, and `net_down` (int64). The counters only grow while the node runs, so compare against a baseline to see what happened during a given period. |
| `status` | Current control state as a string array: `stopped`, `started`, or `aligned`. With alignment enabled, `aligned` is emitted once initial alignment completes. |

`request_state` publishes only `state`; `request_position` publishes only
`position`. Each observation includes `observation_timestamp`: integer Unix
wall-clock nanoseconds captured after the snapshot is read. Request metadata is
preserved except for `timestamp`, which is removed so Dora supplies the output
message timestamp.

Every output includes a process-local `start_epoch`, starting at zero and
incrementing after each successful start, including `--start-on-startup`.
Commands with an epoch must supply a non-boolean integer matching the current
session; malformed or mismatched epochs are ignored. Commands without an epoch
remain accepted for compatibility. The epoch resets on process restart and
does not detect reordered commands within a session.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

Copyright 2026 Enactic, Inc.

## Code of Conduct

All participation in the OpenArm project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
