Spec for control of a servo on a Raspberry Pi
* Use GPIO pin 18
* Latency is critical, use hardware PWM
* Use C++
* Subscribe to "command" and read {"timestamp_ns": int, "servo_angle_rad": float}. Ignore extra fields. Convert the servo_angle_rad value to degrees and send.
