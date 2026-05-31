#include "command.hpp"

#include <cmath>
#include <regex>
#include <stdexcept>
#include <string>

namespace {

std::string require_match(const std::string& payload, const std::regex& pattern, const char* field_name) {
    std::smatch match;
    if (!std::regex_search(payload, match, pattern) || match.size() < 2) {
        throw std::invalid_argument(std::string("missing or invalid field: ") + field_name);
    }
    return match[1].str();
}

}  // namespace

Command parse_command_json(const std::string& payload) {
    static const std::regex timestamp_pattern(R"("timestamp_ns"\s*:\s*(-?\d+))");
    static const std::regex angle_pattern(
        R"("servo_angle_rad"\s*:\s*(-?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?))");

    Command command{};
    command.timestamp_ns = std::stoll(require_match(payload, timestamp_pattern, "timestamp_ns"));
    command.servo_angle_rad = std::stod(require_match(payload, angle_pattern, "servo_angle_rad"));

    if (!std::isfinite(command.servo_angle_rad)) {
        throw std::invalid_argument("servo_angle_rad must be finite");
    }

    return command;
}

double radians_to_degrees(double radians) {
    constexpr double pi = 3.141592653589793238462643383279502884;
    return radians * 180.0 / pi;
}
