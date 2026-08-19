# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import json
import copy
import random
from dataclasses import dataclass
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

# Local imports
from function_tree import Function, FunctionDependencyTree
from evaluator import ToolCallingEvaluator
from utils import (
    ensure_dir,
    set_global_rng,
    any_exact_number_match,
    is_reasoning_model,
    model_basename,
    stable_int_from,
    sanitize_node_link_data,
    graph_from_node_link_data,
    print_args_banner,
    format_noise_var_tag
)

# Reasoning / chat-completion backend
from litellm import completion

# ========= Experiment configuration =========

@dataclass
class ExperimentConfig:
    """Holds a single graph-generation configuration."""
    num_total_nodes: int
    max_critical_path_length: int
    num_disconnected_nodes: int
    min_calls: int  # Effective minimum calls to satisfy constraints
    show_variable_names_in_description: bool
    rename_variables: bool
    use_subtypes: bool
    num_supertypes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "num_total_nodes": self.num_total_nodes,
            "max_critical_path_length": self.max_critical_path_length,
            "num_disconnected_nodes": self.num_disconnected_nodes,
            "min_calls": self.min_calls,
            "show_variable_names_in_description": self.show_variable_names_in_description,
            "rename_variables": self.rename_variables,
            "use_subtypes": self.use_subtypes,
            "num_supertypes": self.num_supertypes,
        }


def _config_tag(cfg: ExperimentConfig) -> str:
    """Deterministic, readable per-config tag (used both for graph dir and results)."""
    return (
        f"ntn{cfg.num_total_nodes}"
        f"_mc{cfg.min_calls}"
        f"_mcp{cfg.max_critical_path_length}"
        f"_ndn{cfg.num_disconnected_nodes}"
        f"_svnid{cfg.show_variable_names_in_description}"
        f"_rv{cfg.rename_variables}"
        f"_ust{cfg.use_subtypes}"
        f"_nst{cfg.num_supertypes}"
    )


def _filename_tag(cfg: ExperimentConfig, thinking_budget: str, verbosity: str, num_noise_inputs: Optional[int]) -> str:
    """File tag for result directory: add runtime knobs to the config tag."""
    return f"{_config_tag(cfg)}_tb-{thinking_budget}_vb-{verbosity}_{format_noise_var_tag(num_noise_inputs)}"


def derive_trial_seed(cfg_tag: str, graph_seed: int, trial_idx: int, rng_salt: int = 0) -> int:
    return stable_int_from(
        str(cfg_tag), str(graph_seed), str(trial_idx), str(rng_salt),
        modulo=2**31 - 1
    )

# ========= Chat-completion wrapper (ported from original) =========

def chat_completion(model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], api_extras: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call litellm.completion and parse token usage and thought text.

    Returns:
        {
          "prediction": <OpenAI-like message object>,
          "input_tokens": int,
          "output_tokens": int,
          "reasoning_tokens": int,
          "thought_process": str
        }
    """

    # Execute
    kwargs = dict(model=model, messages=messages, tools=tools, num_retries=10, drop_params=True, **api_extras)

    response = completion(**kwargs)

    # Token usage (defensive)
    if getattr(response.usage, "completion_tokens_details", None) is not None:
        reasoning_tokens = response.usage.completion_tokens_details.reasoning_tokens
    else:
        reasoning_tokens = 0
    if len(response.choices) == 0:
        print(response)
        msg = {}
    else:
        msg = response.choices[0].message
    parsed = {
        "prediction": msg,
        "input_tokens": getattr(response.usage, "prompt_tokens", 0),
        "output_tokens": getattr(response.usage, "completion_tokens", 0),
        "reasoning_tokens": reasoning_tokens,
    }

    # Extract thought process inside <think>...</think> if present
    content = getattr(msg, "content", None)
    thought_process = ""
    if isinstance(content, str) and content:
        m = re.search(r"(.*?)</think>", content, re.DOTALL)
        if m:
            thought_process = (m.group(1) or "").strip().replace("<think>", "")
    parsed["thought_process"] = thought_process
    return parsed


# ========= Graph building =========

def _build_graph_for_seed(cfg: ExperimentConfig, graph_seed: int, verbose: bool = False) -> Dict[str, Any]:
    """Build one graph for one seed and return its serializable payload."""
    set_global_rng(graph_seed, graph_seed + 7)

    ft = FunctionDependencyTree()
    # English comment: Build with constraints; min_calls must be >= mcp + 1
    ft.build_graph_with_constraints(
        num_total_nodes=cfg.num_total_nodes,
        min_calls=max(cfg.min_calls, cfg.max_critical_path_length + 1),
        max_critical_path_length=cfg.max_critical_path_length,
        num_disconnected_nodes=cfg.num_disconnected_nodes,
    )

    # English comment: Choose the target node by matching the desired_output_variable (if exposed by the builder)
    target_node = None
    target_var = getattr(ft, "desired_output_variable", None)
    if target_var:
        for n, data in ft.graph.nodes(data=True):
            f = data.get("function")
            if isinstance(f, Function) and f.output == target_var:
                target_node = n
                break

    if verbose:
        print(f"[Graph] seed={graph_seed}, target_node={target_node}, target_var={target_var}")

    graph_data = sanitize_node_link_data(ft.graph)
    return {
        "graph_data": graph_data,
        "target_node": target_node,
        "target_variable": target_var,
        "config": cfg.to_dict(),
        "graph_seed": graph_seed,
    }


# ========= Aggregate CSV I/O =========

def write_aggregate_outputs(root_save_dir: str, model: str, experiment_name: str, rows: List[Dict[str, Any]]) -> None:
    """Append/merge aggregate rows into a CSV."""
    try:
        import pandas as pd  # Optional, but preferred for merging
    except Exception:
        pd = None

    model_base = model_basename(model)
    out_dir = os.path.join(root_save_dir, "results", model_base, experiment_name)
    ensure_dir(out_dir)
    csv_path = os.path.join(out_dir, "aggregate.csv")

    columns = [
        "model", "experiment_name", "config_tag", "tb", "vb", "noise_var",
        "success_rate", "avg_calls", "avg_efficiency", "num_trials",
        "ntn", "mc", "mcp", "ndn", "svnid", "rv", "ust", "nst", "timestamp",
    ]

    def _normalize(r: Dict[str, Any]) -> Dict[str, Any]:
        return {col: r.get(col) for col in columns}

    rows = [_normalize(r) for r in rows]

    if pd is not None:
        key_cols = ["model", "experiment_name", "config_tag", "tb", "vb", "noise_var"]
        new_df = pd.DataFrame(rows, columns=columns)

        if os.path.exists(csv_path):
            try:
                old_df = pd.read_csv(csv_path)
                merged = (
                    pd.concat([old_df, new_df], ignore_index=True)
                    .sort_values("timestamp")
                    .drop_duplicates(subset=key_cols, keep="last")
                )
            except Exception:
                merged = new_df
        else:
            merged = new_df

        merged.to_csv(csv_path, index=False)
        print(f"[aggregate] wrote/merged: {csv_path} (rows={len(merged)})")
    else:
        # Fallback append-only
        import csv
        write_header = not os.path.exists(csv_path)
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            if write_header:
                writer.writeheader()
            for r in rows:
                writer.writerow(r)
        print(f"[aggregate] appended: {csv_path} (+{len(rows)} rows)")


# ========= Run helpers =========

def _adjust_model_settings(
    model: str,
    temperature: float,
    thinking_budget: str,
    max_completion_tokens: int
) -> tuple:
    """Adjust generation parameters based on the model type."""
    if "gpt-5" in model:
        return temperature, thinking_budget, 128000
    
    # Non-GPT-5 models: use temperature=0 and model-specific token limits
    temperature = 0.0
    thinking_budget = "medium"
    
    if "gpt-4.1" in model:
        max_completion_tokens = 32768
    elif "gemini" in model:
        max_completion_tokens = 65535
    elif "qwen" in model:
        max_completion_tokens = 32768
    else:
        raise ValueError(f"Unknown model: {model}")
    
    return temperature, thinking_budget, max_completion_tokens


def _build_config_grid(
    mcp_start: int,
    mcp_stop: int,
    mcp_step: int,
    num_total_nodes: int,
    core_nodes: int,
    num_disconnected_nodes: int,
    show_variable_names_in_description: bool,
    rename_variables: bool,
    use_subtypes: bool,
    num_supertypes: int,
) -> List[ExperimentConfig]:
    """Build a list of ExperimentConfig for the MCP sweep range."""
    configs: List[ExperimentConfig] = []
    rng = range(mcp_start, mcp_stop + (1 if mcp_step > 0 else -1), mcp_step)
    
    for mcp in rng:
        min_calls_eff = max(core_nodes, mcp + 1)
        if core_nodes < mcp + 1:
            print(f"[WARN] core_nodes({core_nodes}) < mcp+1({mcp+1}); using min_calls_eff={min_calls_eff}.")
        
        configs.append(ExperimentConfig(
            num_total_nodes=num_total_nodes,
            max_critical_path_length=mcp,
            num_disconnected_nodes=num_disconnected_nodes,
            min_calls=min_calls_eff,
            show_variable_names_in_description=show_variable_names_in_description,
            rename_variables=rename_variables,
            use_subtypes=use_subtypes,
            num_supertypes=num_supertypes,
        ))
    
    return configs


def _build_or_load_graphs(
    cfg: ExperimentConfig,
    cfg_graph_dir: str,
    seeds_for_graphs: List[int],
    overwrite_graphs: bool,
    verbose: bool,
) -> List[Dict[str, Any]]:
    """Build new graphs or load existing ones for the given config."""
    graphs: List[Dict[str, Any]] = []
    
    for graph_seed in seeds_for_graphs:
        per_seed_graph_path = os.path.join(cfg_graph_dir, f"{graph_seed}.json")
        
        if (not overwrite_graphs) and os.path.exists(per_seed_graph_path):
            with open(per_seed_graph_path, "r") as f:
                graphs.append(json.load(f))
        else:
            payload = _build_graph_for_seed(cfg, graph_seed, verbose=verbose)
            tmp = per_seed_graph_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, per_seed_graph_path)
            graphs.append(payload)
            print(f"[graph] saved: {per_seed_graph_path}")
    
    return graphs


def _initialize_trial_variables(
    evaluator: ToolCallingEvaluator,
    function_tree: FunctionDependencyTree,
    use_types: bool,
) -> tuple:
    """Initialize variable values and determine input variables for a trial."""
    if use_types:
        vt_map = getattr(evaluator.function_tree, 'variable_to_type_map', None)
        if isinstance(vt_map, dict) and vt_map:
            var_to_type = dict(vt_map)
            type_to_vars: Dict[str, List[str]] = {}
            for v, t in var_to_type.items():
                type_to_vars.setdefault(t, []).append(v)
            input_vars, known = evaluator._get_input_variables_with_types(var_to_type, type_to_vars)
        else:
            var_to_type, type_to_vars = evaluator._parse_types_from_descriptions()
            input_vars, known = evaluator._get_input_variables_with_types(var_to_type, type_to_vars)
    else:
        var_to_type = {}
        type_to_vars = {}
        input_vars = function_tree.get_input_vars()
        known = set(input_vars)

    # Initialize variable values
    evaluator.variable_values = {}
    if use_types:
        for _t, _vars in type_to_vars.items():
            val = random.choice(range(1000))
            for _v in _vars:
                evaluator.variable_values[_v] = val
    else:
        for node_name in function_tree.graph.nodes:
            func = function_tree.graph.nodes[node_name]['function']
            for var in (func.inputs + [func.output]):
                if var not in evaluator.variable_values:
                    evaluator.variable_values[var] = random.choice(range(1000))

    return input_vars, known, var_to_type, type_to_vars


def _run_tool_calling_loop(
    model: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    api_extras: Dict[str, Any],
    evaluator: ToolCallingEvaluator,
    known: set,
    use_types: bool,
    var_to_type: Dict[str, str],
    max_num_calls: int,
) -> tuple:
    """Execute the main tool-calling loop until completion or timeout."""
    num_calls = 0
    call_failures: List[str] = []
    call_sequence: List[Dict[str, Any]] = []
    run_loop = True
    final_msg_content = None

    while run_loop and num_calls < max_num_calls:
        # Call the model
        try:
            rsp = chat_completion(model=model, messages=messages, tools=tools, api_extras=api_extras)
            msg = rsp["prediction"]
        except Exception as e:
            print(f"      ERROR calling model: {e}")
            call_failures.append(f"API_ERROR: {e}")
            call_sequence.append({
                "event": "api_error",
                "error_message": str(e),
                "timestamp": datetime.now().isoformat(),
            })
            break

        # Process tool calls if any
        if getattr(msg, "tool_calls", None):
            messages.append({"role": "assistant", "content": None, "tool_calls": msg.tool_calls})
            
            for call in msg.tool_calls:
                # Extract metadata
                _func_name = getattr(getattr(call, "function", None), "name", None)
                _node_type = "unknown"
                try:
                    if _func_name and _func_name in evaluator.function_tree.graph.nodes:
                        _node_type = evaluator.function_tree.graph.nodes[_func_name].get("node_type", "core")
                except Exception:
                    pass

                num_calls += 1

                # Execute the tool
                try:
                    result, failure_reason = evaluator._execute_tool(
                        call,
                        known,
                        use_types=use_types,
                        var_to_type=var_to_type,
                    )
                except Exception as e:
                    result = "Error occurred."
                    failure_reason = "Tool input was invalid"

                # Record this step
                _entry = {
                    "order": num_calls,
                    "msg": str(msg),
                    "function": _func_name,
                    "node_type": _node_type,
                    "tool_call": str(call),
                    "timestamp": datetime.now().isoformat(),
                    "content": str({"result": result}),
                    "input_tokens": rsp["input_tokens"],
                    "output_tokens": rsp["output_tokens"],
                    "reasoning_tokens": rsp["reasoning_tokens"],
                    "thought_process": rsp["thought_process"],
                }
                if failure_reason:
                    call_failures.append(failure_reason)
                    _entry["error_message"] = str(failure_reason)
                call_sequence.append(_entry)

                # Add tool result to chat context
                messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(call, "id", None),
                    "name": _func_name,
                    "content": json.dumps(result, ensure_ascii=False)
                })
        else:
            # No tool calls -> final message
            run_loop = False
            final_msg_content = getattr(msg, "content", None)

    return num_calls, call_failures, call_sequence, final_msg_content


def _evaluate_trial_result(
    final_msg_content: Optional[str],
    num_calls: int,
    max_num_calls: int,
    minimum_calls_eff: int,
    target_value_str: str,
) -> tuple:
    """Evaluate correctness and efficiency of a trial."""
    if num_calls >= max_num_calls and final_msg_content is None:
        was_correct = False
        failure_reason = "timeout"
    else:
        if final_msg_content is not None:
            was_correct = (
                any_exact_number_match(final_msg_content, target_value_str) 
                and num_calls >= minimum_calls_eff
            )
            failure_reason = None
        else:
            was_correct = False
            failure_reason = "incorrect"

    efficiency = (minimum_calls_eff / num_calls) if was_correct and num_calls > 0 else 0.0
    
    return was_correct, failure_reason, efficiency


def _build_trial_record(
    graph_seed: int,
    trial_idx: int,
    desired_output_func_name: str,
    was_correct: bool,
    input_prompt: str,
    final_msg_content: Optional[str],
    target_value_str: str,
    num_calls: int,
    efficiency: float,
    minimum_calls_eff: int,
    call_failures: List[str],
    call_sequence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a record for a single trial."""
    return {
        "graph_seed": graph_seed,
        "trial_index": trial_idx,
        "target_node": desired_output_func_name,
        "was_correct": was_correct,
        "input_prompt": input_prompt,
        "final_msg_content": final_msg_content,
        "target_value": target_value_str,
        "num_calls": num_calls,
        "efficiency": efficiency,
        "minimum_calls": minimum_calls_eff,
        "call_failures": call_failures,
        "call_sequence": call_sequence,
        "node_type_sequence": [e.get("node_type") for e in call_sequence if "node_type" in e],
        "function_sequence": [e.get("function") for e in call_sequence if "function" in e],
    }


def _compute_summary_stats(trials: List[Dict[str, Any]]) -> tuple:
    """Compute summary statistics from trial results."""
    if not trials:
        return 0.0, 0.0, 0.0
    
    success_rate = sum(1 for r in trials if r['was_correct']) / len(trials)
    avg_calls = float(np.mean([r['num_calls'] for r in trials]))
    correct_runs = [r for r in trials if r['was_correct']]
    avg_efficiency = float(np.mean([r['efficiency'] for r in correct_runs])) if correct_runs else 0.0
    
    return success_rate, avg_calls, avg_efficiency


def _build_per_seed_payload(
    model_base: str,
    experiment_name: str,
    cfg: ExperimentConfig,
    cfg_tag: str,
    graph_seed: int,
    thinking_budget: str,
    verbosity: str,
    num_noise_inputs: int,
    trials: List[Dict[str, Any]],
    failure_counter: Counter,
) -> Dict[str, Any]:
    """Build the JSON payload for per-seed results."""
    success_rate, avg_calls, avg_efficiency = _compute_summary_stats(trials)
    
    return {
        "model": model_base,
        "experiment_name": experiment_name,
        "config": cfg.to_dict(),
        "config_tag": cfg_tag,
        "seed": graph_seed,
        "tb": thinking_budget,
        "vb": verbosity,
        "num_noise_inputs": int(num_noise_inputs) if num_noise_inputs is not None else 0,
        "noise_var": format_noise_var_tag(num_noise_inputs),
        "summary": {
            "success_rate": success_rate,
            "avg_calls": avg_calls,
            "avg_efficiency": avg_efficiency,
            "num_trials": len(trials),
        },
        "failure_counter": dict(failure_counter),
        "trials": trials,
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
    }


def _build_aggregate_row(
    model: str,
    experiment_name: str,
    cfg: ExperimentConfig,
    cfg_tag: str,
    thinking_budget: str,
    verbosity: str,
    num_noise_inputs: int,
    trials: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a single row for the aggregate CSV."""
    was_all = [1 if r.get("was_correct") else 0 for r in trials]
    success_rate_all = float(sum(was_all) / max(len(was_all), 1))
    calls_all = [r.get("num_calls", 0) for r in trials]
    avg_calls_all = float(np.mean(calls_all)) if calls_all else 0.0
    eff_all = [r.get("efficiency", 0.0) for r in trials if r.get("was_correct")]
    avg_eff_all = float(np.mean(eff_all)) if eff_all else 0.0

    return {
        "model": model_basename(model),
        "experiment_name": experiment_name,
        "config_tag": cfg_tag,
        "tb": str(thinking_budget or "medium"),
        "vb": str(verbosity or "medium"),
        "noise_var": format_noise_var_tag(num_noise_inputs),
        "success_rate": success_rate_all,
        "avg_calls": avg_calls_all,
        "avg_efficiency": avg_eff_all,
        "num_trials": len(trials),
        "ntn": cfg.num_total_nodes,
        "mc": cfg.min_calls,
        "mcp": cfg.max_critical_path_length,
        "ndn": cfg.num_disconnected_nodes,
        "svnid": cfg.show_variable_names_in_description,
        "rv": cfg.rename_variables,
        "ust": cfg.use_subtypes,
        "nst": cfg.num_supertypes,
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
    }


# ========= Main entrypoint =========

def run(
    # Model
    model: str,

    # Core knobs
    root_save_dir: str,
    experiment_name: str,

    # Graph count & overwrite
    num_graphs_per_config: int = 1,
    overwrite_graphs: bool = False,
    overwrite_results: bool = False,

    # Output controls
    save_aggregate_outputs: bool = True,

    # Reproducibility
    num_trials_per_graph: int = 1,

    # Execution behavior
    use_types: bool = True,
    verbose: bool = False,

    # Reasoning controls
    thinking_budget: str = "medium",  # minimal|low|medium|high
    verbosity: str = "medium",        # low|medium|high

    # Generation controls
    temperature: float = 1.0,
    top_p: float = 1.0,
    max_completion_tokens: int = 128000,

    # Prompt noise control
    num_noise_inputs: Optional[int] = 0,  # default 0; when None => tag 'noiseVar0'

    # Sweep: max critical path
    mcp_start: int = 1,
    mcp_stop: int = 1,
    mcp_step: int = 1,

    # Graph shape
    num_total_nodes: int = 10,
    core_nodes: int = 10,                # min_calls baseline (will be max(core_nodes, mcp+1))
    num_disconnected_nodes: int = 0,

    # Type description options
    show_variable_names_in_description: bool = False,
    rename_variables: bool = False,
    use_subtypes: bool = False,
    num_supertypes: int = 5,
    function_shuffle_seed: int = 42,
    repeat_known_variable_values: bool = False,
    on_wrong_inputs: str= "Execute"
):
    """
    Build graphs and run evaluation with full per-trial logging; save per-seed JSONs only.
    """

    # 1. Adjust settings
    temperature, thinking_budget, max_completion_tokens = _adjust_model_settings(
        model, temperature, thinking_budget, max_completion_tokens
    )

    print_args_banner(
        "run",
        model=model,
        root_save_dir=root_save_dir,
        experiment_name=experiment_name,
        num_graphs_per_config=num_graphs_per_config,
        overwrite_graphs=overwrite_graphs,
        overwrite_results=overwrite_results,
        save_aggregate_outputs=save_aggregate_outputs,
        num_trials_per_graph=num_trials_per_graph,
        use_types=use_types,
        verbose=verbose,
        thinking_budget=thinking_budget,
        verbosity=verbosity,
        temperature=temperature,
        top_p=top_p,
        max_completion_tokens=max_completion_tokens,
        mcp_start=mcp_start, mcp_stop=mcp_stop, mcp_step=mcp_step,
        num_total_nodes=num_total_nodes, core_nodes=core_nodes, num_disconnected_nodes=num_disconnected_nodes,
        show_variable_names_in_description=show_variable_names_in_description,
        rename_variables=rename_variables,
        use_subtypes=use_subtypes, num_supertypes=num_supertypes,
        num_noise_inputs=num_noise_inputs,
        function_shuffle_seed=function_shuffle_seed,
        repeat_known_variable_values=repeat_known_variable_values,
        on_wrong_inputs=on_wrong_inputs
    )

    # Sanity checks
    if num_graphs_per_config <= 0:
        raise ValueError("num_graphs_per_config must be >= 1")
    if mcp_step == 0:
        raise ValueError("mcp_step must not be 0")

    # Deterministic seeds for graphs per config
    seeds_for_graphs: List[int] = [i * 100 for i in range(int(num_graphs_per_config))]
    print(f"[run] seeds_for_graphs={seeds_for_graphs}")

    # 2. Build config grid
    graph_config_grid = _build_config_grid(
        mcp_start, mcp_stop, mcp_step,
        num_total_nodes, core_nodes, num_disconnected_nodes,
        show_variable_names_in_description, rename_variables,
        use_subtypes, num_supertypes
    )

    # Graph output root
    graph_root = os.path.join(root_save_dir, "graphs", experiment_name)
    ensure_dir(graph_root)

    # Prepare aggregate rows across all configs for CSV
    aggregate_rows_all: List[Dict[str, Any]] = []

    # Iterate configs
    for cfg in graph_config_grid:
        cfg_tag = _config_tag(cfg)
        cfg_graph_dir = os.path.join(graph_root, cfg_tag)
        ensure_dir(cfg_graph_dir)

        # 3. Build or load graphs
        graphs_for_cfg = _build_or_load_graphs(
            cfg, cfg_graph_dir, seeds_for_graphs, overwrite_graphs, verbose
        )

        # Prepare result directory
        model_base = model_basename(model)
        filename_tag = _filename_tag(cfg, thinking_budget, verbosity, num_noise_inputs)
        results_dir = os.path.join(root_save_dir, "results", model_base, experiment_name, filename_tag)
        ensure_dir(results_dir)

        # Aggregate for this config
        aggregate_trials_for_cfg: List[Dict[str, Any]] = []
        minimum_calls_eff = cfg.min_calls

        # Run evaluation per graph/seed
        for gi, graph_info in enumerate(graphs_for_cfg):
            graph_seed = int(graph_info.get("graph_seed", seeds_for_graphs[gi]))
            per_seed_results_path = os.path.join(results_dir, f"{graph_seed}.json")

            # Skip or fold existing results
            if (not overwrite_results) and os.path.exists(per_seed_results_path):
                try:
                    with open(per_seed_results_path, "r") as f:
                        prev = json.load(f)
                    aggregate_trials_for_cfg.extend(prev.get("trials", []))
                    print(f"[skip] results exist (overwrite_results=False): {per_seed_results_path}")
                    continue
                except Exception as e:
                    print(f"[warn] failed to load existing results (will recompute): {per_seed_results_path} ({e})")

            # Reconstruct graph
            function_tree = FunctionDependencyTree()
            graph_data = copy.deepcopy(graph_info["graph_data"])
            function_tree.graph = graph_from_node_link_data(graph_data)

            # Optional type-based reformat
            if use_types and hasattr(function_tree, 'reformat_tree_with_shared_types'):
                function_tree.reformat_tree_with_shared_types(
                    show_variable_names_in_description=cfg.show_variable_names_in_description,
                    rename_variables=cfg.rename_variables,
                    use_subtypes=cfg.use_subtypes,
                    num_supertypes=cfg.num_supertypes,
                )

            # Target node/variable
            desired_output_func_name = graph_info.get("target_node")
            if desired_output_func_name is None:
                sinks = [n for n in function_tree.graph.nodes if function_tree.graph.out_degree(n) == 0]
                desired_output_func_name = sinks[0] if sinks else list(function_tree.graph.nodes)[-1]
            desired_output_variable = function_tree.graph.nodes[desired_output_func_name]['function'].output

            # Evaluator helper
            evaluator = ToolCallingEvaluator(
                function_tree, 
                num_noise_inputs=int(num_noise_inputs) if num_noise_inputs is not None else 0, 
                function_shuffle_seed=function_shuffle_seed, 
                error_detail_level="None",  
                on_wrong_inputs=on_wrong_inputs,
                repeat_known_variable_values=repeat_known_variable_values
            )

            trials_for_this_graph: List[Dict[str, Any]] = []
            failure_counter_this: Counter = Counter()

            # Run trials
            for trial_idx in range(int(num_trials_per_graph)):
                trial_seed = derive_trial_seed(cfg_tag, graph_seed, trial_idx)
                set_global_rng(trial_seed, trial_seed + 11)

                # 4. Initialize variables
                input_vars, known, var_to_type, type_to_vars = _initialize_trial_variables(
                    evaluator, function_tree, use_types
                )

                # Prepare first message
                input_prompt = evaluator._get_input_prompt(desired_output_variable, input_vars)
                messages = [{"role": "user", "content": input_prompt}]
                tools = evaluator._get_function_schemas()

                # API extras
                api_extras = {
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_completion_tokens": max_completion_tokens,
                }
                if is_reasoning_model(model):
                    api_extras["reasoning_effort"] = thinking_budget
                    if "gpt-5" in (model or ""):
                        api_extras["verbosity"] = verbosity

                max_num_calls = minimum_calls_eff * 2

                # 5. Run tool calling loop
                num_calls, call_failures, call_sequence, final_msg_content = _run_tool_calling_loop(
                    model, messages, tools, api_extras, evaluator, known, use_types, var_to_type, max_num_calls
                )

                # 6. Evaluate result
                target_value_str = str(evaluator.variable_values[desired_output_variable])
                was_correct, failure_reason, efficiency = _evaluate_trial_result(
                    final_msg_content, num_calls, max_num_calls, minimum_calls_eff, target_value_str
                )

                if failure_reason:
                    call_failures.append(failure_reason)
                    failure_counter_this[failure_reason] += 1

                # 7. Build trial record
                trial_record = _build_trial_record(
                    graph_seed, trial_idx, desired_output_func_name, was_correct,
                    input_prompt, final_msg_content, target_value_str,
                    num_calls, efficiency, minimum_calls_eff,
                    call_failures, call_sequence
                )
                trials_for_this_graph.append(trial_record)

            # 8. Build per-seed payload
            payload = _build_per_seed_payload(
                model_base, experiment_name, cfg, cfg_tag, graph_seed,
                thinking_budget, verbosity, num_noise_inputs,
                trials_for_this_graph, failure_counter_this
            )

            # Check for API errors
            write_file = True
            for trial in payload["trials"]:
                 for call in trial["call_sequence"]:
                    if call.get("event") == "api_error":
                        write_file = False
                        break
                 if not write_file: break
            
            if not write_file:
                print(f"[skip] not writing results due to API errors: {per_seed_results_path}")
                continue

            # Save per-seed results
            with open(per_seed_results_path, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"[result] saved: {per_seed_results_path}")

            aggregate_trials_for_cfg.extend(trials_for_this_graph)

        # 9. Build aggregate row
        row = _build_aggregate_row(
            model, experiment_name, cfg, cfg_tag,
            thinking_budget, verbosity, num_noise_inputs,
            aggregate_trials_for_cfg
        )
        aggregate_rows_all.append(row)

    # Save aggregate CSV
    if save_aggregate_outputs and aggregate_rows_all:
        write_aggregate_outputs(root_save_dir, model, experiment_name, aggregate_rows_all)
    elif save_aggregate_outputs:
        print("[aggregate] nothing to write (no rows).")

    print("Done.")


if __name__ == "__main__":
    import fire
    fire.Fire({"run": run})