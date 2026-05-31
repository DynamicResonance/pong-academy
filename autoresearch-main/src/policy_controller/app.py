from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import threading
import time
from typing import Callable, Protocol

import zenoh

from policy_controller.policy import (
    Command,
    OptimizedPolicy,
    Position,
    RandomPolicy,
    command_with_servo_angle,
    parse_position_json,
)


POSITION_TOPIC = "position"
COMMAND_TOPIC = "command"
COMMAND_INTERVAL_S = 0.5
POLICY_CHOICES = ("random", "optimized")


class Publisher(Protocol):
    def put(self, payload: str) -> object:
        ...


class Policy(Protocol):
    def __call__(self, position: Position) -> Command:
        ...


class PolicyController:
    def __init__(
        self,
        policy: Policy | None = None,
        *,
        command_interval_s: float = COMMAND_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy or RandomPolicy()
        self._command_interval_s = command_interval_s
        self._clock = clock
        self._last_command_at_s: float | None = None
        self._last_servo_angle_rad: float | None = None
        self._logger = logging.getLogger(__name__)

    def handle_payload(self, payload: str | bytes | bytearray) -> str:
        self._logger.debug(
            "received position payload bytes=%d preview=%r",
            _payload_size(payload),
            _payload_preview(payload),
        )

        position = parse_position_json(payload)
        self._logger.debug(
            "parsed position timestamp_ns=%d x=%f y=%f visible=%s",
            position.timestamp_ns,
            position.x,
            position.y,
            position.visible,
        )

        now_s = self._clock()
        if self._command_due(now_s):
            command = self._policy(position)
            self._last_command_at_s = now_s
            self._last_servo_angle_rad = command.servo_angle_rad
        else:
            last_command_at_s = self._last_command_at_s
            servo_angle_rad = self._last_servo_angle_rad
            if last_command_at_s is None or servo_angle_rad is None:
                raise RuntimeError("command reuse requested before first command was recorded")
            self._logger.debug(
                "reusing servo angle elapsed_s=%f interval_s=%f servo_angle_rad=%f",
                now_s - last_command_at_s,
                self._command_interval_s,
                servo_angle_rad,
            )
            command = command_with_servo_angle(position, servo_angle_rad)

        self._logger.debug(
            "policy output timestamp_ns=%d x=%f y=%f visible=%s servo_angle_rad=%f servo_angle_deg=%f",
            command.timestamp_ns,
            command.x,
            command.y,
            command.visible,
            command.servo_angle_rad,
            math.degrees(command.servo_angle_rad),
        )

        command_payload = command.to_json()
        self._logger.debug(
            "encoded command payload bytes=%d payload=%s",
            len(command_payload),
            command_payload,
        )
        return command_payload

    def _command_due(self, now_s: float) -> bool:
        if self._last_command_at_s is None:
            return True
        return now_s - self._last_command_at_s >= self._command_interval_s

    def on_sample(self, sample: object, publisher: Publisher) -> None:
        try:
            key_expr = getattr(sample, "key_expr", POSITION_TOPIC)
            self._logger.debug("received Zenoh sample key_expr=%s", key_expr)
            payload = sample.payload.to_string()  # type: ignore[attr-defined]
            command_payload = self.handle_payload(payload)
            publisher.put(command_payload)
            self._logger.debug("published command topic=%s payload=%s", COMMAND_TOPIC, command_payload)
        except Exception:
            self._logger.exception("failed to handle position sample")


def build_policy(name: str) -> Policy:
    if name == "random":
        return RandomPolicy()
    if name == "optimized":
        return OptimizedPolicy()
    raise ValueError(f"unknown policy: {name}")


def run(policy_name: str = "random") -> None:
    logger = logging.getLogger(__name__)
    stop = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        logger.debug("received shutdown signal signum=%d", _signum)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    controller = PolicyController(build_policy(policy_name))
    logger.info("opening Zenoh session")
    with zenoh.open(zenoh.Config()) as session:
        logger.info("declaring publisher topic=%s", COMMAND_TOPIC)
        publisher = session.declare_publisher(COMMAND_TOPIC)
        logger.info("declaring subscriber topic=%s", POSITION_TOPIC)
        subscriber = session.declare_subscriber(
            POSITION_TOPIC,
            lambda sample: controller.on_sample(sample, publisher),
        )
        logger.info("policy controller running policy=%s", policy_name)
        stop.wait()
        subscriber.undeclare()
    logger.info("policy controller stopped")


def publish_test_position(x: float, y: float, visible: bool, timestamp_ns: int) -> None:
    logger = logging.getLogger(__name__)
    payload = json.dumps(
        {"x": x, "y": y, "visible": visible, "timestamp_ns": timestamp_ns},
        separators=(",", ":"),
    )
    logger.info("opening Zenoh session")
    with zenoh.open(zenoh.Config()) as session:
        logger.info("publishing test position topic=%s payload=%s", POSITION_TOPIC, payload)
        session.put(POSITION_TOPIC, payload)


def listen_command(timeout_s: float | None) -> None:
    logger = logging.getLogger(__name__)
    stop = threading.Event()

    def on_sample(sample: object) -> None:
        payload = sample.payload.to_string()  # type: ignore[attr-defined]
        logger.info("received command payload=%s", payload)
        print(payload, flush=True)

    logger.info("opening Zenoh session")
    with zenoh.open(zenoh.Config()) as session:
        logger.info("declaring command debug subscriber topic=%s", COMMAND_TOPIC)
        subscriber = session.declare_subscriber(COMMAND_TOPIC, on_sample)
        stop.wait(timeout_s)
        subscriber.undeclare()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the autoresearch Zenoh policy controller.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Minimum log level to emit.",
    )
    parser.add_argument(
        "--policy",
        choices=POLICY_CHOICES,
        default="random",
        help="Policy implementation to use when running the controller.",
    )
    parser.add_argument(
        "--publish-test-position",
        action="store_true",
        help="Publish one test position message and exit.",
    )
    parser.add_argument("--x", type=float, default=0.0, help="Test position x value.")
    parser.add_argument("--y", type=float, default=0.0, help="Test position y value.")
    parser.add_argument(
        "--visible",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Test position visible value.",
    )
    parser.add_argument(
        "--timestamp-ns",
        type=int,
        default=1,
        help="Test position timestamp_ns value.",
    )
    parser.add_argument(
        "--listen-command",
        action="store_true",
        help="Subscribe to command messages, print payloads, and exit after --timeout-s.",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=5.0,
        help="Timeout for --listen-command.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.publish_test_position:
        publish_test_position(args.x, args.y, args.visible, args.timestamp_ns)
    elif args.listen_command:
        listen_command(args.timeout_s)
    else:
        run(args.policy)


def _payload_size(payload: str | bytes | bytearray) -> int:
    if isinstance(payload, str):
        return len(payload.encode())
    return len(payload)


def _payload_preview(payload: str | bytes | bytearray, limit: int = 200) -> str:
    if isinstance(payload, str):
        text = payload
    else:
        text = bytes(payload).decode(errors="replace")

    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


if __name__ == "__main__":
    main()
