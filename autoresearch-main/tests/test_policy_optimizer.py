import csv
import json
import os
import sys
import tempfile
import types
from pathlib import Path
import unittest
from unittest import mock

from policy_optimizer import raindrop_monitoring
from policy_optimizer.app import analyze_episode_logs, episode_paths_from_mcp, recommend_with_llm
from policy_optimizer.modal_csv_analyzer import _build_csv_analysis_prompt, _build_synthesis_prompt, _build_worker_payload


class PolicyOptimizerTest(unittest.TestCase):
    def test_analyze_episode_logs_prefers_positive_x_velocity_with_lower_angle_travel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.csv"
            poor = Path(tmp) / "poor.csv"
            _write_episode(
                good,
                [
                    (0, 0.1, 0.0, 1.0),
                    (1, 0.4, 0.0, 1.1),
                    (2, 0.8, 0.0, 1.2),
                ],
            )
            _write_episode(
                poor,
                [
                    (0, 0.8, 0.0, 0.0),
                    (1, 0.7, 0.0, 3.0),
                    (2, 0.6, 0.0, 0.0),
                ],
            )

            recommendation = analyze_episode_logs([poor, good])

            self.assertEqual(recommendation.best_episode.path, good)
            self.assertIn("OptimizedPolicy", recommendation.allowed_edit_target)
            self.assertGreater(recommendation.analyses[0].score, recommendation.analyses[1].score)

    def test_recommendation_json_includes_allowed_edit_target(self) -> None:
        recommendation = analyze_episode_logs([])

        payload = json.loads(recommendation.to_json())

        self.assertEqual(payload["allowed_edit_target"], "src/policy_controller/policy.py:OptimizedPolicy")
        self.assertIsNone(payload["best_episode"])

    def test_episode_paths_from_mcp_uses_episode_manager_tool(self) -> None:
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"structuredContent": {"paths": ["/tmp/done.csv"]}},
        }
        completed = mock.Mock(returncode=0, stdout=json.dumps(response) + "\n", stderr="")

        with mock.patch("policy_optimizer.app.subprocess.run", return_value=completed) as run:
            self.assertEqual(episode_paths_from_mcp(), [Path("/tmp/done.csv")])

        _, kwargs = run.call_args
        self.assertEqual(json.loads(kwargs["input"])["params"]["name"], "list_episode_logs")
        self.assertEqual(run.call_args.args[0][1:], ["-m", "episode_manager.app", "--mcp"])

    def test_recommend_with_llm_sends_csv_contents_to_openai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode.csv"
            path.write_text("timestamp_ns,x,y,servo_angle_rad\n0,0.1,0.0,1.0\n", encoding="utf-8")

            fake_response = types.SimpleNamespace(output_text="Use a smooth rising angle schedule.")
            fake_client = mock.Mock()
            fake_client.responses.create.return_value = fake_response
            fake_openai_module = types.SimpleNamespace(OpenAI=mock.Mock(return_value=fake_client))

            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False):
                with mock.patch.dict(sys.modules, {"openai": fake_openai_module}):
                    with mock.patch("policy_optimizer.app.track_llm_call") as track_llm_call:
                        summary = recommend_with_llm(
                            [path],
                            "test-model",
                            7.0,
                            max_files=1,
                            max_chars_per_file=3000,
                            max_output_tokens=123,
                            reasoning_effort="low",
                        )

            self.assertEqual(summary, "Use a smooth rising angle schedule.")
            _, kwargs = fake_client.responses.create.call_args
            self.assertEqual(kwargs["model"], "test-model")
            self.assertIn("timestamp_ns,x,y,servo_angle_rad", kwargs["input"])
            self.assertEqual(kwargs["max_output_tokens"], 123)
            self.assertEqual(kwargs["reasoning"], {"effort": "low"})
            fake_openai_module.OpenAI.assert_called_once_with(timeout=7.0, max_retries=0)
            _, track_kwargs = track_llm_call.call_args
            self.assertEqual(track_kwargs["model"], "test-model")
            self.assertIn("timestamp_ns,x,y,servo_angle_rad", track_kwargs["prompt"])

    def test_raindrop_monitoring_is_noop_without_environment(self) -> None:
        @raindrop_monitoring.tool("unit_test_tool")
        def sample(value: int) -> int:
            return value + 1

        self.assertEqual(sample(1), 2)

    def test_raindrop_monitoring_uses_non_empty_values(self) -> None:
        fake_raindrop = mock.Mock()

        with mock.patch("policy_optimizer.raindrop_monitoring._init_raindrop", return_value=fake_raindrop):
            raindrop_monitoring.track_llm_call(model="test", prompt="", output="", event="empty_event")

        _, kwargs = fake_raindrop.track_ai.call_args
        self.assertEqual(kwargs["input"], "empty_event")
        self.assertEqual(kwargs["output"], "completed")

    def test_modal_worker_prompt_contains_metrics_and_clips_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode.csv"
            path.write_text("timestamp_ns,x,y,servo_angle_rad\n0,0.1,0.0,1.0\n1,0.2,0.0,1.1\n", encoding="utf-8")
            analysis = analyze_episode_logs([path]).analyses[0]

            prompt = _build_csv_analysis_prompt(analysis, max_chars=35)
            payload = _build_worker_payload(
                analysis,
                worker_model="test-model",
                worker_timeout_s=3.0,
                worker_max_output_tokens=99,
                worker_reasoning_effort="low",
                max_chars_per_csv=35,
            )

            self.assertIn("positive_velocity_x_gain", prompt)
            self.assertIn("...TRUNCATED...", prompt)
            self.assertEqual(payload["path"], str(path))
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["max_output_tokens"], 99)

    def test_modal_synthesis_prompt_includes_findings(self) -> None:
        prompt = _build_synthesis_prompt([{"path": "episode.csv", "finding": "hold angle near 2.7"}])

        self.assertIn("OptimizedPolicy", prompt)
        self.assertIn("hold angle near 2.7", prompt)


def _write_episode(path: Path, rows: list[tuple[int, float, float, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp_ns", "x", "y", "servo_angle_rad"])
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
