from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .analysis import analyze
from .config import load_config
from .provider import ProviderRateLimitError, create_provider
from .runner import result_path, run_trial, save_result
from .upstream import build_fixture, load_fixture, save_fixture


ROOT = Path(__file__).resolve().parents[2]


def _settings(config_path: str) -> dict:
    return load_config(config_path)


def doctor(config_path: str) -> None:
    settings = _settings(config_path)
    provider = create_provider(settings)
    print(json.dumps(provider.doctor(), indent=2, default=str))


def generate(config_path: str) -> None:
    settings = _settings(config_path)
    directory = ROOT / str(settings.get("fixtures_dir", "data/frozen_tasks"))
    directory.mkdir(parents=True, exist_ok=True)
    created = 0
    for item in settings["configs"]:
        for seed in settings["graph_seeds"]:
            fixture = build_fixture(item, int(seed), settings)
            save_fixture(fixture, directory)
            created += 1
    print(f"Generated {created} frozen task fixtures in {directory}")


def preflight(config_path: str) -> None:
    """Confirm hosted structured tool calling without touching benchmark files."""
    settings = _settings(config_path)
    provider = create_provider(settings)
    turn = provider.chat(
        [{"role": "user", "content": "Call the provided tool once with value 7. Do not explain."}],
        [{
            "type": "function",
            "function": {
                "name": "statebench_preflight",
                "description": "Records a supplied integer for a connection test.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }],
    )
    print(json.dumps({
        "model": settings["model"],
        "tool_call_count": len(turn.tool_calls),
        "tool_calls": turn.tool_calls,
        "prompt_tokens": turn.prompt_tokens,
        "completion_tokens": turn.completion_tokens,
    }, indent=2))
    if not turn.tool_calls:
        raise RuntimeError("Preflight failed: the model returned no structured tool call.")


def run(config_path: str, resume: bool) -> None:
    settings = _settings(config_path)
    provider = create_provider(settings)
    fixture_dir = ROOT / str(settings.get("fixtures_dir", "data/frozen_tasks"))
    fixtures = [load_fixture(path) for path in sorted(fixture_dir.glob("*.json"))]
    task_prefix = settings.get("task_prefix")
    if task_prefix:
        fixtures = [fixture for fixture in fixtures if fixture.task_id.startswith(str(task_prefix))]
    task_ids = settings.get("task_ids")
    if task_ids:
        by_id = {fixture.task_id: fixture for fixture in fixtures}
        missing = [task_id for task_id in task_ids if task_id not in by_id]
        if missing:
            raise RuntimeError(f"Configured task IDs are missing from fixtures: {missing}")
        fixtures = [by_id[task_id] for task_id in task_ids]
    results_dir = str(settings.get("results_dir", "results/raw"))
    retry_failure_codes = set(settings.get("retry_failure_codes", []))
    if not fixtures:
        raise RuntimeError("No frozen tasks found. Run generate first.")
    for fixture in fixtures:
        for strategy in settings["strategies"]:
            destination = result_path(ROOT, fixture.task_id, strategy, results_dir)
            if resume and destination.exists():
                existing = json.loads(destination.read_text(encoding="utf-8"))
                existing_failures = set(existing.get("failure_codes", []))
                if not (existing_failures & retry_failure_codes):
                    continue
                print(
                    f"Retrying {fixture.task_id} / {strategy} after configured transient failure: "
                    f"{', '.join(sorted(existing_failures & retry_failure_codes))}",
                    flush=True,
                )
            print(f"Running {fixture.task_id} / {strategy}", flush=True)
            # A local Ollama daemon can be briefly unavailable after a model or
            # GPU worker restart. Completed trials are already on disk; retrying
            # an unfinished trial is therefore safe and keeps a long batch run
            # from failing because of one transient connection error.
            for attempt in range(1, 4):
                try:
                    save_result(ROOT, run_trial(fixture, strategy, provider), results_dir)
                    break
                except ProviderRateLimitError as error:
                    # Do not write a partial trial. `--resume` will begin at
                    # this exact fixture/strategy after the quota resets.
                    retry_hint = (
                        f" Retry after about {error.retry_after_seconds}s."
                        if error.retry_after_seconds is not None
                        else " Resume after the provider quota resets."
                    )
                    print(f"Groq quota reached at {fixture.task_id} / {strategy}.{retry_hint}", flush=True)
                    return
                except ConnectionError:
                    if attempt == 3:
                        raise
                    delay = 15 * attempt
                    print(f"Ollama unavailable; retrying this trial in {delay}s (attempt {attempt}/3)", flush=True)
                    time.sleep(delay)


def assess(config_path: str) -> None:
    """Report the pre-registered model-viability gate from saved trial files."""
    settings = _settings(config_path)
    results_dir = ROOT / str(settings.get("results_dir", "results/raw"))
    gate = settings.get("viability_gate") or {}
    if not gate:
        raise RuntimeError("This configuration has no viability_gate.")
    checks: list[dict[str, object]] = []
    for check in gate["checks"]:
        records = []
        for task_id in check["task_ids"]:
            path = result_path(ROOT, task_id, check["strategy"], str(settings.get("results_dir", "results/raw")))
            if path.exists():
                records.append(json.loads(path.read_text(encoding="utf-8")))
        successes = sum(bool(record["success"]) for record in records)
        checks.append({
            "name": check["name"],
            "completed": len(records),
            "expected": len(check["task_ids"]),
            "successes": successes,
            "minimum_successes": check["minimum_successes"],
            "passed": len(records) == len(check["task_ids"]) and successes >= check["minimum_successes"],
        })
    complete = all(check["completed"] == check["expected"] for check in checks)
    viable = complete and all(check["passed"] for check in checks)
    print(json.dumps({"complete": complete, "viable_for_full_panel": viable, "checks": checks}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="statebench")
    parser.add_argument("command", choices=["doctor", "preflight", "generate", "run", "assess", "analyze", "verify"])
    parser.add_argument("--config", default="configs/final.yaml")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        doctor(args.config)
    elif args.command == "preflight":
        preflight(args.config)
    elif args.command == "generate":
        generate(args.config)
    elif args.command == "run":
        run(args.config, args.resume)
    elif args.command == "assess":
        assess(args.config)
    elif args.command == "analyze":
        _, summary = analyze(
            ROOT,
            str(_settings(args.config).get("results_dir", "results/raw")),
            str(_settings(args.config).get("processed_dir", "results/processed")),
            str(_settings(args.config).get("figures_dir", "results/figures")),
            _settings(args.config).get("task_ids"),
            _settings(args.config).get("strategies"),
        )
        print(summary.to_string(index=False))
    else:
        fixtures = list((ROOT / str(_settings(args.config).get("fixtures_dir", "data/frozen_tasks"))).glob("*.json"))
        print(f"fixtures={len(fixtures)}")


if __name__ == "__main__":
    main()
