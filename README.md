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
    args: "--side right --align-trigger gripper --start-on-startup"
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
    args: "--side left --align-trigger gripper --start-on-startup"
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
| `--align-delta-limit` | Maximum joint delta per intermediate initial-alignment command in radians. Once the arm is within `--align-threshold`, the final target is sent directly. Default: `0.001`. |
| `--[no-]align` | Whether to align to incoming position commands after the arm starts. Default: enabled. |
| `--[no-]stop` | Whether to stop the arm when the node exits. Default: controlled by the `STOP` environment variable, or `true` when it is unset. |
| `--[no-]refresh-every-request` | Whether to refresh OpenArm state before each request. Default: controlled by the `REFRESH` environment variable, or `true` when it is unset. |
| `--[no-]start-on-startup` | Whether to start the arm when the node starts. When disabled, send `start` to the `command` input to start the arm. Default: disabled. |

### Inputs

| Input | Description |
| --- | --- |
| `request_position` | Requests the current arm position and the latest command sent to the arm. The event ID is used and the event value is ignored. |
| `request_state` | Requests the current arm state and the latest command sent to the arm. The event ID is used and the event value is ignored. |
| `command` | Controls the arm. The value is a string array whose first element is `start` or `stop`. `start` stops the current session if any, then starts the arm again; `stop` stops the arm. Both publish `status`. Other values are ignored. |
| `move_position` | Sends a new target position to the arm. The value may be a struct containing `qpos` (`[{"qpos": [...]}]`), a position array directly, or a legacy struct containing `new_position`. When initial alignment is enabled, this input drives the alignment until it completes. |

### Outputs

| Output | Description |
| --- | --- |
| `position` | Current arm position as a length-1 struct containing a float32 array: `[{"qpos": [...]}]`. |
| `state` | Current arm state as a length-1 struct: `[{"qpos": [...], "qvel": [...], "qtorque": [...], "tmos": [...], "trotor": [...], "motor_status": [...], "bus": {...}}]`. `qpos`, `qvel`, and `qtorque` are float32 lists; `tmos` (MOS temperature) and `trotor` (rotor temperature) are int32 lists per motor, in °C. `motor_status` is a string list with one entry per motor in `qpos` order: the motor's own status name, or `SILENT` if it has stopped answering. `bus` is a struct describing the CAN interface: `carrier` (bool, whether the link is up) and the cumulative fault counters `bus_off`, `error_passive`, `error_warning`, `ack_error`, `tx_overflow`, `rx_overflow`, and `net_down` (int64). The counters only grow while the node runs, so compare against a baseline to see what happened during a given period. |
| `status` | Current control state as a string array: `stopped`, `started`, or `aligned`. With alignment enabled, `aligned` is emitted once initial alignment completes. |
| `commanded_position` | Latest target position sent to the arm, as a length-1 struct containing a float32 array: `[{"qpos": [...]}]`. This is the value after the driver's safety checks, so it may differ from the requested `move_position` value. Published after `position` on `request_position` and after `state` on `request_state`, once the driver has sent a position command since the arm started. This includes the driver's startup motion, so it is usually available right after start. It is not the measured position nor a confirmation that the arm has reached the target. |

`request_state` publishes `state` and `request_position` publishes `position`.
Both also publish `commanded_position` when available. Each observation includes
`observation_timestamp`: integer Unix wall-clock nanoseconds captured after the
snapshot is read. Request metadata is preserved except for `timestamp`, which is
removed so Dora supplies the output message timestamp.

`commanded_position` has the same metadata as the `state` or `position`
published for the same request, plus `dispatch_timestamp`: integer Unix
wall-clock nanoseconds when the driver sent the command. Declare
`commanded_position` in the node's `outputs` only if you need it.

When the command was sent for a `move_position`, including an intermediate
alignment step, `commanded_position` also includes that `move_position`'s
metadata, such as chunk identifiers, except for `timestamp`. If a key is in
both, the `move_position` value is used, except for `start_epoch`,
`observation_timestamp`, and `dispatch_timestamp`, which this node sets.
Commands rejected by the driver and the driver's startup motion add no
`move_position` metadata.

Every output includes a process-local `start_epoch`, starting at zero and
incrementing after each successful start, including `--start-on-startup`.
Commands with an epoch must supply a non-boolean integer matching the current
session; malformed or mismatched epochs are ignored. Commands without an epoch
remain accepted for compatibility. The epoch resets on process restart and
does not detect reordered commands within a session.

If the arm fails to start, for example because the driver's startup motion is
interrupted by a safety check, the node publishes `stopped` and does not
increment `start_epoch`. `request_position`, `request_state`, and
`move_position` are ignored until the arm is started again. The motors may stay
enabled until the arm is stopped or started again.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

Copyright 2026 Enactic, Inc.

## Code of Conduct

All participation in the OpenArm project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
