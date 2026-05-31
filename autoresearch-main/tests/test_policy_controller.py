import json
import math
import random
import unittest

from policy_controller.app import PolicyController, build_policy
from policy_controller.policy import OptimizedPolicy, RandomPolicy, command_for_position_payload, parse_position_json


class PolicyControllerTest(unittest.TestCase):
    def test_parse_position_json_accepts_expected_schema(self) -> None:
        position = parse_position_json('{"x": 1.25, "y": -2, "visible": true, "timestamp_ns": 42}')

        self.assertEqual(position.x, 1.25)
        self.assertEqual(position.y, -2.0)
        self.assertTrue(position.visible)
        self.assertEqual(position.timestamp_ns, 42)

    def test_random_policy_passes_timestamp_through_and_bounds_angle(self) -> None:
        policy = RandomPolicy(random.Random(7))
        command = policy(parse_position_json('{"x": 0.0, "y": 0.0, "visible": false, "timestamp_ns": 99}'))

        self.assertEqual(command.timestamp_ns, 99)
        self.assertEqual(command.x, 0.0)
        self.assertEqual(command.y, 0.0)
        self.assertFalse(command.visible)
        self.assertGreaterEqual(command.servo_angle_rad, 0.0)
        self.assertLessEqual(command.servo_angle_rad, math.pi)

    def test_command_payload_matches_command_schema(self) -> None:
        payload = command_for_position_payload(
            '{"x": 1.0, "y": 2.0, "visible": true, "timestamp_ns": 123}',
            RandomPolicy(random.Random(1)),
        )

        command = json.loads(payload)
        self.assertEqual(set(command), {"timestamp_ns", "x", "y", "visible", "servo_angle_rad"})
        self.assertEqual(command["timestamp_ns"], 123)
        self.assertEqual(command["x"], 1.0)
        self.assertEqual(command["y"], 2.0)
        self.assertTrue(command["visible"])
        self.assertIsInstance(command["servo_angle_rad"], float)

    def test_optimized_policy_preserves_position_fields_and_bounds_angle(self) -> None:
        policy = OptimizedPolicy()
        position = parse_position_json('{"x": 0.25, "y": 0.5, "visible": true, "timestamp_ns": 250000000}')

        command = policy(position)

        self.assertEqual(command.timestamp_ns, position.timestamp_ns)
        self.assertEqual(command.x, position.x)
        self.assertEqual(command.y, position.y)
        self.assertEqual(command.visible, position.visible)
        self.assertGreaterEqual(command.servo_angle_rad, 0.0)
        self.assertLessEqual(command.servo_angle_rad, math.pi)

    def test_build_policy_selects_cli_policy(self) -> None:
        self.assertIsInstance(build_policy("random"), RandomPolicy)
        self.assertIsInstance(build_policy("optimized"), OptimizedPolicy)

    def test_optimized_policy_holds_angle_during_forward_progress(self) -> None:
        policy = OptimizedPolicy()

        commands = [
            policy(parse_position_json(f'{{"x": {x}, "y": 0.0, "visible": true, "timestamp_ns": {i}}}'))
            for i, x in enumerate([0.0, 0.2, 0.4, 0.7], start=1)
        ]

        self.assertEqual([command.servo_angle_rad for command in commands], [2.4, 2.4, 2.4, 2.4])

    def test_optimized_policy_moves_slowly_after_persistent_stall(self) -> None:
        policy = OptimizedPolicy()
        for i, x in enumerate([0.0, 0.2, 0.4, 0.7], start=1):
            policy(parse_position_json(f'{{"x": {x}, "y": 0.0, "visible": true, "timestamp_ns": {i}}}'))

        policy._last_servo_angle_rad = 1.6
        stall_commands = [
            policy(parse_position_json(f'{{"x": {x}, "y": 0.0, "visible": true, "timestamp_ns": {i}}}'))
            for i, x in enumerate([0.6, 0.5, 0.4], start=5)
        ]

        self.assertEqual([command.servo_angle_rad for command in stall_commands], [1.6, 1.6, 1.8])

    def test_policy_controller_publishes_every_timestamp_and_reuses_angle_between_intervals(self) -> None:
        now = 100.0

        def clock() -> float:
            return now

        controller = PolicyController(RandomPolicy(random.Random(1)), clock=clock)

        first = controller.handle_payload('{"x": 1.0, "y": 2.0, "visible": true, "timestamp_ns": 1}')
        first_command = json.loads(first)
        self.assertEqual(first_command["timestamp_ns"], 1)

        now = 100.49
        second = controller.handle_payload('{"x": 1.0, "y": 2.0, "visible": true, "timestamp_ns": 2}')
        second_command = json.loads(second)
        self.assertEqual(second_command["timestamp_ns"], 2)
        self.assertEqual(second_command["servo_angle_rad"], first_command["servo_angle_rad"])

        now = 100.5
        third = controller.handle_payload('{"x": 1.0, "y": 2.0, "visible": true, "timestamp_ns": 3}')
        third_command = json.loads(third)
        self.assertEqual(third_command["timestamp_ns"], 3)
        self.assertNotEqual(third_command["servo_angle_rad"], first_command["servo_angle_rad"])

    def test_rejects_invalid_position_payload(self) -> None:
        with self.assertRaises(ValueError):
            parse_position_json('{"x": "1", "y": 2.0, "visible": true, "timestamp_ns": 123}')

        with self.assertRaises(ValueError):
            parse_position_json('{"x": 1.0, "y": 2.0, "visible": true, "timestamp_ns": 1.5}')

        with self.assertRaises(ValueError):
            parse_position_json('{"x": 1.0, "y": 2.0, "timestamp_ns": 123}')

        with self.assertRaises(ValueError):
            parse_position_json('{"x": 1.0, "y": 2.0, "visible": 1, "timestamp_ns": 123}')


if __name__ == "__main__":
    unittest.main()
