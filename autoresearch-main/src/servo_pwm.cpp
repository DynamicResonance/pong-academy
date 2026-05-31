#include "servo_pwm.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include <pigpiod_if2.h>

namespace {

unsigned pulse_to_pigpio_duty(unsigned pulse_us, unsigned frequency_hz) {
    return pulse_us * frequency_hz;
}

}  // namespace

HardwarePwmServo::HardwarePwmServo(
    unsigned gpio_pin,
    unsigned pwm_frequency_hz,
    double min_angle_degrees,
    double max_angle_degrees,
    unsigned min_pulse_us,
    unsigned max_pulse_us)
    : gpio_pin_(gpio_pin),
      pwm_frequency_hz_(pwm_frequency_hz),
      min_angle_degrees_(min_angle_degrees),
      max_angle_degrees_(max_angle_degrees),
      min_pulse_us_(min_pulse_us),
      max_pulse_us_(max_pulse_us) {
    pi_ = pigpio_start(nullptr, nullptr);
    if (pi_ < 0) {
        throw std::runtime_error("failed to connect to pigpio daemon");
    }
    if (min_angle_degrees_ >= max_angle_degrees_) {
        throw std::invalid_argument("min angle must be less than max angle");
    }
    if (min_pulse_us_ >= max_pulse_us_) {
        throw std::invalid_argument("min pulse must be less than max pulse");
    }
}

HardwarePwmServo::~HardwarePwmServo() {
    stop();
    pigpio_stop(pi_);
}

void HardwarePwmServo::write_angle_degrees(double angle_degrees) const {
    if (!std::isfinite(angle_degrees)) {
        throw std::invalid_argument("angle must be finite");
    }

    const double clamped_angle = std::clamp(angle_degrees, min_angle_degrees_, max_angle_degrees_);
    const double angle_fraction =
        (clamped_angle - min_angle_degrees_) / (max_angle_degrees_ - min_angle_degrees_);
    const auto pulse_us = static_cast<unsigned>(
        std::lround(min_pulse_us_ + angle_fraction * (max_pulse_us_ - min_pulse_us_)));
    const unsigned duty_cycle = pulse_to_pigpio_duty(pulse_us, pwm_frequency_hz_);

    const int result = hardware_PWM(pi_, gpio_pin_, pwm_frequency_hz_, duty_cycle);
    if (result != 0) {
        throw std::runtime_error("gpioHardwarePWM failed");
    }
}

void HardwarePwmServo::stop() const {
    hardware_PWM(pi_, gpio_pin_, 0, 0);
}
