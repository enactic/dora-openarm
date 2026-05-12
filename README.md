# dora-openarm

A [Dora](https://dora-rs.ai/) node that controls OpenArm.

## Usage

Run the node with:

```console
dora-openarm [--side {right,left}] [--config PATH] [--align-trigger gripper]
             [--align-threshold RAD] [--[no-]stop]
             [--[no-]refresh-every-request]
```

### Options

- `--side`: OpenArm side to control. Default: `right`.
- `--config`: Path to the OpenArm configuration file. Default:
  `openarm_cell.yaml`.
- `--align-trigger`: Optional trigger for the initial alignment step. Supported
  value: `gripper`.
- `--align-threshold`: Alignment threshold in radians. Default: `0.1`.
- `--[no-]stop`: Whether to stop the arm when the node exits. Default:
  controlled by the `STOP` environment variable, or `true` when it is unset.
- `--[no-]refresh-every-request`: Whether to refresh OpenArm state before each
  request. Default: controlled by the `REFRESH` environment variable, or
  `true` when it is unset.

### Inputs

- `request_position`: Requests the current arm position.
- `request_state`: Requests the current arm state.
- `move_position`: Sends a new target position to the arm. The value may be a
  position array directly, or a struct containing `new_position`. When the node
  has not been initialized yet, this input first drives the alignment step.

### Outputs

- `position`: Current arm position as a float32 array.
- `state`: Current arm state as a struct with float32 array fields `qpos`,
  `qvel`, and `qtorque`.
- `status`: A string array containing `ready` once the initial alignment
  completes.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

Copyright 2026 Enactic, Inc.

## Code of Conduct

All participation in the OpenArm project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
