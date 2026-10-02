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
| `--align-delta-limit` | Maximum intermediate alignment target step in radians. Default: `0.001`. The final target is submitted directly once within the alignment threshold, subject to driver safety checks. |
| `--[no-]start-on-startup` | Whether to start the arm when the node starts. Default: disabled. |
| `--[no-]align` | Whether to align to incoming position commands after the arm starts. Default: enabled. |
| `--[no-]stop` | Whether to stop the arm when the node exits. Default: controlled by the `STOP` environment variable, or `true` when it is unset. |
| `--[no-]refresh-every-request` | Whether to refresh OpenArm state before each request. Default: controlled by the `REFRESH` environment variable, or `true` when it is unset. |

### Inputs

| Input | Description |
| --- | --- |
| `request_position` | Requests the current arm position. The event ID is used and the event value is ignored. |
| `request_state` | Requests the current arm state. The event ID is used and the event value is ignored. |
| `publish_tick` | Reads state once, publishes `state`, then publishes the cached `latest_command` if a command has been accepted. Ignores the event value. |
| `move_position` | Sends a new target position to the arm. The value may be a struct containing `qpos` (`[{"qpos": [...]}]`), a position array directly, or a legacy struct containing `new_position`. When initial alignment is enabled, this input drives the alignment until it completes. |

### Outputs

| Output | Description |
| --- | --- |
| `position` | Current arm position as a length-1 struct containing a float32 array: `[{"qpos": [...]}]`. |
| `state` | Current arm state as a length-1 struct: `[{"qpos": [...], "qvel": [...], "qtorque": [...], "tmos": [...], "trotor": [...], "motor_status": [...], "bus": {...}}]`. `qpos`, `qvel`, and `qtorque` are float32 lists; `tmos` (MOS temperature) and `trotor` (rotor temperature) are int32 lists per motor, in °C. `motor_status` is a string list with one entry per motor in `qpos` order: the motor's own status name, or `SILENT` if it has stopped answering. `bus` is a struct describing the CAN interface: `carrier` (bool, whether the link is up) and the cumulative fault counters `bus_off`, `error_passive`, `error_warning`, `ack_error`, `tx_overflow`, `rx_overflow`, and `net_down` (int64). The counters only grow while the node runs, so compare against a baseline to see what happened during a given period. |
| `status` | Current control state as a string array: `stopped`, `started`, or `aligned`. With alignment enabled, `aligned` is emitted once initial alignment completes. |
| `latest_command` | A length-one `qpos` struct containing the driver's last accepted target, including safety clamping. Published only on `publish_tick`; not measured position or confirmation of arrival. |

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

If the driver returns `False` from `start()`, the node reports `stopped`, does not
advance the epoch, and leaves the command cache empty. Commands remain blocked;
the driver is retained for an explicit stop/start. This control status does not
mean the motors were disabled by the failed start.

### Accepted command snapshots

To sample state and accepted commands together, connect `publish_tick` to a timer
and declare both `state` and `latest_command` outputs. It performs one state read;
the command snapshot reads the driver's cache and never resends a motor command.
The old request inputs, `position` output, and automatic-start option remain
available. Avoid wiring an additional state request to the same timer unless a
second read is intended.

Normal and alignment commands cache their source metadata only when the driver
accepts them. The snapshot preserves that metadata, including chunk identifiers
and source `timestamp`, and adds `executed_timestamp` (Unix nanoseconds recorded
by the driver before dispatch) and the current `start_epoch`. Rejected commands
leave the previous cache unchanged; alignment completes only after the final
target is accepted. Repeated ticks preserve the command's original times.

The final startup dispatch, when present, uses its dispatch time as the source
timestamp. A startup without a dispatch has no command snapshot. Stop/start
clears the old cache. Several accepted commands between ticks produce only the
latest snapshot, not a complete log of every dispatch. This requires the boolean
`send_position()` contract in `openarm-driver >= 0.5.0`; no new stop API is needed.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

Copyright 2026 Enactic, Inc.

## Code of Conduct

All participation in the OpenArm project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
