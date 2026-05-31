#include "command.hpp"

#include <cassert>
#include <cmath>
#include <stdexcept>

namespace {

bool near(double lhs, double rhs) {
    return std::abs(lhs - rhs) < 1e-9;
}

}  // namespace

int main() {
    {
        const Command command =
            parse_command_json(R"({"timestamp_ns": 123456789, "servo_angle_rad": 1.5707963267948966})");
        assert(command.timestamp_ns == 123456789);
        assert(near(command.servo_angle_rad, 1.5707963267948966));
        assert(near(radians_to_degrees(command.servo_angle_rad), 90.0));
    }

    {
        const Command command =
            parse_command_json(R"({"servo_angle_rad": 3.141592653589793, "timestamp_ns": -42})");
        assert(command.timestamp_ns == -42);
        assert(near(radians_to_degrees(command.servo_angle_rad), 180.0));
    }

    {
        bool threw = false;
        try {
            (void)parse_command_json(R"({"timestamp_ns": 1})");
        } catch (const std::invalid_argument&) {
            threw = true;
        }
        assert(threw);
    }

    return 0;
}
