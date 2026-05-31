#include "command.hpp"
#include "servo_pwm.hpp"

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <zenoh.h>

namespace {

volatile std::sig_atomic_t keep_running = 1;

void handle_signal(int) {
    keep_running = 0;
}

struct ControllerOptions {
    bool debug = false;
    bool dry_run = false;
};

struct CommandContext {
    HardwarePwmServo* servo = nullptr;
    ControllerOptions options;
};

bool env_flag_enabled(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr) {
        return false;
    }
    return std::strcmp(value, "1") == 0 || std::strcmp(value, "true") == 0 || std::strcmp(value, "TRUE") == 0 ||
           std::strcmp(value, "yes") == 0 || std::strcmp(value, "YES") == 0;
}

ControllerOptions parse_options(int argc, char* argv[]) {
    ControllerOptions options{};
    options.debug = env_flag_enabled("AUTORESEARCH_DEBUG");

    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (arg == "--debug") {
            options.debug = true;
        } else if (arg == "--dry-run") {
            options.dry_run = true;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "usage: servo_controller [--debug] [--dry-run]\n";
            std::exit(0);
        } else {
            throw std::invalid_argument("unknown argument: " + arg);
        }
    }

    return options;
}

void debug_log(const ControllerOptions& options, const std::string& message) {
    if (options.debug) {
        std::cerr << "[servo_controller debug] " << message << '\n';
    }
}

std::string sample_payload_to_string(z_loaned_sample_t* sample) {
    const z_loaned_bytes_t* payload = z_sample_payload(sample);
    if (payload == nullptr) {
        throw std::invalid_argument("sample has no payload");
    }

    std::vector<unsigned char> buffer(z_bytes_len(payload));
    z_bytes_reader_t reader = z_bytes_get_reader(payload);
    const size_t bytes_read = z_bytes_reader_read(&reader, buffer.data(), buffer.size());
    if (bytes_read != buffer.size()) {
        throw std::runtime_error("failed to read full payload");
    }

    return std::string(buffer.begin(), buffer.end());
}

void on_sample(z_loaned_sample_t* sample, void* context) {
    auto* command_context = static_cast<CommandContext*>(context);
    try {
        const std::string payload = sample_payload_to_string(sample);
        debug_log(command_context->options, "received command payload bytes=" + std::to_string(payload.size()) +
                                                " payload=" + payload);
        const Command command = parse_command_json(payload);
        const double angle_degrees = radians_to_degrees(command.servo_angle_rad);
        debug_log(command_context->options,
                  "parsed command timestamp_ns=" + std::to_string(command.timestamp_ns) +
                      " servo_angle_rad=" + std::to_string(command.servo_angle_rad) +
                      " servo_angle_deg=" + std::to_string(angle_degrees));

        if (command_context->options.dry_run) {
            std::cerr << "dry-run command timestamp_ns=" << command.timestamp_ns
                      << " servo_angle_rad=" << command.servo_angle_rad << " servo_angle_deg=" << angle_degrees
                      << '\n';
            return;
        }

        if (command_context->servo == nullptr) {
            throw std::runtime_error("servo is not initialized");
        }

        command_context->servo->write_angle_degrees(angle_degrees);
        debug_log(command_context->options, "wrote servo angle_deg=" + std::to_string(angle_degrees));
    } catch (const std::exception& error) {
        std::cerr << "failed to handle command: " << error.what() << '\n';
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    try {
        const ControllerOptions options = parse_options(argc, argv);
        debug_log(options, "starting");

        std::unique_ptr<HardwarePwmServo> servo;
        if (!options.dry_run) {
            debug_log(options, "initializing hardware PWM servo on GPIO 18");
            servo = std::make_unique<HardwarePwmServo>(18);
        } else {
            debug_log(options, "dry-run enabled; hardware PWM servo will not be initialized");
        }

        CommandContext command_context{servo.get(), options};

        z_owned_config_t config;
        if (z_config_default(&config) != 0) {
            throw std::runtime_error("failed to create default Zenoh config");
        }

        debug_log(options, "opening Zenoh session");
        z_owned_session_t session;
        if (z_open(&session, z_config_move(&config), nullptr) != 0) {
            throw std::runtime_error("failed to open Zenoh session");
        }
        debug_log(options, "opened Zenoh session");

        z_owned_keyexpr_t keyexpr;
        if (z_keyexpr_from_str(&keyexpr, "command") != 0) {
            z_session_drop(z_session_move(&session));
            throw std::runtime_error("failed to create command key expression");
        }
        debug_log(options, "created key expression topic=command");

        z_owned_closure_sample_t callback;
        z_closure_sample(&callback, on_sample, nullptr, &command_context);

        z_owned_subscriber_t subscriber;
        if (z_declare_subscriber(
                z_session_loan(&session),
                &subscriber,
                z_keyexpr_loan(&keyexpr),
                z_closure_sample_move(&callback),
                nullptr) != 0) {
            z_keyexpr_drop(z_keyexpr_move(&keyexpr));
            z_session_drop(z_session_move(&session));
            throw std::runtime_error("failed to declare command subscriber");
        }
        std::cerr << "servo_controller subscribed topic=command";
        if (options.dry_run) {
            std::cerr << " dry_run=true";
        }
        std::cerr << '\n';

        while (keep_running != 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        debug_log(options, "shutting down");
        z_undeclare_subscriber(z_subscriber_move(&subscriber));
        z_keyexpr_drop(z_keyexpr_move(&keyexpr));
        z_session_drop(z_session_move(&session));
        if (servo != nullptr) {
            servo->stop();
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "servo_controller failed: " << error.what() << '\n';
        return 1;
    }
}
