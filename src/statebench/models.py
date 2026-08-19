from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    description: str
    inputs: list[str]
    output: str
    node_type: str


@dataclass
class TaskFixture:
    task_id: str
    config_id: str
    graph_seed: int
    trial_seed: int
    functions: list[FunctionSpec]
    edges: list[tuple[str, str]]
    target_function: str
    target_variable: str
    initial_values: dict[str, int]
    all_values: dict[str, int]
    variable_types: dict[str, str]
    input_variables: list[str]
    minimum_calls: int
    call_budget: int
    tool_schemas: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["functions"] = [asdict(item) for item in self.functions]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskFixture":
        data = dict(data)
        data["functions"] = [FunctionSpec(**item) for item in data["functions"]]
        data["edges"] = [tuple(item) for item in data["edges"]]
        return cls(**data)


@dataclass
class ToolEvent:
    step: int
    tool_name: str
    arguments: dict[str, Any]
    result_text: str
    output_variable: str | None
    output_value: int | None
    visible_error: bool
    failure_code: str | None = None
    node_type: str = "unknown"


@dataclass
class ModelTurn:
    content: str
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    latency_seconds: float
    raw: dict[str, Any]


@dataclass
class TrialResult:
    task_id: str
    strategy: str
    success: bool
    predicted_value: str | None
    target_value: str
    calls: int
    minimum_calls: int
    prompt_tokens: int
    completion_tokens: int
    state_bytes: int
    failure_codes: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

