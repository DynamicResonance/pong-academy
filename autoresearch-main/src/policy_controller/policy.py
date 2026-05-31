from __future__ import annotations

from dataclasses import dataclass
import json
import math
import random
from typing import Any


MIN_SERVO_ANGLE_RAD = 0.0
MAX_SERVO_ANGLE_RAD = math.pi


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    visible: bool
    timestamp_ns: int


@dataclass(frozen=True)
class Command:
    timestamp_ns: int
    x: float
    y: float
    visible: bool
    servo_angle_rad: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp_ns": self.timestamp_ns,
                "x": self.x,
                "y": self.y,
                "visible": self.visible,
                "servo_angle_rad": self.servo_angle_rad,
            },
            separators=(",", ":"),
        )


class RandomPolicy:
    """Initial policy that commands any servo angle from 0 to 180 degrees."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    def __call__(self, position: Position) -> Command:
        return Command(
            timestamp_ns=position.timestamp_ns,
            x=position.x,
            y=position.y,
            visible=position.visible,
            servo_angle_rad=self._rng.uniform(MIN_SERVO_ANGLE_RAD, MAX_SERVO_ANGLE_RAD),
        )


class OptimizedPolicy:
    """Hold productive servo plateaus and avoid unnecessary angle travel."""

    SUCCESS_LEN = 3
    NONFORWARD_THRESHOLD = 3
    MAX_STEP_RAD = 0.20
    TARGET_FALLBACK_RAD = 2.40
    MIN_CHANGE_RAD = 0.01

    def __init__(self) -> None:
        self._last_position: Position | None = None
        self._last_servo_angle_rad = self.TARGET_FALLBACK_RAD
        self._last_successful_angle_rad: float | None = None
        self._consecutive_forward_count = 0
        self._consecutive_nonforward_count = 0

    def __call__(self, position: Position) -> Command:
        x_velocity = self._x_velocity(position)
        if x_velocity is None:
            servo_angle_rad = self._last_servo_angle_rad
        elif x_velocity > 0.0:
            self._consecutive_forward_count += 1
            self._consecutive_nonforward_count = 0
            if self._consecutive_forward_count >= self.SUCCESS_LEN:
                self._last_successful_angle_rad = self._last_servo_angle_rad
            servo_angle_rad = self._last_servo_angle_rad
        else:
            self._consecutive_nonforward_count += 1
            self._consecutive_forward_count = 0
            servo_angle_rad = self._angle_after_nonforward_sample()

        self._last_position = position
        self._last_servo_angle_rad = _clamp_servo_angle(servo_angle_rad)
        return command_with_servo_angle(position, self._last_servo_angle_rad)

    def _x_velocity(self, position: Position) -> float | None:
        if self._last_position is None:
            return None
        dt_ns = position.timestamp_ns - self._last_position.timestamp_ns
        if dt_ns <= 0:
            return None
        return (position.x - self._last_position.x) / dt_ns

    def _angle_after_nonforward_sample(self) -> float:
        if self._consecutive_nonforward_count < self.NONFORWARD_THRESHOLD:
            return self._last_servo_angle_rad

        target = self._last_successful_angle_rad
        if target is None:
            target = self.TARGET_FALLBACK_RAD

        delta = target - self._last_servo_angle_rad
        if abs(delta) < self.MIN_CHANGE_RAD:
            return self._last_servo_angle_rad

        step = min(self.MAX_STEP_RAD, max(-self.MAX_STEP_RAD, delta))
        return self._last_servo_angle_rad + step


def command_with_servo_angle(position: Position, servo_angle_rad: float) -> Command:
    return Command(
        timestamp_ns=position.timestamp_ns,
        x=position.x,
        y=position.y,
        visible=position.visible,
        servo_angle_rad=servo_angle_rad,
    )


def _clamp_servo_angle(servo_angle_rad: float) -> float:
    return min(MAX_SERVO_ANGLE_RAD, max(MIN_SERVO_ANGLE_RAD, servo_angle_rad))


def parse_position_json(payload: str | bytes | bytearray) -> Position:
    try:
        message = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid position JSON: {error}") from error

    if not isinstance(message, dict):
        raise ValueError("position payload must be a JSON object")

    x = _require_finite_float(message, "x")
    y = _require_finite_float(message, "y")
    visible = _require_bool(message, "visible")
    timestamp_ns = _require_int(message, "timestamp_ns")
    return Position(x=x, y=y, visible=visible, timestamp_ns=timestamp_ns)


def command_for_position_payload(payload: str | bytes | bytearray, policy: RandomPolicy) -> str:
    return policy(parse_position_json(payload)).to_json()


def _require_finite_float(message: dict[str, Any], field: str) -> float:
    if field not in message:
        raise ValueError(f"missing field: {field}")

    value = message[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be a number")

    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")

    return value


def _require_int(message: dict[str, Any], field: str) -> int:
    if field not in message:
        raise ValueError(f"missing field: {field}")

    value = message[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")

    return value


def _require_bool(message: dict[str, Any], field: str) -> bool:
    if field not in message:
        raise ValueError(f"missing field: {field}")

    value = message[field]
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")

    return value
