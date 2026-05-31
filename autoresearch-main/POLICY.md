This file describes the specs for a policy controller for a single actuator.
* Write in Python and use uv as package manager.
* Use a pub-sub architecture via Zenoh
    * Subcribe to topic "position" which has the schema {"x": float, "y": float, "timestamp_ns": int, "visible": bool}
    * Publish on the topic "command" with the schema {"timestamp_ns": int, "servo_angle_rad": float, "x": float, "y": float, "visible": bool}
    * timestamp_ns, x, y, and visible are passthrough from position to command
    * The callback function publishes one command for every position message so every detected timestamp can be logged.
* Initialize with a random policy which chooses a new motor angle between 0-180 at 0.5 sec intervals. Between intervals, publish every timestamp with the previously chosen servo_angle_rad.
