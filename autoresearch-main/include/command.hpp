#pragma once

#include <cstdint>
#include <string>

struct Command {
    std::int64_t timestamp_ns;
    double servo_angle_rad;
};

Command parse_command_json(const std::string& payload);
double radians_to_degrees(double radians);
