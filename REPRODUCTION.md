# Reproduction record

## Locked experiment

- Paper: Maekawa et al., *Towards Reliable Benchmarking: A Contamination Free,
  Controllable Evaluation Framework for Multi-step LLM Function Calling*.
- Upstream source commit: `0718d1cf25b601d0b25fbbbbd064525536cea876`.
- Provider/model: Groq, `qwen/qwen3.6-27b`.
- Model settings: temperature 0, top-p 1, default thinking enabled, 4,096
  completion tokens per turn.
- Graph seeds: 0, 100, 200, 300, 400.
- Evaluation: exact target value within twice the minimum required call count.
- Final panel: 15 frozen tasks × 5 state strategies = 75 paired executions.

The canonical configuration is `configs/final.yaml`. It points to the exact
frozen fixtures and the same raw result directory used to produce the included
processed tables.

## Commands

```powershell
uv python install 3.11
uv venv --python 3.11
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
Copy-Item .env.example .env
# Edit .env and set GROQ_API_KEY.

python -m statebench doctor --config configs/final.yaml
python -m statebench preflight --config configs/final.yaml
python -m statebench run --config configs/final.yaml --resume
python -m statebench analyze --config configs/final.yaml
python scripts/build_report.py
python -m pytest -q
```

`--resume` skips valid completed records. Short rate-limit windows are retried
without discarding the active conversation, and daily quota exhaustion exits
cleanly. No key is stored in configurations, results, or tracked source.

## Reproduction boundary

The paper-style calibration retained opaque descriptions and upstream
wrong-input behavior. Qwen3.6-27B achieved 2/5 on easy graphs, 0/5 on deep
graphs, and 0/5 with connected distractors. This reproduces the depth decline
qualitatively, but the zero deep baseline prevents isolating the incremental
connected-distractor effect in that calibration.

The final extension uses one model-compatible interface for every strategy:
visible input/output variable names, explicit errors for calls whose values are
not yet known, and one call per response. Hidden graph edges, target paths,
ground-truth values, and correctness labels remain unavailable. The final
successes are 12/15, 13/15, 15/15, 15/15, and 13/15 for full history,
restatement, JSON, static pruning, and live pruning respectively.

These are descriptive results over five fixtures per condition. Hosted model
trajectories may differ between reruns even at temperature zero, so exact
numerical identity is not guaranteed.
