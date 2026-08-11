# RGB-D and Pose-Label Boundary

The current pipeline outputs RGB-D frames, camera calibration, frame timestamps, and `depth_frame_index` mappings for aligned CSI packets. These are synchronized reference measurements.

A later pose-label stage must explicitly add:

- `pose_joints_3d`: shape `(frames, joints, 3)` with documented units;
- `pose_confidence`: shape `(frames, joints)`;
- joint-name/order metadata and a missing-joint policy;
- the camera coordinate system and any camera-to-room/Wi-Fi extrinsic transform;
- `pose_frame_index` or another documented mapping from aligned CSI rows to pose rows;
- accuracy checks against known distances or an approved reference system.

Until those fields exist and pass validation, reports should say “synchronized RGB-D reference” rather than “3D pose ground truth.”
