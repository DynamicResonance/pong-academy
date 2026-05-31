import csv
from datetime import datetime, timezone
import json
import tempfile
from pathlib import Path
import unittest

from episode_manager.app import EpisodeManager, McpServer, parse_command_json


class EpisodeManagerTest(unittest.TestCase):
    def test_starts_episode_on_visible_command_under_threshold_and_writes_command_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = EpisodeManager(
                tmp,
                now=lambda: datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc),
            )

            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.9, "y": 0.25, "visible": true, "servo_angle_rad": 1.5}'
            )
            self.assertIsNotNone(manager.active_path)

            manager.close()

            logs = sorted(Path(tmp).glob("*.csv"))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].name, "2026-05-30T12:00:00Z.csv")
            self.assertFalse(logs[0].name.endswith(".active.csv"))
            with logs[0].open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file))

            self.assertEqual(rows[0], ["timestamp_ns", "x", "y", "servo_angle_rad"])
            self.assertEqual(rows[1], ["0", "0.9", "0.25", "1.5"])

    def test_does_not_start_episode_when_command_is_not_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = EpisodeManager(tmp)

            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.5, "y": 0.25, "visible": false, "servo_angle_rad": 1.5}'
            )

            self.assertIsNone(manager.active_path)
            self.assertEqual(manager.list_logs(), [])

    def test_does_not_start_episode_when_command_x_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = EpisodeManager(tmp)

            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.0, "y": 0.25, "visible": true, "servo_angle_rad": 1.5}'
            )

            self.assertIsNone(manager.active_path)
            self.assertEqual(manager.list_logs(), [])

    def test_closes_active_episode_when_command_is_not_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = EpisodeManager(tmp)
            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.5, "y": 0.25, "visible": true, "servo_angle_rad": 1.5}'
            )
            self.assertIsNotNone(manager.active_path)

            manager.handle_command_payload(
                '{"timestamp_ns": 11, "x": 0.0, "y": 0.0, "visible": false, "servo_angle_rad": 2.5}'
            )

            self.assertIsNone(manager.active_path)

    def test_closes_active_episode_when_command_x_is_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = EpisodeManager(tmp)
            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.5, "y": 0.25, "visible": true, "servo_angle_rad": 1.5}'
            )
            self.assertIsNotNone(manager.active_path)

            manager.handle_command_payload(
                '{"timestamp_ns": 11, "x": -0.1, "y": 0.5, "visible": true, "servo_angle_rad": 2.5}'
            )

            self.assertIsNone(manager.active_path)

    def test_each_command_carries_its_own_timestep_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            timestamps = iter(
                [
                    datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 5, 30, 12, 0, 1, tzinfo=timezone.utc),
                ]
            )
            manager = EpisodeManager(tmp, now=lambda: next(timestamps))
            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.5, "y": 0.25, "visible": true, "servo_angle_rad": 1.5}'
            )
            manager.handle_command_payload(
                '{"timestamp_ns": 11, "x": 0.0, "y": 0.0, "visible": false, "servo_angle_rad": 2.0}'
            )
            manager.handle_command_payload(
                '{"timestamp_ns": 20, "x": 0.4, "y": 0.75, "visible": true, "servo_angle_rad": 2.5}'
            )
            manager.handle_command_payload(
                '{"timestamp_ns": 20, "x": 0.9, "y": 0.95, "visible": true, "servo_angle_rad": 3.0}'
            )
            manager.handle_command_payload(
                '{"timestamp_ns": 25, "x": 0.8, "y": 0.85, "visible": true, "servo_angle_rad": 3.5}'
            )
            manager.close()

            logs = sorted(Path(tmp).glob("*.csv"))
            with logs[1].open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file))

            self.assertEqual(
                rows,
                [
                    ["timestamp_ns", "x", "y", "servo_angle_rad"],
                    ["0", "0.4", "0.75", "2.5"],
                    ["5", "0.8", "0.85", "3.5"],
                ],
            )

    def test_list_logs_excludes_active_episode_until_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = EpisodeManager(tmp)
            manager.handle_command_payload(
                '{"timestamp_ns": 10, "x": 0.5, "y": 0.25, "visible": true, "servo_angle_rad": 1.5}'
            )

            self.assertEqual(manager.list_logs(), [])
            self.assertEqual(len(list(Path(tmp).glob("*.active.csv"))), 1)

            manager.handle_command_payload(
                '{"timestamp_ns": 11, "x": 0.0, "y": 0.0, "visible": false, "servo_angle_rad": 2.5}'
            )

            logs = manager.list_logs()
            self.assertEqual(len(logs), 1)
            self.assertFalse(logs[0].name.endswith(".active.csv"))

    def test_parse_command_json_rejects_invalid_schema(self) -> None:
        command = parse_command_json(
            '{"timestamp_ns": 123, "x": 1.0, "y": 2.0, "visible": true, "servo_angle_rad": 3.14}'
        )
        self.assertEqual(command.timestamp_ns, 123)
        self.assertEqual(command.x, 1.0)
        self.assertEqual(command.y, 2.0)
        self.assertTrue(command.visible)
        self.assertEqual(command.servo_angle_rad, 3.14)

        with self.assertRaises(ValueError):
            parse_command_json('{"timestamp_ns": 1.5, "x": 1.0, "y": 2.0, "visible": true, "servo_angle_rad": 3.14}')

        with self.assertRaises(ValueError):
            parse_command_json('{"timestamp_ns": 123, "x": 1.0, "y": 2.0, "visible": true, "servo_angle_rad": "3.14"}')

        with self.assertRaises(ValueError):
            parse_command_json('{"timestamp_ns": 123, "x": 1.0, "y": 2.0, "visible": 1, "servo_angle_rad": 3.14}')

    def test_mcp_tool_lists_episode_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "2026-05-30T12:00:00Z.csv"
            log_path.write_text("timestamp_ns,x,y,servo_angle_rad\n", encoding="utf-8")
            active_path = Path(tmp) / "2026-05-30T12:00:01Z.active.csv"
            active_path.write_text("timestamp_ns,x,y,servo_angle_rad\n", encoding="utf-8")
            server = McpServer(EpisodeManager(tmp))

            response = server.handle_request(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_episode_logs"}}
            )

            self.assertIsNotNone(response)
            result = response["result"]  # type: ignore[index]
            self.assertEqual(result["structuredContent"], {"paths": [str(log_path)]})
            text = result["content"][0]["text"]
            self.assertEqual(json.loads(text), {"paths": [str(log_path)]})


if __name__ == "__main__":
    unittest.main()
