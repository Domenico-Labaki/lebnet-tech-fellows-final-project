# StateBench

StateBench is a representative reproduction and controlled extension of
[FuncBenchGen](https://arxiv.org/html/2509.26553v2), a contamination-resistant
benchmark for multi-step function calling over hidden dependency graphs. The
project asks whether the way an agent is reminded of already-observed values
changes exact task success and context cost.

The final study uses thinking-enabled `qwen/qwen3.6-27b` through Groq. Fifteen
frozen synthetic graphs—five easy, five deep, and five with connected
distractors—are paired with five working-state strategies for 75 executions.
The graph, tool ordering, initial values, target, evaluator, and call budget are
identical across strategies.

## Main findings

| Strategy | Exact success | Avg. prompt tokens | Avg. added-state bytes |
|---|---:|---:|---:|
| Full history | 12/15 (80.0%) | 7,515 | 0.0 |
| Paper restatement | 13/15 (86.7%) | 8,631 | 1,227.7 |
| JSON state | 15/15 (100%) | 8,887 | 404.7 |
| Static dependency pruning | 15/15 (100%) | 8,744 | 404.7 |
| Live dependency pruning | 13/15 (86.7%) | 7,484 | 156.9 |

The paper-style calibration reproduced the qualitative depth effect: full
history fell from 2/5 on easy graphs to 0/5 on deep graphs. Under the shared
model-compatible protocol, connected distractors reduced full history from 5/5
to 3/5, while JSON and static pruning remained 5/5. Static pruning was a null
compression result because its global type rule retained every JSON value.
The live filter activated and reduced added state by 61.2%, but reliability
fell to 3/5 on connected tasks. This is a descriptive
compression–reliability trade-off, not evidence that a larger reasoning budget
would necessarily repair the failures.

## Repository map

```text
configs/final.yaml                 canonical 75-execution configuration
data/frozen_tasks_compatible/      15 paired synthetic fixtures
src/statebench/                    runner, providers, strategies, evaluator
results/final/processed/           final aggregate CSVs
results/final/figures/             report-ready plots
report/StateBench_Report.docx      four-page mini-paper
video/                             three-minute deck, slide images, and script
docs/METHODOLOGY.md                reproduction and extension protocol
docs/RESULTS.md                    detailed result interpretation
REPRODUCTION.md                    exact commands and provenance
```

Local plans, superseded configurations, exploratory outputs, and the incomplete
reasoning-budget probe are preserved in gitignored `local_notes/`; they are not
part of the public reproduction surface.

## Setup

Requirements: Windows PowerShell, Python 3.11, `uv`, and a Groq account with
access to `qwen/qwen3.6-27b`.

```powershell
uv python install 3.11
uv venv --python 3.11
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set `GROQ_API_KEY` in the gitignored `.env`, then verify the environment:

```powershell
python -m statebench doctor --config configs/final.yaml
python -m statebench preflight --config configs/final.yaml
```

## Run and resume

The frozen fixtures are already included, so regeneration is unnecessary.
Every completed task-strategy pair is written atomically. Short-window rate
limits are retried inside the active trial; a quota stop is safe to resume.

```powershell
python -m statebench run --config configs/final.yaml --resume
python -m statebench analyze --config configs/final.yaml
python scripts/build_report.py
```

Raw model traces are intentionally gitignored because they are large and may
contain provider-specific metadata. The processed CSVs and figures needed to
audit the published numbers are tracked under `results/final/`.

## Validate

```powershell
python -m pytest -q
python -m compileall src scripts
```

The project is a methodological reproduction, not a numerical replication of
the paper's full model suite. Its compatible interface exposes variable names,
returns explicit errors for premature calls, and requires one call per turn for
all five strategies; it never exposes hidden edges, the target path, unobserved
values, or correctness labels. See [docs/METHODOLOGY.md](docs/METHODOLOGY.md)
for the complete boundary.

## Citation and license

The required FuncBenchGen source is vendored at commit
`0718d1cf25b601d0b25fbbbbd064525536cea876` under its BSD-3-Clause license.
See [UPSTREAM.md](UPSTREAM.md) for provenance.
