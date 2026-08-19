from statebench.models import FunctionSpec, TaskFixture, ToolEvent
from statebench.state import create_strategy


def fixture() -> TaskFixture:
    return TaskFixture(
        task_id="test", config_id="test", graph_seed=0, trial_seed=0,
        functions=[
            FunctionSpec("make_b", "Processes (type: b)", ["a"], "b", "core"),
            FunctionSpec("make_c", "Processes (type: c)", ["b"], "c", "core"),
        ],
        edges=[("make_b", "make_c")], target_function="make_c", target_variable="c",
        initial_values={"a": 10}, all_values={"a": 10, "b": 20, "c": 30},
        variable_types={"a": "type_a", "b": "type_b", "c": "type_c"},
        input_variables=["a"], minimum_calls=2, call_budget=4, tool_schemas=[],
    )


def event() -> ToolEvent:
    return ToolEvent(1, "make_b", {"a": 10}, "Variable b = 20.", "b", 20, False)


def test_json_hides_current_value_from_suffix() -> None:
    strategy = create_strategy("json_state")
    strategy.reset(fixture())
    strategy.observe(event())
    suffix = strategy.render_suffix(event())
    assert '"a":10' in suffix
    assert '"b":20' not in suffix


def test_dependency_pruning_uses_consumers() -> None:
    strategy = create_strategy("dependency_pruning")
    strategy.reset(fixture())
    strategy.observe(event())
    suffix = strategy.render_suffix(event())
    assert '"a":10' in suffix


def live_fixture() -> TaskFixture:
    return TaskFixture(
        task_id="live", config_id="live", graph_seed=0, trial_seed=0,
        functions=[
            FunctionSpec("make_b", "a type_a to b type_b", ["a"], "b", "core"),
            FunctionSpec("make_c", "b_input type_b and a_again type_a to c", ["b_input", "a_again"], "c", "core"),
            FunctionSpec("distract", "a_extra type_a to x type_x", ["a_extra"], "x", "extra"),
        ],
        edges=[("make_b", "make_c")], target_function="make_c", target_variable="c",
        initial_values={"a": 10}, all_values={"a": 10, "b": 20, "b_input": 20, "a_again": 10, "a_extra": 10, "c": 30, "x": 40},
        variable_types={
            "a": "type_a", "a_again": "type_a", "a_extra": "type_a",
            "b": "type_b", "b_input": "type_b", "c": "type_c", "x": "type_x",
        },
        input_variables=["a"], minimum_calls=2, call_budget=4, tool_schemas=[],
    )


def test_live_pruning_retains_only_target_frontier_values() -> None:
    strategy = create_strategy("live_dependency_pruning")
    strategy.reset(live_fixture())
    make_b = ToolEvent(1, "make_b", {"a": 10}, "Variable b = 20.", "b", 20, False)
    strategy.observe(make_b)
    suffix = strategy.render_suffix(make_b)
    # b is already visible in the current tool result; a must remain because
    # the target itself also consumes type_a. The distractor does not widen the
    # target-directed frontier.
    assert suffix == '\nKnown state: {"known_variables":{"a":10}}'
    diagnostics = strategy.diagnostics(make_b)
    assert diagnostics["retained_variables"] == ["a"]
    assert "type_x" not in diagnostics["live_types"]


def test_live_pruning_does_not_treat_failed_call_as_completed() -> None:
    strategy = create_strategy("live_dependency_pruning")
    strategy.reset(live_fixture())
    failed = ToolEvent(
        1, "make_b", {"a": 999}, "Error: value not yet known.", None, None, True, "value_not_yet_known"
    )
    strategy.observe(failed)
    diagnostics = strategy.diagnostics(failed)
    assert "make_b" not in strategy.successful_invoked
    assert diagnostics["retained_variables"] == ["a"]


def test_live_pruning_emits_no_suffix_after_target() -> None:
    strategy = create_strategy("live_dependency_pruning")
    strategy.reset(live_fixture())
    make_b = ToolEvent(1, "make_b", {"a": 10}, "Variable b = 20.", "b", 20, False)
    strategy.observe(make_b)
    target = ToolEvent(
        2, "make_c", {"b_input": 20, "a_again": 10}, "Variable c = 30.", "c", 30, False
    )
    strategy.observe(target)
    assert strategy.render_suffix(target) == ""
    assert strategy.diagnostics(target)["target_complete"] is True


def test_live_pruning_deduplicates_equal_values_but_preserves_conflicts() -> None:
    fixture = live_fixture()
    fixture.initial_values = {"a": 10, "a_again": 10}
    fixture.input_variables = ["a", "a_again"]
    strategy = create_strategy("live_dependency_pruning")
    strategy.reset(fixture)
    make_b = ToolEvent(1, "make_b", {"a": 10}, "Variable b = 20.", "b", 20, False)
    strategy.observe(make_b)
    suffix = strategy.render_suffix(make_b)
    assert suffix.count(":10") == 1

    strategy.values["a_again"].value = 11
    suffix = strategy.render_suffix(make_b)
    assert '"a":10' in suffix
    assert '"a_again":11' in suffix
