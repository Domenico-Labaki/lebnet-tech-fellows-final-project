# StateBench three-minute video script

Target length: about 3:00 at a measured pace. Record at 16:9 and use the
included slide deck full-screen. Before recording, replace `[Your Name]` in the
report and close any terminal that displays the API key.

| Time | What to show | What to say |
|---|---|---|
| 0:00–0:24 | **Slide 1.** Start on the title, then point to the research-question box and the hidden graph. | “My project is StateBench, a reproduction and extension of FuncBenchGen. Models can understand individual tools yet fail when later calls depend on earlier outputs. My question is: if the task and model stay fixed, does the way we represent working state change exact success and context cost?” |
| 0:24–0:52 | **Slide 2.** Highlight the three visible inputs, then trace the blue hidden path from `v0` to `T`. | “FuncBenchGen turns this into a controlled hidden-graph problem. Each node is a synthetic function that produces one typed value, and each edge is a dependency. The model sees initial values, randomized tool schemas, and a target, but never sees the graph or valid path. The evaluator accepts only the exact deterministic target within the call budget.” |
| 0:52–1:17 | **Slide 3.** Move left to right: Easy, Deep, Connected distractors. | “Difficulty is adjustable. Easy graphs have a short path. Deep graphs require intermediate values to survive across more calls. Connected irrelevant nodes are especially challenging: they are not on the solution path, but their types are compatible, so wrong routes look legitimate.” |
| 1:17–1:52 | **Slide 4.** Follow the five numbered cards. At 1:39, briefly switch to the README section titled ‘Run and resume’ and show the three commands—do not execute them—then return to the slide. | “I first ran a paper-style calibration. Qwen3.6-27B dropped from two out of five on easy graphs to zero on deep graphs, reproducing the depth effect but leaving no headroom for a state comparison. I therefore used one model-compatible interface for every strategy: visible variable names, explicit premature-call errors, and one call per turn. Fifteen frozen graphs across three conditions were paired with five strategies while model, tool order, call budget, and evaluator stayed fixed. Atomic checkpoints made all seventy-five executions resumable through Groq rate limits.” |
| 1:52–2:33 | **Slide 5.** First indicate the connected-distractor bars; then point to the three compact-state bars on the right. | “The main separation appears with connected distractors. Full history and paper restatement reached three out of five, while JSON and static dependency pruning reached five out of five and fifteen out of fifteen overall. But static pruning did not actually compress: it retained the same 405 cumulative state bytes as JSON. I added a live public-schema filter. It reduced state to 157 bytes, a 61.2 percent reduction, but fell to thirteen out of fifteen because two connected trials exhausted the reasoning allowance. So the useful result is a trade-off: smaller state can remove evidence the model still needs.” |
| 2:33–3:00 | **Slide 6.** Point to the conclusion sentence. During the last ten seconds, show the repository root, open `report/StateBench_Report.docx` at its first page, then end on the slide. | “Overall, I reproduced the paper’s depth and connected-distractor findings qualitatively, implemented a controlled working-state extension, and found that structured state recovered failures while aggressive pruning weakened reliability. I do not claim that more reasoning tokens would necessarily fix those failures; five fixtures per condition make the result descriptive. The submission package contains the reproducible runner and frozen data, the four-page report, final figures, and this three-minute explanation. The central lesson is that reliability depends on what state remains, not only how little state remains.” |

## Screen-recording checklist

- Use `video/StateBench_3_Minute_Deck.pptx` in full-screen presentation mode.
- Keep `README.md`, `results/final/`, and `report/StateBench_Report.docx` open
  before recording so transitions are immediate.
- Zoom the README to the `Run and resume` commands; never show `.env`.
- In the final repository view, briefly reveal `configs/final.yaml`,
  `results/final/processed/`, `report/`, and `video/`.
- Personalize the report name and reflection before capturing its first page.
