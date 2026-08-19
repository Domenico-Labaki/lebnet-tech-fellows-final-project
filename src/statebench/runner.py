from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import TaskFixture, TrialResult
from .provider import GroqProvider, OllamaProvider, ProviderToolValidationError
from .state import create_strategy
from .upstream import EvaluatorAdapter, input_prompt


FINAL_NUMBER = re.compile(r"(?<!\d)(-?\d+)(?!\d)")


def _assistant_message(turn: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.content or ""}
    if turn.tool_calls:
        message["tool_calls"] = turn.tool_calls
    return message


def _call_name(call: dict[str, Any]) -> str:
    return str(call.get("function", {}).get("name", ""))


def _call_arguments(call: dict[str, Any]) -> dict[str, Any]:
    arguments = call.get("function", {}).get("arguments", {})
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {}
    return dict(arguments or {})


def _predicted_value(content: str) -> str | None:
    values = FINAL_NUMBER.findall(content)
    return values[-1] if values else None


def run_trial(fixture: TaskFixture, strategy_name: str, provider: OllamaProvider | GroqProvider) -> TrialResult:
    strategy = create_strategy(strategy_name)
    strategy.reset(fixture)
    evaluator = EvaluatorAdapter(fixture, str(provider.settings.get("on_wrong_inputs", "Execute")))
    messages: list[dict[str, Any]] = []
    system_prompt = provider.settings.get("system_prompt")
    if system_prompt:
        messages.append({"role": "system", "content": str(system_prompt)})
    messages.append({"role": "user", "content": input_prompt(fixture)})
    requests: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    failures: list[str] = []
    calls = 0
    state_bytes = 0
    final_content = ""

    while calls < fixture.call_budget:
        try:
            turn = provider.chat(messages, fixture.tool_schemas)
        except ProviderToolValidationError as error:
            # Groq validates tool names before returning an assistant message.
            # Persist this as a model failure rather than losing the entire
            # trial/batch; the original evaluator would likewise reject an
            # unknown function name.
            failures.append("provider_rejected_invalid_tool_call")
            requests.append(
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "latency_seconds": 0,
                    "tool_call_count": 0,
                    "raw": {"provider_error": str(error)},
                }
            )
            break
        exhausted_output_budget = (
            not turn.tool_calls
            and not turn.content
            and turn.completion_tokens >= int(provider.settings["num_predict"])
        )
        requests.append(
            {
                "prompt_tokens": turn.prompt_tokens,
                "completion_tokens": turn.completion_tokens,
                "latency_seconds": turn.latency_seconds,
                "tool_call_count": len(turn.tool_calls),
                "raw": turn.raw,
            }
        )
        messages.append(_assistant_message(turn))
        if not turn.tool_calls:
            if exhausted_output_budget:
                failures.append("thinking_output_budget_exhausted")
            final_content = turn.content
            break
        for call in turn.tool_calls:
            if calls >= fixture.call_budget:
                failures.append("call_budget_exhausted")
                break
            calls += 1
            tool_name = _call_name(call)
            arguments = _call_arguments(call)
            event = evaluator.execute(calls, tool_name, arguments)
            if event.failure_code:
                failures.append(event.failure_code)
            strategy.observe(event)
            suffix = strategy.render_suffix(event)
            state_bytes += len(suffix.encode("utf-8"))
            visible_content = event.result_text + suffix
            tool_message: dict[str, Any] = {"role": "tool", "tool_name": tool_name, "content": visible_content}
            # OpenAI-compatible providers require this identifier, while Ollama
            # accepts the compact tool message used by the original runner.
            if call.get("id"):
                tool_message["tool_call_id"] = call["id"]
                tool_message.pop("tool_name")
            messages.append(tool_message)
            event_data = {
                "step": event.step,
                "tool_name": event.tool_name,
                "arguments": event.arguments,
                "result_text": event.result_text,
                "visible_result": visible_content,
                "output_variable": event.output_variable,
                "output_value": event.output_value,
                "visible_error": event.visible_error,
                "failure_code": event.failure_code,
                "node_type": event.node_type,
                "state_snapshot_bytes": len(suffix.encode("utf-8")),
            }
            state_diagnostics = strategy.diagnostics(event)
            if state_diagnostics:
                event_data["state_diagnostics"] = state_diagnostics
            events.append(event_data)
        if calls >= fixture.call_budget:
            failures.append("call_budget_exhausted")
            break

    predicted = _predicted_value(final_content)
    target = str(fixture.all_values[fixture.target_variable])
    success = predicted == target and calls >= fixture.minimum_calls
    if not success and not failures:
        failures.append("wrong_final_answer" if predicted is not None else "premature_stop")
    return TrialResult(
        task_id=fixture.task_id,
        strategy=strategy_name,
        success=success,
        predicted_value=predicted,
        target_value=target,
        calls=calls,
        minimum_calls=fixture.minimum_calls,
        prompt_tokens=sum(item["prompt_tokens"] for item in requests),
        completion_tokens=sum(item["completion_tokens"] for item in requests),
        state_bytes=state_bytes,
        failure_codes=failures,
        events=events,
        requests=requests,
    )


def result_path(root: str | Path, task_id: str, strategy: str, results_dir: str = "results/raw") -> Path:
    """Return an isolated per-trial result path.

    `results_dir` lets diagnostic model comparisons coexist with the primary
    experiment rather than overwriting its paired trial records.
    """
    return Path(root) / results_dir / strategy / f"{task_id}.json"


def save_result(root: str | Path, result: TrialResult, results_dir: str = "results/raw") -> Path:
    path = result_path(root, result.task_id, result.strategy, results_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
