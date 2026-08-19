from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from .models import FunctionSpec, TaskFixture, ToolEvent


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_SRC = ROOT / "vendor" / "funcbenchgen" / "src"
if str(UPSTREAM_SRC) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_SRC))

from evaluator import ToolCallingEvaluator  # noqa: E402
from function_tree import Function, FunctionDependencyTree  # noqa: E402
from utils import set_global_rng  # noqa: E402


TYPE_PATTERN = re.compile(r"\(type: ([^)]+)\)")
OUTPUT_PATTERN = re.compile(r"^Variable ([A-Za-z0-9_]+) = (-?\d+)\.")


def stable_trial_seed(config_id: str, graph_seed: int) -> int:
    value = sum((index + 1) * ord(char) for index, char in enumerate(config_id))
    return (value * 1009 + graph_seed * 9176 + 20260818) % (2**31 - 1)


def _tool_schemas(tree: FunctionDependencyTree, shuffle_seed: int) -> list[dict]:
    evaluator = ToolCallingEvaluator(tree, function_shuffle_seed=shuffle_seed)
    return evaluator._get_function_schemas()


def _functions(tree: FunctionDependencyTree) -> list[FunctionSpec]:
    return [
        FunctionSpec(
            name=node,
            description=data["function"].description,
            inputs=list(data["function"].inputs),
            output=data["function"].output,
            node_type=data.get("node_type", "core"),
        )
        for node, data in tree.graph.nodes(data=True)
    ]


def _typed_values(tree: FunctionDependencyTree, trial_seed: int) -> tuple[list[str], dict[str, int], dict[str, int], dict[str, str]]:
    set_global_rng(trial_seed, trial_seed + 11)
    evaluator = ToolCallingEvaluator(tree)
    var_to_type = dict(getattr(tree, "variable_to_type_map", {}))
    if not var_to_type:
        var_to_type, type_to_vars = evaluator._parse_types_from_descriptions()
    else:
        type_to_vars: dict[str, list[str]] = {}
        for variable, type_name in var_to_type.items():
            type_to_vars.setdefault(type_name, []).append(variable)
    input_variables, _ = evaluator._get_input_variables_with_types(var_to_type, type_to_vars)
    all_values: dict[str, int] = {}
    for variables in type_to_vars.values():
        value = random.choice(range(1000))
        for variable in variables:
            all_values[variable] = value
    return sorted(input_variables), {item: all_values[item] for item in input_variables}, all_values, var_to_type


def build_fixture(config: dict, graph_seed: int, settings: dict) -> TaskFixture:
    core = int(config["core_nodes"])
    connected = int(config["connected_nodes"])
    disconnected = int(config["disconnected_nodes"])
    depth = int(config["max_critical_path_length"])
    set_global_rng(graph_seed, graph_seed + 7)
    tree = FunctionDependencyTree()
    tree.build_graph_with_constraints(
        num_total_nodes=core + connected + disconnected,
        min_calls=core,
        max_critical_path_length=depth,
        num_disconnected_nodes=disconnected,
    )
    target_function = next(
        node for node, data in tree.graph.nodes(data=True) if data["function"].output == tree.desired_output_variable
    )
    trial_seed = stable_trial_seed(str(config["id"]), graph_seed)
    set_global_rng(trial_seed, trial_seed + 11)
    tree.reformat_tree_with_shared_types(
        show_variable_names_in_description=bool(config.get("show_variable_names_in_description", False)),
        rename_variables=bool(config.get("rename_variables", False)),
        use_subtypes=bool(config.get("use_subtypes", True)),
        num_supertypes=int(config.get("num_supertypes", 5)),
    )
    target = tree.graph.nodes[target_function]["function"].output
    tree.desired_output_variable = target
    input_variables, initial_values, all_values, variable_types = _typed_values(tree, trial_seed)
    minimum_calls = core
    return TaskFixture(
        task_id=f"{config['id']}-seed{graph_seed}",
        config_id=str(config["id"]),
        graph_seed=graph_seed,
        trial_seed=trial_seed,
        functions=_functions(tree),
        edges=[(str(left), str(right)) for left, right in tree.graph.edges()],
        target_function=target_function,
        target_variable=target,
        initial_values=initial_values,
        all_values=all_values,
        variable_types=variable_types,
        input_variables=input_variables,
        minimum_calls=minimum_calls,
        call_budget=minimum_calls * int(settings["call_budget_multiplier"]),
        tool_schemas=_tool_schemas(tree, int(settings["function_shuffle_seed"])),
    )


def save_fixture(fixture: TaskFixture, directory: str | Path) -> Path:
    path = Path(directory) / f"{fixture.task_id}.json"
    path.write_text(json.dumps(fixture.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_fixture(path: str | Path) -> TaskFixture:
    return TaskFixture.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def tree_from_fixture(fixture: TaskFixture) -> FunctionDependencyTree:
    tree = FunctionDependencyTree()
    tree.graph = nx.DiGraph()
    tree.used_names = set()
    tree.has_been_reformatted = True
    tree.desired_output_variable = fixture.target_variable
    tree.variable_to_type_map = dict(fixture.variable_types)
    for spec in fixture.functions:
        func = Function(name=spec.name, inputs=list(spec.inputs), output=spec.output, description=spec.description)
        tree.graph.add_node(spec.name, function=func, node_type=spec.node_type)
        tree.used_names.update([spec.name, spec.output, *spec.inputs])
    tree.graph.add_edges_from(fixture.edges)
    return tree


def input_prompt(fixture: TaskFixture) -> str:
    lines = [
        "Using the tools at your disposal, use function(s) to compute and give me the correct "
        f"value of variable {fixture.target_variable}."
    ]
    lines.extend(f"Variable {name} = {fixture.initial_values[name]}" for name in fixture.input_variables)
    lines.append("You have all the information you need to get the correct result.")
    return "\n".join(lines)


def parse_output(result: str) -> tuple[str | None, int | None]:
    match = OUTPUT_PATTERN.match(result)
    return (match.group(1), int(match.group(2))) if match else (None, None)


def canonical_failure(raw: str | None) -> str | None:
    mapping = {
        "Func not in graph": "function_not_found",
        "Gave too many inputs for function": "wrong_number_of_inputs",
        "Did not give all inputs for function": "wrong_number_of_inputs",
        "Variable value not yet known": "value_not_yet_known",
        "Value of variable was incorrect": "incorrect_value",
    }
    return mapping.get(raw)


class EvaluatorAdapter:
    """Executes the upstream evaluator while retaining StateBench annotations."""

    def __init__(self, fixture: TaskFixture, on_wrong_inputs: str = "Execute") -> None:
        self.fixture = fixture
        self.tree = tree_from_fixture(fixture)
        self.evaluator = ToolCallingEvaluator(
            self.tree,
            error_detail_level="None",
            on_wrong_inputs=on_wrong_inputs,
            function_shuffle_seed=42,
            repeat_known_variable_values=False,
        )
        self.evaluator.variable_values = dict(fixture.all_values)
        self.known_types = {fixture.variable_types[name] for name in fixture.input_variables}

    def execute(self, step: int, tool_name: str, arguments: dict) -> ToolEvent:
        call = SimpleNamespace(function=SimpleNamespace(name=tool_name, arguments=json.dumps(arguments)))
        result, raw_failure = self.evaluator._execute_tool(
            call, self.known_types, use_types=True, var_to_type=self.fixture.variable_types
        )
        output_variable, output_value = parse_output(result)
        node_type = "unknown"
        if tool_name in self.tree.graph.nodes:
            node_type = self.tree.graph.nodes[tool_name].get("node_type", "unknown")
        return ToolEvent(
            step=step,
            tool_name=tool_name,
            arguments=dict(arguments),
            result_text=result,
            output_variable=output_variable,
            output_value=output_value,
            visible_error=output_variable is None,
            failure_code=canonical_failure(raw_failure),
            node_type=node_type,
        )
