from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import signal
import sys
import threading
from typing import Any, Callable, TextIO

import zenoh


COMMAND_TOPIC = "command"
CSV_HEADER = ["timestamp_ns", "x", "y", "servo_angle_rad"]
DEFAULT_LOG_DIR = "episodes"
MCP_PROTOCOL_VERSION = "2025-06-18"
ACTIVE_SUFFIX = ".active.csv"
COMPLETE_SUFFIX = ".csv"


@dataclass(frozen=True)
class Command:
    timestamp_ns: int
    x: float
    y: float
    visible: bool
    servo_angle_rad: float


class EpisodeManager:
    def __init__(
        self,
        log_dir: Path | str = DEFAULT_LOG_DIR,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._active_path: Path | None = None
        self._completed_active_path: Path | None = None
        self._active_file: TextIO | None = None
        self._writer: Any | None = None
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def active_path(self) -> Path | None:
        return self._active_path

    def close(self) -> None:
        with self._lock:
            self._close_active_locked()

    def handle_command_payload(self, payload: str | bytes | bytearray) -> None:
        command = parse_command_json(payload)
        with self._lock:
            if self._active_file is not None and (not command.visible or command.x < 0.0):
                self._logger.info(
                    "closing episode path=%s visible=%s x=%f",
                    self._active_path,
                    command.visible,
                    command.x,
                )
                self._close_active_locked()
                return

            if self._active_file is None and command.visible and 0.0 < command.x < 1.0:
                self._open_active_locked()

            if self._active_file is None or self._writer is None:
                return

            self._writer.writerow(
                [
                    command.timestamp_ns,
                    command.x,
                    command.y,
                    command.servo_angle_rad,
                ]
            )
            self._active_file.flush()

    def list_logs(self) -> list[Path]:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return sorted(
            path
            for path in self.log_dir.glob(f"*{COMPLETE_SUFFIX}")
            if path.is_file() and not path.name.endswith(ACTIVE_SUFFIX)
        )

    def _open_active_locked(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        filename_base = _rfc3339_timestamp(self._now())
        completed_path = self.log_dir / f"{filename_base}{COMPLETE_SUFFIX}"
        path = self.log_dir / f"{filename_base}{ACTIVE_SUFFIX}"
        file = path.open("x", newline="", encoding="utf-8")
        writer = csv.writer(file)
        writer.writerow(CSV_HEADER)
        file.flush()

        self._active_path = path
        self._completed_active_path = completed_path
        self._active_file = file
        self._writer = writer
        self._logger.info("opened episode path=%s", path)

    def _close_active_locked(self) -> None:
        if self._active_file is None:
            return

        active_path = self._active_path
        completed_path = self._completed_active_path
        self._active_file.close()
        if active_path is not None and completed_path is not None:
            _finalize_episode_csv(active_path, completed_path)
            active_path.unlink()
        self._active_file = None
        self._writer = None
        self._active_path = None
        self._completed_active_path = None


def parse_command_json(payload: str | bytes | bytearray) -> Command:
    try:
        message = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid command JSON: {error}") from error

    if not isinstance(message, dict):
        raise ValueError("command payload must be a JSON object")

    timestamp_ns = _require_int(message, "timestamp_ns")
    x = _require_finite_float(message, "x")
    y = _require_finite_float(message, "y")
    visible = _require_bool(message, "visible")
    servo_angle_rad = _require_finite_float(message, "servo_angle_rad")
    return Command(timestamp_ns=timestamp_ns, x=x, y=y, visible=visible, servo_angle_rad=servo_angle_rad)


def _finalize_episode_csv(active_path: Path, completed_path: Path) -> None:
    rows: list[list[str]] = []
    seen_timestamps: set[int] = set()
    first_timestamp_ns: int | None = None

    with active_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            timestamp_ns = int(row["timestamp_ns"])
            if timestamp_ns in seen_timestamps:
                continue
            seen_timestamps.add(timestamp_ns)
            if first_timestamp_ns is None:
                first_timestamp_ns = timestamp_ns
            rows.append(
                [
                    str(timestamp_ns - first_timestamp_ns),
                    row["x"],
                    row["y"],
                    row["servo_angle_rad"],
                ]
            )

    with completed_path.open("x", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def run_collector(log_dir: Path | str) -> None:
    logger = logging.getLogger(__name__)
    stop = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        logger.debug("received shutdown signal signum=%d", _signum)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    manager = EpisodeManager(log_dir)

    def on_command(sample: object) -> None:
        try:
            payload = sample.payload.to_string()  # type: ignore[attr-defined]
            manager.handle_command_payload(payload)
        except Exception:
            logger.exception("failed to handle command sample")

    logger.info("opening Zenoh session")
    with zenoh.open(zenoh.Config()) as session:
        logger.info("declaring subscriber topic=%s", COMMAND_TOPIC)
        command_subscriber = session.declare_subscriber(COMMAND_TOPIC, on_command)
        logger.info("episode manager running log_dir=%s", Path(log_dir))
        try:
            stop.wait()
        finally:
            command_subscriber.undeclare()
            manager.close()
    logger.info("episode manager stopped")


def run_mcp_server(log_dir: Path | str) -> None:
    server = McpServer(EpisodeManager(log_dir))
    server.run(sys.stdin, sys.stdout)


class McpServer:
    def __init__(self, manager: EpisodeManager) -> None:
        self._manager = manager

    def run(self, input_stream: TextIO, output_stream: TextIO) -> None:
        for line in input_stream:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
                response = self.handle_request(request)
            except Exception as error:
                response = _json_rpc_error(None, -32603, str(error))

            if response is not None:
                output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
                output_stream.flush()

    def handle_request(self, request: object) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return _json_rpc_error(None, -32600, "request must be a JSON object")

        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return _json_rpc_error(request_id, -32600, "request method must be a string")

        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "autoresearch-episode-manager", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _json_rpc_result(request_id, {})
        if method == "tools/list":
            return _json_rpc_result(
                request_id,
                {
                    "tools": [
                        {
                            "name": "list_episode_logs",
                            "description": "Return the CSV episode log paths stored by the episode manager.",
                            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                        }
                    ]
                },
            )
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or params.get("name") != "list_episode_logs":
                return _json_rpc_error(request_id, -32602, "unknown tool")
            paths = [str(path) for path in self._manager.list_logs()]
            return _json_rpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"paths": paths}, separators=(",", ":")),
                        }
                    ],
                    "structuredContent": {"paths": paths},
                    "isError": False,
                },
            )

        return _json_rpc_error(request_id, -32601, f"unsupported method: {method}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect autoresearch episodes and expose episode logs.")
    parser.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help="Directory where episode CSV files are stored.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run the MCP stdio server instead of the Zenoh collector.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Minimum log level to emit.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    if args.mcp:
        run_mcp_server(args.log_dir)
    else:
        run_collector(args.log_dir)


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


def _rfc3339_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


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


def _json_rpc_result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
