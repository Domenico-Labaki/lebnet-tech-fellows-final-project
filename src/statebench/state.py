from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .models import TaskFixture, ToolEvent


@dataclass
class KnownValue:
    name: str
    value: int
    type_name: str
    source_tool: str
    first_seen_step: int
    last_seen_step: int


class StateStrategy(Protocol):
    name: str

    def reset(self, fixture: TaskFixture) -> None: ...

    def observe(self, event: ToolEvent) -> None: ...

    def render_suffix(self, current: ToolEvent) -> str: ...

    def diagnostics(self, current: ToolEvent) -> dict[str, object]: ...


class BaseState:
    name = "base"

    def reset(self, fixture: TaskFixture) -> None:
        self.fixture = fixture
        self.values: dict[str, KnownValue] = {
            name: KnownValue(name, value, fixture.variable_types[name], "initial_prompt", 0, 0)
            for name, value in fixture.initial_values.items()
        }
        self.invoked: set[str] = set()
        self.successful_invoked: set[str] = set()

    def observe(self, event: ToolEvent) -> None:
        self.invoked.add(event.tool_name)
        if event.output_variable is not None and event.output_value is not None:
            self.successful_invoked.add(event.tool_name)
            previous = self.values.get(event.output_variable)
            self.values[event.output_variable] = KnownValue(
                name=event.output_variable,
                value=event.output_value,
                type_name=self.fixture.variable_types[event.output_variable],
                source_tool=event.tool_name,
                first_seen_step=previous.first_seen_step if previous else event.step,
                last_seen_step=event.step,
            )

    def prior_values(self, current: ToolEvent) -> list[KnownValue]:
        return [value for name, value in self.values.items() if name != current.output_variable]

    def render_suffix(self, current: ToolEvent) -> str:
        return ""

    def diagnostics(self, current: ToolEvent) -> dict[str, object]:
        return {}


class FullHistory(BaseState):
    name = "full_history"


class PaperRestatement(BaseState):
    name = "paper_restatement"

    def render_suffix(self, current: ToolEvent) -> str:
        if current.visible_error:
            return ""
        lines = ["", "As a reminder, here are the variable values that are currently known."]
        lines.extend(f"Variable {item.name} ({item.type_name}) = {item.value}" for item in self.prior_values(current))
        return "\n".join(lines)


class JsonState(BaseState):
    name = "json_state"

    def render_suffix(self, current: ToolEvent) -> str:
        if current.visible_error:
            return ""
        payload = {item.name: item.value for item in self.prior_values(current)}
        return "\nKnown state: " + json.dumps({"known_variables": payload}, separators=(",", ":"))


class StructuredLedger(BaseState):
    name = "structured_ledger"

    def render_suffix(self, current: ToolEvent) -> str:
        if current.visible_error:
            return ""
        payload = [
            {
                "name": item.name,
                "value": item.value,
                "type": item.type_name,
                "source_tool": item.source_tool,
                "first_seen_step": item.first_seen_step,
                "last_seen_step": item.last_seen_step,
            }
            for item in self.prior_values(current)
        ]
        return "\nKnown state: " + json.dumps({"known_variables": payload}, separators=(",", ":"))


class RelevancePruning(JsonState):
    name = "relevance_pruning"

    def _keep(self, current: ToolEvent) -> list[KnownValue]:
        present_types = {value.type_name for value in self.values.values()}
        active_inputs: set[str] = set()
        for function in self.fixture.functions:
            if function.name in self.invoked:
                continue
            inputs = {self.fixture.variable_types[name] for name in function.inputs}
            if inputs and inputs.issubset(present_types):
                active_inputs |= inputs
        return [item for item in self.prior_values(current) if item.type_name in active_inputs]

    def render_suffix(self, current: ToolEvent) -> str:
        if current.visible_error:
            return ""
        return "\nKnown state: " + json.dumps(
            {"known_variables": {item.name: item.value for item in self._keep(current)}}, separators=(",", ":")
        )


class DependencyPruning(JsonState):
    name = "dependency_pruning"

    def _keep(self, current: ToolEvent) -> list[KnownValue]:
        consumed_types = {
            self.fixture.variable_types[input_name]
            for function in self.fixture.functions
            for input_name in function.inputs
        }
        return [item for item in self.prior_values(current) if item.type_name in consumed_types]

    def render_suffix(self, current: ToolEvent) -> str:
        if current.visible_error:
            return ""
        return "\nKnown state: " + json.dumps(
            {"known_variables": {item.name: item.value for item in self._keep(current)}}, separators=(",", ":")
        )


class LiveDependencyPruning(JsonState):
    """Retain one value per publicly relevant, still-live exact type.

    The compatible protocol exposes the target variable plus every function's
    input/output variable names and exact types. This strategy reconstructs a
    conservative *visible type frontier* from those schemas. It deliberately
    does not inspect fixture edges, node types, ground-truth values, or the
    hidden target path.

    A type remains live when it is already known and is required by an
    unfinished public route to the target. If a required type is not known,
    all still-uncompleted visible producers of that type are considered and
    their input types are explored recursively. Ambiguous public routes are
    therefore retained rather than guessed away.
    """

    name = "live_dependency_pruning"

    def _fallback_live_types(self) -> set[str]:
        """Conservative fallback when the visible target producer is unclear."""
        return {
            self.fixture.variable_types[input_name]
            for function in self.fixture.functions
            if function.name not in self.successful_invoked
            for input_name in function.inputs
        }

    def _live_types(self) -> set[str]:
        if self.fixture.target_variable in self.values:
            return set()

        # The compatible protocol prints output variable names in public tool
        # descriptions. If that mapping is absent (for example, an opaque
        # paper-style fixture), do not infer it from private fixture metadata;
        # fall back to remaining-consumer liveness instead.
        target_functions = [
            function
            for function in self.fixture.functions
            if function.output == self.fixture.target_variable and function.output in function.description
        ]
        if len(target_functions) != 1:
            return self._fallback_live_types()

        variable_types = self.fixture.variable_types
        known_types = {value.type_name for value in self.values.values()}
        producers: dict[str, list[object]] = {}
        for function in self.fixture.functions:
            output_type = variable_types[function.output]
            producers.setdefault(output_type, []).append(function)

        live: set[str] = set()
        visiting: set[str] = set()

        def require(type_name: str) -> None:
            if type_name in known_types:
                live.add(type_name)
                return
            if type_name in visiting:
                return
            visiting.add(type_name)
            for function in producers.get(type_name, []):
                if function.name in self.successful_invoked:
                    continue
                for input_name in function.inputs:
                    require(variable_types[input_name])
            visiting.remove(type_name)

        for input_name in target_functions[0].inputs:
            require(variable_types[input_name])
        return live

    @staticmethod
    def _one_representative_per_type(items: list[KnownValue]) -> list[KnownValue]:
        grouped: dict[str, list[KnownValue]] = {}
        for item in items:
            grouped.setdefault(item.type_name, []).append(item)

        retained: list[KnownValue] = []
        for group in grouped.values():
            # FuncBenchGen assigns one value per exact type. Preserve all
            # entries if a future fixture violates that invariant rather than
            # silently discarding a conflicting value.
            if len({item.value for item in group}) == 1:
                retained.append(max(group, key=lambda item: (item.last_seen_step, item.first_seen_step, item.name)))
            else:
                retained.extend(group)
        return sorted(retained, key=lambda item: (item.first_seen_step, item.last_seen_step, item.name))

    def _selection(self, current: ToolEvent) -> tuple[list[KnownValue], list[KnownValue], set[str]]:
        prior = self.prior_values(current)
        live_types = self._live_types()
        candidates = [item for item in prior if item.type_name in live_types]
        retained = self._one_representative_per_type(candidates)
        retained_names = {item.name for item in retained}
        pruned = [item for item in prior if item.name not in retained_names]
        return retained, pruned, live_types

    def render_suffix(self, current: ToolEvent) -> str:
        if current.visible_error:
            return ""
        retained, _, _ = self._selection(current)
        if not retained:
            return ""
        return "\nKnown state: " + json.dumps(
            {"known_variables": {item.name: item.value for item in retained}}, separators=(",", ":")
        )

    def diagnostics(self, current: ToolEvent) -> dict[str, object]:
        retained, pruned, live_types = self._selection(current)
        return {
            "live_types": sorted(live_types),
            "retained_variables": [item.name for item in retained],
            "pruned_variables": [item.name for item in pruned],
            "target_complete": self.fixture.target_variable in self.values,
        }


STRATEGIES = {
    item.name: item
    for item in [
        FullHistory,
        PaperRestatement,
        JsonState,
        StructuredLedger,
        RelevancePruning,
        DependencyPruning,
        LiveDependencyPruning,
    ]
}


def create_strategy(name: str) -> StateStrategy:
    try:
        return STRATEGIES[name]()
    except KeyError as error:
        raise ValueError(f"Unknown strategy: {name}") from error
