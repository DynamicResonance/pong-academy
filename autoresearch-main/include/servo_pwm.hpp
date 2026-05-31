#pragma once

class HardwarePwmServo {
public:
    explicit HardwarePwmServo(
        unsigned gpio_pin = 18,
        unsigned pwm_frequency_hz = 50,
        double min_angle_degrees = 0.0,
        double max_angle_degrees = 180.0,
        unsigned min_pulse_us = 500,
        unsigned max_pulse_us = 2500);

    HardwarePwmServo(const HardwarePwmServo&) = delete;
    HardwarePwmServo& operator=(const HardwarePwmServo&) = delete;

    ~HardwarePwmServo();

    void write_angle_degrees(double angle_degrees) const;
    void stop() const;

private:
    int pi_;
    unsigned gpio_pin_;
    unsigned pwm_frequency_hz_;
    double min_angle_degrees_;
    double max_angle_degrees_;
    unsigned min_pulse_us_;
    unsigned max_pulse_us_;
};
