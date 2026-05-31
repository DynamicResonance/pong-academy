from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterable

from policy_optimizer.raindrop_monitoring import (
    begin_interaction,
    finish_interaction,
    flush_and_shutdown,
    load_env_file,
    tool,
    track_llm_call,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EpisodeSample:
    timestamp_ns: int
    x: float
    y: float
    servo_angle_rad: float


@dataclass(frozen=True)
class EpisodeAnalysis:
    path: Path
    sample_count: int
    positive_velocity_sample_count: int
    max_x_during_positive_velocity: float
    positive_velocity_x_gain: float
    absolute_angle_travel: float
    score: float


@dataclass(frozen=True)
class OptimizerRecommendation:
    best_episode: EpisodeAnalysis | None
    analyses: list[EpisodeAnalysis]
    summary: str
    allowed_edit_target: str = "src/policy_controller/policy.py:OptimizedPolicy"

    def to_json(self) -> str:
        return json.dumps(
            {
                "allowed_edit_target": self.allowed_edit_target,
                "summary": self.summary,
                "best_episode": _analysis_to_json(self.best_episode),
                "episodes": [_analysis_to_json(analysis) for analysis in self.analyses],
            },
            separators=(",", ":"),
        )


@tool("episode_manager_mcp_list_episode_logs")
def episode_paths_from_mcp() -> list[Path]:
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_episode_logs"}}
    LOGGER.debug("requesting completed episode logs from episode manager MCP server")
    completed = subprocess.run(
        [sys.executable, "-m", "episode_manager.app", "--mcp"],
        input=json.dumps(request, separators=(",", ":")) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    LOGGER.debug("episode manager MCP server exited returncode=%d", completed.returncode)
    if completed.returncode != 0:
        raise RuntimeError(f"episode manager MCP server failed: {completed.stderr.strip()}")
    response = _parse_mcp_response(completed.stdout)
    if response is None or "error" in response:
        raise RuntimeError(f"failed to list episode logs via MCP: {response}")
    result = response["result"]
    structured = result.get("structuredContent", {})
    paths = structured.get("paths", [])
    if not isinstance(paths, list):
        raise ValueError("episode MCP response structuredContent.paths must be a list")
    LOGGER.debug("episode manager MCP returned completed_csv_count=%d", len(paths))
    return [Path(path) for path in paths]


@tool("analyze_episode_logs")
def analyze_episode_logs(paths: Iterable[Path | str]) -> OptimizerRecommendation:
    analyses = [analyze_episode_log(Path(path)) for path in paths]
    analyses.sort(key=lambda analysis: analysis.score, reverse=True)
    best = analyses[0] if analyses else None

    if best is None:
        summary = (
            "No completed episode logs were provided. Keep OptimizedPolicy unchanged until "
            "there is data with positive x velocity."
        )
    else:
        summary = (
            "Update only OptimizedPolicy. Favor servo_angle_rad patterns similar to the "
            f"best episode ({best.path}) during positive x velocity: max_x={best.max_x_during_positive_velocity:.6f}, "
            f"x_gain={best.positive_velocity_x_gain:.6f}, angle_travel={best.absolute_angle_travel:.6f}."
        )

    return OptimizerRecommendation(best_episode=best, analyses=analyses, summary=summary)


@tool("recommend_with_llm")
def recommend_with_llm(
    paths: Iterable[Path | str],
    model: str,
    timeout_s: float,
    *,
    max_files: int,
    max_chars_per_file: int,
    max_output_tokens: int,
    reasoning_effort: str,
) -> str:
    if max_output_tokens < 16:
        raise ValueError("max_output_tokens must be at least 16")

    started = time.monotonic()
    path_list = [Path(path) for path in paths]
    analyses = analyze_episode_logs(path_list).analyses
    selected_paths = [analysis.path for analysis in analyses[:max_files]]
    prompt = _build_llm_prompt(selected_paths, analyses=analyses, max_chars_per_file=max_chars_per_file)
    load_env_file()
    LOGGER.debug("loaded .env openai_key_present=%s", bool(os.getenv("OPENAI_API_KEY")))
    if not os.getenv("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set; cannot run LLM analysis."

    LOGGER.debug("importing OpenAI client")
    from openai import OpenAI

    LOGGER.debug("creating OpenAI client")
    client = OpenAI(timeout=timeout_s, max_retries=0)
    try:
        LOGGER.info(
            "calling OpenAI responses.create model=%s reasoning_effort=%s timeout_s=%.3f selected_csv_count=%d total_csv_count=%d prompt_chars=%d max_output_tokens=%d",
            model,
            reasoning_effort,
            timeout_s,
            len(selected_paths),
            len(path_list),
            len(prompt),
            max_output_tokens,
        )
        response = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": reasoning_effort},
        )
        output = response.output_text
    except Exception as error:
        elapsed_s = time.monotonic() - started
        LOGGER.error(
            "OpenAI analysis failed model=%s timeout_s=%.3f elapsed_s=%.3f error_type=%s error=%s",
            model,
            timeout_s,
            elapsed_s,
            type(error).__name__,
            error,
        )
        track_llm_call(model=model, prompt=prompt, output=f"error: {error}")
        raise
    elapsed_s = time.monotonic() - started
    LOGGER.info("OpenAI analysis completed model=%s elapsed_s=%.3f output_chars=%d", model, elapsed_s, len(output))
    track_llm_call(model=model, prompt=prompt, output=output)
    return output


@tool("analyze_episode_log")
def analyze_episode_log(path: Path) -> EpisodeAnalysis:
    samples = _read_samples(path)
    if len(samples) < 2:
        return EpisodeAnalysis(
            path=path,
            sample_count=len(samples),
            positive_velocity_sample_count=0,
            max_x_during_positive_velocity=0.0,
            positive_velocity_x_gain=0.0,
            absolute_angle_travel=0.0,
            score=0.0,
        )

    positive_velocity_x_values: list[float] = []
    positive_velocity_x_gain = 0.0
    absolute_angle_travel = 0.0

    for previous, current in zip(samples, samples[1:]):
        dx = current.x - previous.x
        dt = current.timestamp_ns - previous.timestamp_ns
        absolute_angle_travel += abs(current.servo_angle_rad - previous.servo_angle_rad)
        if dt <= 0:
            continue
        if dx / dt > 0.0:
            positive_velocity_x_values.append(current.x)
            positive_velocity_x_gain += dx

    max_x = max(positive_velocity_x_values, default=0.0)
    score = max_x + positive_velocity_x_gain - 0.1 * absolute_angle_travel
    return EpisodeAnalysis(
        path=path,
        sample_count=len(samples),
        positive_velocity_sample_count=len(positive_velocity_x_values),
        max_x_during_positive_velocity=max_x,
        positive_velocity_x_gain=positive_velocity_x_gain,
        absolute_angle_travel=absolute_angle_travel,
        score=score,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze episode CSVs and recommend OptimizedPolicy changes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--model", default="gpt-5-mini", help="OpenAI model used for CSV analysis.")
    parser.add_argument("--llm-timeout", type=float, default=15.0, help="OpenAI request timeout in seconds.")
    parser.add_argument("--llm-max-files", type=int, default=5, help="Maximum ranked episode CSVs sent to OpenAI.")
    parser.add_argument(
        "--llm-max-chars-per-file",
        type=int,
        default=1200,
        help="Maximum CSV characters sent to OpenAI for each selected episode.",
    )
    parser.add_argument("--llm-max-output-tokens", type=int, default=800, help="Maximum OpenAI output tokens.")
    parser.add_argument(
        "--llm-reasoning-effort",
        default="low",
        choices=["minimal", "low", "medium", "high"],
        help="Reasoning effort sent to OpenAI reasoning models.",
    )
    parser.add_argument("--no-llm", action="store_true", help="Skip the OpenAI analysis call.")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Minimum log level to emit.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    for noisy_logger in ("httpcore", "httpx", "openai", "raindrop", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    optimizer_interaction = None
    paths = episode_paths_from_mcp()
    recommendation = analyze_episode_logs(paths)
    optimizer_interaction = begin_interaction(
        event="policy_optimizer_run",
        input_value="Analyze completed episode CSVs via MCP and recommend OptimizedPolicy changes.",
        properties={
            "completed_csv_count": len(paths),
            "model": args.model,
            "llm_enabled": not args.no_llm,
        },
    )
    llm_summary = None
    llm_error = None
    if not args.no_llm:
        try:
            llm_summary = recommend_with_llm(
                paths,
                args.model,
                args.llm_timeout,
                max_files=args.llm_max_files,
                max_chars_per_file=args.llm_max_chars_per_file,
                max_output_tokens=args.llm_max_output_tokens,
                reasoning_effort=args.llm_reasoning_effort,
            )
        except Exception as error:
            llm_error = str(error)
    try:
        if args.json:
            payload = json.loads(recommendation.to_json())
            payload["llm_summary"] = llm_summary
            payload["llm_error"] = llm_error
            finish_interaction(optimizer_interaction, output=llm_summary or recommendation.summary)
            print(json.dumps(payload, separators=(",", ":")))
            if llm_error is not None:
                raise SystemExit(1)
        else:
            if llm_error is not None:
                finish_interaction(optimizer_interaction, output=f"error: {llm_error}")
                raise RuntimeError(f"LLM analysis failed: {llm_error}")
            finish_interaction(optimizer_interaction, output=llm_summary or recommendation.summary)
            print(llm_summary or recommendation.summary)
    finally:
        flush_and_shutdown()


@tool("read_episode_csv")
def _read_samples(path: Path) -> list[EpisodeSample]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return [
            EpisodeSample(
                timestamp_ns=int(row["timestamp_ns"]),
                x=_finite_float(row["x"], "x"),
                y=_finite_float(row["y"], "y"),
                servo_angle_rad=_finite_float(row["servo_angle_rad"], "servo_angle_rad"),
            )
            for row in reader
        ]


def _finite_float(value: object, field: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _analysis_to_json(analysis: EpisodeAnalysis | None) -> dict[str, object] | None:
    if analysis is None:
        return None
    return {
        "path": str(analysis.path),
        "sample_count": analysis.sample_count,
        "positive_velocity_sample_count": analysis.positive_velocity_sample_count,
        "max_x_during_positive_velocity": analysis.max_x_during_positive_velocity,
        "positive_velocity_x_gain": analysis.positive_velocity_x_gain,
        "absolute_angle_travel": analysis.absolute_angle_travel,
        "score": analysis.score,
    }


def _parse_mcp_response(output: str) -> dict[str, object] | None:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        message = json.loads(stripped)
        if not isinstance(message, dict):
            raise ValueError("episode manager MCP response must be a JSON object")
        return message
    return None


def _build_llm_prompt(
    paths: list[Path],
    *,
    analyses: list[EpisodeAnalysis] | None = None,
    max_chars_per_file: int = 3000,
) -> str:
    files = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file] + "\n...TRUNCATED..."
        files.append(f"FILE: {path}\n{text}")

    metrics = ""
    if analyses is not None:
        metrics_rows = [
            "path,sample_count,positive_velocity_sample_count,max_x_during_positive_velocity,"
            "positive_velocity_x_gain,absolute_angle_travel,score"
        ]
        for analysis in analyses:
            metrics_rows.append(
                f"{analysis.path},{analysis.sample_count},{analysis.positive_velocity_sample_count},"
                f"{analysis.max_x_during_positive_velocity:.9f},{analysis.positive_velocity_x_gain:.9f},"
                f"{analysis.absolute_angle_travel:.9f},{analysis.score:.9f}"
            )
        metrics = "Ranked episode metrics:\n" + "\n".join(metrics_rows) + "\n\n"

    return (
        "Analyze these episode CSV logs and recommend how to update only "
        "src/policy_controller/policy.py:OptimizedPolicy.\n"
        "Objective:\n"
        "- During periods where x velocity > 0, maximize x position.\n"
        "- Ignore phases where x velocity < 0 with high x.\n"
        "- Minimize absolute servo angle travel as a secondary objective.\n"
        "- servo_angle_rad is the only variable that may be changed.\n"
        "Return concise implementation guidance for OptimizedPolicy only.\n\n"
        + metrics
        + "Selected CSV excerpts:\n"
        + "\n\n".join(files)
    )


if __name__ == "__main__":
    main()
