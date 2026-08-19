from __future__ import annotations

import json
from pathlib import Path

from statebench.models import ToolEvent
from statebench.state import create_strategy
from statebench.upstream import load_fixture


ROOT = Path(__file__).resolve().parents[1]


def test_live_pruning_activates_on_all_completed_fixtures() -> None:
    result_dir = ROOT / "results" / "final" / "raw" / "dependency_pruning"
    fixture_dir = ROOT / "data" / "frozen_tasks_compatible"
    comparisons: list[tuple[int, int]] = []

    for result_path in sorted(result_dir.glob("*.json")):
        record = json.loads(result_path.read_text(encoding="utf-8"))
        fixture = load_fixture(fixture_dir / result_path.name)
        strategy = create_strategy("live_dependency_pruning")
        strategy.reset(fixture)
        live_bytes = 0
        for raw in record["events"]:
            event = ToolEvent(
                step=raw["step"],
                tool_name=raw["tool_name"],
                arguments=raw["arguments"],
                result_text=raw["result_text"],
                output_variable=raw["output_variable"],
                output_value=raw["output_value"],
                visible_error=raw["visible_error"],
                failure_code=raw["failure_code"],
                node_type=raw["node_type"],
            )
            strategy.observe(event)
            live_bytes += len(strategy.render_suffix(event).encode("utf-8"))
        comparisons.append((int(record["state_bytes"]), live_bytes))

    assert len(comparisons) == 15
    assert all(live < legacy for legacy, live in comparisons)
    # The deterministic replay is a pre-run activation check, not a model
    # result. It should remove at least half of the legacy added-state bytes.
    assert sum(live for _, live in comparisons) < 0.5 * sum(legacy for legacy, _ in comparisons)
