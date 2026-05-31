from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable

import modal

from policy_optimizer.app import EpisodeAnalysis, analyze_episode_logs, episode_paths_from_mcp
from policy_optimizer.raindrop_monitoring import (
    begin_interaction,
    finish_interaction,
    flush_and_shutdown,
    load_env_file,
    track_llm_call,
)


LOGGER = logging.getLogger(__name__)

modal_app = modal.App("autoresearch-csv-analyzer")
modal_image = modal.Image.debian_slim(python_version="3.13").pip_install("openai>=2.0.0")
modal_secret = modal.Secret.from_dotenv()


@modal_app.function(
    image=modal_image,
    secrets=[modal_secret],
    timeout=90,
    cpu=0.25,
    memory=512,
    max_containers=1,
)
def analyze_csv_remote(payload: dict[str, Any]) -> dict[str, Any]:
    from openai import OpenAI

    prompt = payload["prompt"]
    model = payload["model"]
    response = OpenAI(timeout=payload["timeout_s"], max_retries=0).responses.create(
        model=model,
        input=prompt,
        max_output_tokens=payload["max_output_tokens"],
        reasoning={"effort": payload["reasoning_effort"]},
    )
    output = response.output_text.strip() or "No finding returned by worker model."
    return {
        "path": payload["path"],
        "model": model,
        "prompt": prompt,
        "finding": output,
    }


def run_modal_csv_analysis(
    paths: Iterable[Path],
    analyses: list[EpisodeAnalysis],
    *,
    worker_model: str,
    worker_timeout_s: float,
    worker_max_output_tokens: int,
    worker_reasoning_effort: str,
    max_chars_per_csv: int,
) -> list[dict[str, Any]]:
    if worker_max_output_tokens < 16:
        raise ValueError("worker_max_output_tokens must be at least 16")
    path_set = set(paths)
    payloads = [
        _build_worker_payload(
            analysis,
            worker_model=worker_model,
            worker_timeout_s=worker_timeout_s,
            worker_max_output_tokens=worker_max_output_tokens,
            worker_reasoning_effort=worker_reasoning_effort,
            max_chars_per_csv=max_chars_per_csv,
        )
        for analysis in analyses
        if analysis.path in path_set
    ]
    interactions = {
        payload["path"]: begin_interaction(
            event="modal_csv_analysis",
            input_value=f"Analyze one completed episode CSV on Modal: {payload['path']}",
            properties={
                "path": payload["path"],
                "model": worker_model,
                "prompt_chars": len(payload["prompt"]),
            },
        )
        for payload in payloads
    }

    findings: list[dict[str, Any]] = []
    with modal_app.run():
        for payload, result in zip(payloads, analyze_csv_remote.map(payloads, return_exceptions=True)):
            if isinstance(result, Exception):
                finding = {
                    "path": payload["path"],
                    "model": worker_model,
                    "prompt": payload["prompt"],
                    "finding": "",
                    "error": str(result),
                }
                finish_interaction(interactions[payload["path"]], output=f"error: {result}")
            else:
                finding = dict(result)
                finding["finding"] = str(finding.get("finding") or "No finding returned by worker model.")
                finish_interaction(interactions[payload["path"]], output=finding["finding"])
            track_llm_call(
                model=worker_model,
                prompt=finding["prompt"],
                output=finding.get("finding") or f"error: {finding.get('error', '')}",
                event="modal_csv_analysis_llm",
            )
            findings.append(_public_finding(finding))

    return findings


def synthesize_findings(
    findings: list[dict[str, Any]],
    *,
    model: str,
    timeout_s: float,
    max_output_tokens: int,
    reasoning_effort: str,
) -> str:
    prompt = _build_synthesis_prompt(findings)
    interaction = begin_interaction(
        event="modal_csv_analysis_synthesis",
        input_value="Synthesize per-CSV Modal findings into one OptimizedPolicy recommendation.",
        properties={
            "model": model,
            "finding_count": len(findings),
            "prompt_chars": len(prompt),
        },
    )
    try:
        from openai import OpenAI

        response = OpenAI(timeout=timeout_s, max_retries=0).responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": reasoning_effort},
        )
        output = response.output_text.strip() or "No synthesis returned by model."
    except Exception as error:
        finish_interaction(interaction, output=f"error: {error}")
        track_llm_call(model=model, prompt=prompt, output=f"error: {error}", event="modal_csv_synthesis_llm")
        raise

    finish_interaction(interaction, output=output)
    track_llm_call(model=model, prompt=prompt, output=output, event="modal_csv_synthesis_llm")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze each episode CSV on Modal, then synthesize findings locally.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--worker-model", default="gpt-5-mini", help="Lightweight model used for each Modal CSV task.")
    parser.add_argument("--synthesis-model", default="gpt-5-mini", help="Model used for the local synthesis step.")
    parser.add_argument("--worker-timeout", type=float, default=30.0, help="OpenAI timeout inside each Modal worker.")
    parser.add_argument("--synthesis-timeout", type=float, default=30.0, help="OpenAI timeout for synthesis.")
    parser.add_argument("--worker-max-output-tokens", type=int, default=400, help="Output token cap per CSV worker.")
    parser.add_argument("--synthesis-max-output-tokens", type=int, default=1000, help="Output token cap for synthesis.")
    parser.add_argument("--worker-reasoning-effort", default="low", choices=["minimal", "low", "medium", "high"])
    parser.add_argument("--synthesis-reasoning-effort", default="low", choices=["minimal", "low", "medium", "high"])
    parser.add_argument("--max-chars-per-csv", type=int, default=3000, help="Maximum CSV characters sent per worker.")
    parser.add_argument("--max-csvs", type=int, default=0, help="Optional cap on CSV count for debugging. 0 means all.")
    parser.add_argument("--no-synthesis", action="store_true", help="Skip synthesis after per-CSV Modal analysis.")
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
    load_env_file()

    started = time.monotonic()
    paths = episode_paths_from_mcp()
    if args.max_csvs > 0:
        paths = paths[: args.max_csvs]
    analyses = analyze_episode_logs(paths).analyses
    LOGGER.info("modal CSV analysis starting csv_count=%d", len(paths))

    orchestrator_interaction = begin_interaction(
        event="modal_csv_analyzer_run",
        input_value="Analyze completed episode CSVs on Modal and synthesize findings.",
        properties={
            "csv_count": len(paths),
            "worker_model": args.worker_model,
            "synthesis_model": args.synthesis_model,
        },
    )

    synthesis = None
    error = None
    try:
        findings = run_modal_csv_analysis(
            paths,
            analyses,
            worker_model=args.worker_model,
            worker_timeout_s=args.worker_timeout,
            worker_max_output_tokens=args.worker_max_output_tokens,
            worker_reasoning_effort=args.worker_reasoning_effort,
            max_chars_per_csv=args.max_chars_per_csv,
        )
        if not args.no_synthesis:
            synthesis = synthesize_findings(
                findings,
                model=args.synthesis_model,
                timeout_s=args.synthesis_timeout,
                max_output_tokens=args.synthesis_max_output_tokens,
                reasoning_effort=args.synthesis_reasoning_effort,
            )
    except Exception as caught:
        findings = locals().get("findings", [])
        error = str(caught)
        finish_interaction(orchestrator_interaction, output=f"error: {error}")
    else:
        finish_interaction(orchestrator_interaction, output=synthesis or f"analyzed {len(findings)} CSVs")
    finally:
        flush_and_shutdown()

    payload = {
        "csv_count": len(paths),
        "elapsed_s": round(time.monotonic() - started, 3),
        "findings": findings,
        "synthesis": synthesis,
        "error": error,
    }
    if args.json:
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(synthesis or json.dumps(payload, indent=2))
    if error is not None:
        raise SystemExit(1)


def _build_worker_payload(
    analysis: EpisodeAnalysis,
    *,
    worker_model: str,
    worker_timeout_s: float,
    worker_max_output_tokens: int,
    worker_reasoning_effort: str,
    max_chars_per_csv: int,
) -> dict[str, Any]:
    prompt = _build_csv_analysis_prompt(analysis, max_chars=max_chars_per_csv)
    return {
        "path": str(analysis.path),
        "model": worker_model,
        "timeout_s": worker_timeout_s,
        "max_output_tokens": worker_max_output_tokens,
        "reasoning_effort": worker_reasoning_effort,
        "prompt": prompt,
    }


def _build_csv_analysis_prompt(analysis: EpisodeAnalysis, *, max_chars: int) -> str:
    csv_text = analysis.path.read_text(encoding="utf-8")
    if len(csv_text) > max_chars:
        csv_text = csv_text[:max_chars] + "\n...TRUNCATED..."
    return (
        "Analyze this single episode CSV for policy optimization.\n"
        "Objective: maximize x during periods where x velocity > 0, ignore high-x phases where x velocity < 0, "
        "and minimize absolute servo_angle_rad travel as a secondary objective.\n"
        "Return concise findings: useful servo patterns, harmful patterns, and whether this episode should influence "
        "src/policy_controller/policy.py:OptimizedPolicy.\n\n"
        "Episode metrics:\n"
        f"path={analysis.path}\n"
        f"sample_count={analysis.sample_count}\n"
        f"positive_velocity_sample_count={analysis.positive_velocity_sample_count}\n"
        f"max_x_during_positive_velocity={analysis.max_x_during_positive_velocity:.9f}\n"
        f"positive_velocity_x_gain={analysis.positive_velocity_x_gain:.9f}\n"
        f"absolute_angle_travel={analysis.absolute_angle_travel:.9f}\n"
        f"score={analysis.score:.9f}\n\n"
        "CSV excerpt:\n"
        f"{csv_text}"
    )


def _build_synthesis_prompt(findings: list[dict[str, Any]]) -> str:
    return (
        "Synthesize these per-CSV remote findings into one concise OptimizedPolicy implementation recommendation.\n"
        "Only OptimizedPolicy in src/policy_controller/policy.py may be changed. servo_angle_rad is the only control variable.\n"
        "Prefer patterns that improve x while x velocity > 0 and avoid unnecessary absolute servo travel.\n\n"
        + json.dumps(findings, separators=(",", ":"))
    )


def _public_finding(finding: dict[str, Any]) -> dict[str, Any]:
    public = dict(finding)
    public.pop("prompt", None)
    return public


if __name__ == "__main__":
    main()
