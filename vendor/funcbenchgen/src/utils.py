# -*- coding: utf-8 -*-
import os
import re
import json
import random
import hashlib
import numpy as np
import networkx as nx
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

# Local imports
from function_tree import Function

def ensure_dir(path: str) -> None:
    """Create directory if it does not exist."""
    os.makedirs(path, exist_ok=True)

def set_global_rng(py_seed: int, np_seed: Optional[int] = None) -> None:
    """Set Python and NumPy RNG for reproducibility."""
    if py_seed is not None:
        random.seed(py_seed)
    if np_seed is None and py_seed is not None:
        np_seed = py_seed + 7
    if np_seed is not None:
        try:
            np.random.seed(np_seed)
        except Exception:
            pass

def any_exact_number_match(final_msg_content: str, target_value_str: str) -> bool:
    """Check if the target value appears as an exact number in the content."""
    target = target_value_str.strip()
    candidates = re.findall(r'(?<!\d)\d+(?!\d)', final_msg_content)
    candidates = list(set(candidates))
    return any(c == target for c in candidates)

def is_reasoning_model(model: str) -> bool:
    """Heuristic to check reasoning models."""
    m = (model or "").lower()
    return ("gpt-5" in m) or ("gpt5" in m) or ("deepseek" in m)

def model_basename(model: str) -> str:
    """Extract a basename from a model identifier like 'vendor/model'."""
    return (model or "").split("/")[-1] or str(model)

def stable_int_from(*parts: str, modulo: int = 2**31 - 1) -> int:
    """Derive a stable non-negative int from input strings using md5."""
    m = hashlib.md5()
    for p in parts:
        m.update(str(p).encode('utf-8'))
        m.update(b'|')
    return int(m.hexdigest(), 16) % modulo

def sanitize_node_link_data(G: nx.DiGraph) -> dict:
    """Convert a graph with Function objects to a JSON-serializable node-link dict."""
    Gc = G.copy()
    for _, data in Gc.nodes(data=True):
        f = data.get("function")
        if isinstance(f, Function):
            data["function"] = asdict(f)
    return nx.node_link_data(Gc, edges="links")

def graph_from_node_link_data(d: Dict[str, Any]) -> nx.DiGraph:
    """Recreate a networkx graph and rehydrate Function objects."""
    g = nx.node_link_graph(d, edges="links")
    for _, data in g.nodes(data=True):
        f = data.get("function")
        if isinstance(f, dict):
            data["function"] = Function(**f)
    return g

def to_primitive(obj: Any) -> Any:
    """Best-effort conversion for JSON pretty print."""
    try:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, dict):
            return {str(k): to_primitive(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [to_primitive(v) for v in obj]
        json.dumps(obj)
        return obj
    except TypeError:
        return repr(obj)

def print_args_banner(func_name: str, **kwargs):
    """Pretty-print a banner with all arguments to stdout (masking sensitive ones)."""
    sensitive_keys = ("api_key", "apikey", "password", "secret", "authorization", "auth")
    print("=" * 82)
    print(f"{func_name} called with:")
    for k, v in kwargs.items():
        kl = str(k).lower()
        val = "***" if any(s in kl for s in sensitive_keys) else to_primitive(v)
        print(f"  - {k} = {val}")
    print("=" * 82)

def format_noise_var_tag(num_noise_inputs: Optional[int]) -> str:
    """Create a short tag for noise-input setting."""
    if num_noise_inputs is None:
        return "noiseVar0"
    try:
        return f"noiseVar{int(num_noise_inputs)}"
    except Exception:
        return "noiseVar0"
