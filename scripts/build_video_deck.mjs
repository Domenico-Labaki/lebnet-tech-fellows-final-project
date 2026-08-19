import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(ROOT, "video");
const SLIDES = path.join(OUT, "slides");
const W = 1280;
const H = 720;

const C = {
  bg: "#F7F8FA",
  ink: "#111827",
  muted: "#667085",
  line: "#D0D5DD",
  pale: "#EAF2FF",
  blue: "#2563EB",
  cyan: "#0EA5E9",
  teal: "#0F9D8A",
  red: "#D92D20",
  amber: "#D97706",
  white: "#FFFFFF",
  dark: "#183153",
};

function text(slide, value, left, top, width, height, options = {}) {
  const box = slide.shapes.add({
    geometry: "textbox",
    position: { left, top, width, height },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  box.text = value;
  box.text.style = {
    fontSize: options.size ?? 24,
    bold: options.bold ?? false,
    color: options.color ?? C.ink,
    typeface: "Aptos",
    alignment: options.align ?? "left",
    verticalAlignment: options.valign ?? "top",
    autoFit: "shrinkText",
  };
  return box;
}

function box(slide, left, top, width, height, options = {}) {
  const geometry = options.geometry ?? "roundRect";
  return slide.shapes.add({
    geometry,
    position: { left, top, width, height },
    fill: options.fill ?? C.white,
    line: { style: "solid", fill: options.stroke ?? C.line, width: options.strokeWidth ?? 1 },
    ...(geometry === "rect" || geometry === "textbox" || geometry === "roundRect"
      ? { borderRadius: options.radius ?? "rounded-xl" }
      : {}),
    shadow: options.shadow ?? "shadow-sm",
  });
}

function line(slide, left, top, width, height, color = C.line, weight = 3) {
  const normalizedLeft = width < 0 ? left + width : left;
  const normalizedTop = height < 0 ? top + height : top;
  return slide.shapes.add({
    geometry: "line",
    position: {
      left: normalizedLeft,
      top: normalizedTop,
      width: Math.abs(width),
      height: Math.abs(height),
      horizontalFlip: width < 0,
      verticalFlip: height < 0,
    },
    fill: "none",
    line: { style: "solid", fill: color, width: weight },
  });
}

function arrow(slide, left, top, width = 45, height = 18, color = C.blue) {
  return slide.shapes.add({
    geometry: "rightArrow",
    position: { left, top, width, height },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
}

function node(slide, x, y, label, fill = C.white, stroke = C.blue, size = 54) {
  const n = box(slide, x, y, size, size, { geometry: "ellipse", fill, stroke, shadow: "shadow-none" });
  text(slide, label, x, y + 12, size, 28, { size: 17, bold: true, color: C.dark, align: "center" });
  return n;
}

function header(slide, section, title, number) {
  text(slide, section.toUpperCase(), 58, 32, 400, 24, { size: 13, bold: true, color: C.blue });
  text(slide, title, 58, 64, 1130, 58, { size: 34, bold: true, color: C.ink });
  line(slide, 58, 126, 1164, 0, C.line, 1);
  text(slide, String(number).padStart(2, "0"), 1170, 668, 52, 24, { size: 13, bold: true, color: C.muted, align: "right" });
}

function notes(slide, body, sources) {
  slide.speakerNotes.textFrame.setText(`${body}\n\n[Sources]\n${sources.map((s) => `- ${s}`).join("\n")}\n[/Sources]`);
  slide.speakerNotes.setVisible(true);
}

function addMiniDag(slide, left, top, mode) {
  if (mode === "easy") {
    line(slide, left + 82, top + 121, 150, 0, C.teal, 4);
    node(slide, left + 28, top + 94, "A", C.pale);
    node(slide, left + 232, top + 94, "T", "#D1FAE5", C.teal);
    return;
  }
  const xs = [left, left + 88, left + 176, left + 264];
  const ys = [top + 112, top + 34, top + 142, top + 72];
  line(slide, xs[0] + 44, ys[0] + 20, 52, ys[1] - ys[0], C.blue, 3);
  line(slide, xs[1] + 44, ys[1] + 20, 52, ys[2] - ys[1], C.blue, 3);
  line(slide, xs[2] + 44, ys[2] + 20, 52, ys[3] - ys[2], C.blue, 3);
  if (mode === "connected") {
    line(slide, xs[1] + 27, ys[1] + 27, 84, 210, C.red, 2);
    line(slide, xs[2] + 27, ys[2] + 27, 84, -148, C.red, 2);
  }
  node(slide, xs[0], ys[0], "A", C.pale);
  node(slide, xs[1], ys[1], "B", C.pale);
  node(slide, xs[2], ys[2], "C", C.pale);
  node(slide, xs[3], ys[3], "T", "#D1FAE5", C.teal);
  if (mode === "connected") {
    node(slide, xs[2], top + 248, "X", "#FEE4E2", C.red, 46);
    node(slide, xs[3], top - 2, "Y", "#FEE4E2", C.red, 46);
  }
}

async function main() {
  await fs.mkdir(SLIDES, { recursive: true });
  await fs.mkdir(path.join(OUT, ".build"), { recursive: true });
  const deck = Presentation.create({ slideSize: { width: W, height: H } });

  // 1 — Cover
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    text(slide, "STATEBENCH", 62, 58, 300, 24, { size: 13, bold: true, color: C.blue });
    text(slide, "Working state for\nreliable multi-step\ntool use", 62, 126, 650, 250, { size: 52, bold: true, color: C.ink });
    text(slide, "Representative FuncBenchGen reproduction + controlled Qwen3.6-27B extension", 66, 408, 620, 70, { size: 21, color: C.muted });
    box(slide, 66, 512, 560, 92, { fill: C.dark, stroke: C.dark, shadow: "shadow-none" });
    text(slide, "Research question", 88, 530, 160, 22, { size: 13, bold: true, color: "#B9D7FF" });
    text(slide, "Does state representation change exact success and context cost?", 88, 557, 510, 36, { size: 19, bold: true, color: C.white });

    line(slide, 772, 186, 120, -70, C.blue, 4);
    line(slide, 772, 186, 120, 80, C.blue, 4);
    line(slide, 932, 116, 135, 98, C.blue, 4);
    line(slide, 932, 266, 135, -52, C.blue, 4);
    line(slide, 802, 423, 126, -80, C.red, 3);
    node(slide, 728, 152, "API", C.pale, C.blue, 70);
    node(slide, 890, 80, "v1", C.white, C.blue, 66);
    node(slide, 890, 232, "v2", C.white, C.blue, 66);
    node(slide, 1064, 179, "T", "#D1FAE5", C.teal, 76);
    node(slide, 760, 395, "X", "#FEE4E2", C.red, 60);
    text(slide, "hidden dependency graph", 788, 508, 330, 28, { size: 17, bold: true, color: C.dark, align: "center" });
    text(slide, "75 paired executions • 15 frozen graphs • 5 state strategies", 744, 548, 420, 52, { size: 17, color: C.muted, align: "center" });
    text(slide, "LebNet Tech Fellows 2026", 62, 670, 320, 22, { size: 13, color: C.muted });
    notes(slide, "Opening slide. Introduce the benchmark problem and research question.", [
      "https://arxiv.org/html/2509.26553v2",
      "Local: configs/final.yaml",
    ]);
  }

  // 2 — Hidden DAG
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    header(slide, "Paper concept", "The model must traverse a graph it cannot see", 2);
    box(slide, 58, 158, 300, 456, { fill: C.white });
    text(slide, "What the model sees", 82, 184, 250, 34, { size: 22, bold: true, color: C.dark });
    const seen = [
      ["Initial values", "typed inputs already known"],
      ["Tool schemas", "randomized function list"],
      ["Target", "one value to produce"],
    ];
    seen.forEach(([a, b], i) => {
      box(slide, 82, 244 + i * 102, 250, 78, { fill: i === 2 ? "#D1FAE5" : C.pale, stroke: i === 2 ? C.teal : "#B9D7FF", shadow: "shadow-none" });
      text(slide, a, 98, 257 + i * 102, 220, 24, { size: 17, bold: true, color: C.dark });
      text(slide, b, 98, 284 + i * 102, 220, 22, { size: 14, color: C.muted });
    });
    arrow(slide, 376, 356, 60, 22, C.blue);

    box(slide, 454, 158, 768, 456, { fill: "#F0F5FF", stroke: "#B9D7FF" });
    text(slide, "What the evaluator hides", 484, 184, 330, 34, { size: 22, bold: true, color: C.dark });
    line(slide, 540, 388, 150, -100, C.blue, 4);
    line(slide, 540, 388, 150, 100, C.blue, 4);
    line(slide, 736, 288, 160, 100, C.blue, 4);
    line(slide, 736, 488, 160, -100, C.blue, 4);
    node(slide, 500, 350, "v0", C.white, C.blue, 74);
    node(slide, 688, 250, "f1", C.white, C.blue, 72);
    node(slide, 688, 450, "f2", C.white, C.blue, 72);
    node(slide, 900, 350, "T", "#D1FAE5", C.teal, 78);
    node(slide, 1048, 486, "X", "#FEE4E2", C.red, 60);
    text(slide, "edges + valid dependency path", 650, 558, 360, 26, { size: 17, bold: true, color: C.blue, align: "center" });
    text(slide, "Success requires the exact target value—not merely plausible tool calls.", 58, 638, 1050, 28, { size: 18, bold: true, color: C.ink });
    notes(slide, "Explain that nodes are single-output functions and edges represent typed dependencies. The benchmark hides the graph, so the model must infer a valid sequence while preserving intermediate values.", [
      "https://arxiv.org/html/2509.26553v2",
    ]);
  }

  // 3 — Difficulty controls
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    header(slide, "Paper concept", "Difficulty is controlled, not guessed", 3);
    const cards = [
      { x: 58, title: "Easy", sub: "depth 1", accent: C.teal, mode: "easy", note: "short dependency path" },
      { x: 450, title: "Deep", sub: "depth 4", accent: C.blue, mode: "deep", note: "more state must survive" },
      { x: 842, title: "Connected distractors", sub: "+10 type-compatible tools", accent: C.red, mode: "connected", note: "irrelevant routes look valid" },
    ];
    for (const c of cards) {
      box(slide, c.x, 162, 350, 466, { fill: C.white, stroke: c.accent });
      text(slide, c.title, c.x + 24, 184, 300, 30, { size: 23, bold: true, color: C.ink });
      text(slide, c.sub, c.x + 24, 220, 300, 24, { size: 15, bold: true, color: c.accent });
      addMiniDag(slide, c.x + 20, 258, c.mode);
      text(slide, c.note, c.x + 24, 572, 302, 34, { size: 16, color: C.muted, align: "center" });
    }
    text(slide, "Connected irrelevant nodes are hard because they share valid types with the solution graph.", 82, 651, 1100, 28, { size: 18, bold: true, color: C.dark, align: "center" });
    notes(slide, "Describe the paper's controllable axes: dependency depth and irrelevant functions. Emphasize connected distractors because their type-compatible edges create convincing wrong routes.", [
      "https://arxiv.org/html/2509.26553v2",
    ]);
  }

  // 4 — Experiment design
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    header(slide, "Execution", "A faithful core plus a model-compatible extension", 4);
    for (const x of [264, 508, 752, 996]) arrow(slide, x, 256, 42, 18, C.blue);
    const steps = [
      [58, "01", "Paper-style calibration", "Opaque schemas\n2/5 easy → 0/5 deep"],
      [302, "02", "Shared interface", "Visible variable names\nexplicit premature-call errors"],
      [546, "03", "Frozen fixtures", "5 seeds × 3 conditions\nidentical tasks per strategy"],
      [790, "04", "Five state strategies", "History • restatement • JSON\nstatic prune • live prune"],
      [1034, "05", "Exact evaluator", "target value within\n2× minimum calls"],
    ];
    for (const [x, n, titleText, bodyText] of steps) {
      box(slide, x, 176, 188, 220, { fill: C.white, stroke: n === "04" ? C.blue : C.line });
      text(slide, n, x + 18, 194, 52, 24, { size: 14, bold: true, color: C.blue });
      text(slide, titleText, x + 18, 230, 154, 54, { size: 20, bold: true, color: C.ink });
      text(slide, bodyText, x + 18, 306, 154, 68, { size: 14, color: C.muted });
    }
    box(slide, 58, 442, 1164, 166, { fill: C.dark, stroke: C.dark, shadow: "shadow-none" });
    text(slide, "75 paired executions", 86, 472, 330, 44, { size: 32, bold: true, color: C.white });
    text(slide, "15 graphs × 5 strategies", 88, 523, 320, 30, { size: 19, color: "#B9D7FF" });
    text(slide, "Fixed across strategies", 470, 470, 230, 26, { size: 15, bold: true, color: "#B9D7FF" });
    text(slide, "model • prompts • graph • tool order • call budget • success rule", 470, 509, 650, 38, { size: 20, bold: true, color: C.white });
    text(slide, "Live pruning is reported as a post hoc mechanism-driven follow-up.", 470, 555, 630, 24, { size: 15, color: "#D0D5DD" });
    notes(slide, "Separate the partial paper-style reproduction from the final controlled extension. Explain that all state strategies receive the same compatible interface and frozen tasks, preserving the paired comparison.", [
      "https://arxiv.org/html/2509.26553v2",
      "Local: configs/final.yaml",
      "Local: docs/METHODOLOGY.md",
    ]);
  }

  // 5 — Results
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    header(slide, "Results", "Structured state wins—until compression becomes too aggressive", 5);
    const reliabilityBytes = new Uint8Array(await fs.readFile(path.join(ROOT, "results", "final", "figures", "reliability_by_configuration.png")));
    box(slide, 58, 154, 720, 426, { fill: C.white });
    slide.images.add({ blob: reliabilityBytes, contentType: "image/png", alt: "Success counts by task condition and working-state strategy", fit: "contain", position: { left: 74, top: 166, width: 688, height: 394 } });
    box(slide, 800, 154, 422, 426, { fill: C.white });
    text(slide, "Compact-state trade-off", 826, 180, 360, 28, { size: 20, bold: true, color: C.dark });
    text(slide, "Cumulative reminder bytes", 826, 216, 300, 22, { size: 14, color: C.muted });
    const compactRows = [
      { y: 266, label: "JSON", bytes: 405, success: "15/15", color: C.teal },
      { y: 354, label: "Static prune", bytes: 405, success: "15/15", color: "#A56AA1" },
      { y: 442, label: "Live prune", bytes: 157, success: "13/15", color: C.amber },
    ];
    compactRows.forEach((row) => {
      text(slide, row.label, 826, row.y, 140, 24, { size: 16, bold: true, color: C.ink });
      text(slide, row.success, 1120, row.y, 72, 24, { size: 16, bold: true, color: row.color, align: "right" });
      box(slide, 826, row.y + 34, 270, 18, { geometry: "rect", fill: "#EAECF0", stroke: "#EAECF0", radius: 7, shadow: "shadow-none" });
      box(slide, 826, row.y + 34, 270 * (row.bytes / 405), 18, { geometry: "rect", fill: row.color, stroke: row.color, radius: 7, shadow: "shadow-none" });
      text(slide, `${row.bytes} B`, 1106, row.y + 32, 86, 22, { size: 14, bold: true, color: C.muted, align: "right" });
    });
    text(slide, "61.2% less state", 826, 522, 180, 22, { size: 15, bold: true, color: C.amber });
    text(slide, "but two connected failures", 996, 522, 200, 22, { size: 15, color: C.muted, align: "right" });
    box(slide, 58, 604, 356, 70, { fill: "#D1FAE5", stroke: C.teal, shadow: "shadow-none" });
    text(slide, "JSON + static pruning", 76, 616, 320, 20, { size: 15, bold: true, color: C.teal });
    text(slide, "15/15 exact success", 76, 641, 320, 24, { size: 20, bold: true, color: C.dark });
    box(slide, 430, 604, 370, 70, { fill: C.pale, stroke: C.blue, shadow: "shadow-none" });
    text(slide, "Connected distractors", 448, 616, 330, 20, { size: 15, bold: true, color: C.blue });
    text(slide, "full history falls to 3/5", 448, 641, 330, 24, { size: 20, bold: true, color: C.dark });
    box(slide, 816, 604, 406, 70, { fill: "#FFF3E8", stroke: C.amber, shadow: "shadow-none" });
    text(slide, "Live pruning", 834, 616, 365, 20, { size: 15, bold: true, color: C.amber });
    text(slide, "61.2% smaller • 13/15 success", 834, 641, 365, 24, { size: 20, bold: true, color: C.dark });
    notes(slide, "Walk through the condition-level success chart first, then the compact-state comparison. Stress that static pruning did not compress beyond JSON. Live pruning did compress, but lost two connected trials, so the conclusion is a trade-off rather than a win.", [
      "Local: results/final/processed/strategy_summary.csv",
      "Local: results/final/processed/strategy_by_config.csv",
      "Local: results/final/figures/reliability_by_configuration.png",
      "Local: results/final/figures/live_pruning_comparison.png",
    ]);
  }

  // 6 — Conclusion and deliverables
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    header(slide, "Conclusion", "Smaller state is useful only when it preserves the right evidence", 6);
    box(slide, 58, 164, 718, 430, { fill: C.dark, stroke: C.dark, shadow: "shadow-none" });
    text(slide, "What changed", 88, 194, 280, 26, { size: 15, bold: true, color: "#B9D7FF" });
    text(slide, "The paper’s state-tracking problem\nwas reproduced and then reframed\nas a state-representation experiment.", 88, 236, 620, 130, { size: 32, bold: true, color: C.white });
    text(slide, "Explicit structured state recovered failures. Repeating every value helped only slightly. Aggressive live pruning reduced context but weakened connected-task reliability.", 88, 398, 620, 116, { size: 19, color: "#E4E7EC" });
    text(slide, "Conclusion: reliability depends on what state remains—not only how little state remains.", 88, 536, 620, 38, { size: 19, bold: true, color: "#8FD3FF" });

    text(slide, "Submission package", 826, 174, 330, 32, { size: 23, bold: true, color: C.dark });
    const deliverables = [
      ["01", "Reproducible repository", "runner • frozen fixtures • resume"],
      ["02", "Four-page report", "method • results • limitations"],
      ["03", "Three-minute video", "concept → execution → output"],
    ];
    deliverables.forEach(([n, titleText, sub], i) => {
      const y = 228 + i * 116;
      box(slide, 822, y, 400, 92, { fill: C.white, stroke: C.line });
      box(slide, 840, y + 18, 52, 52, { geometry: "ellipse", fill: C.pale, stroke: C.blue, shadow: "shadow-none" });
      text(slide, n, 840, y + 31, 52, 22, { size: 15, bold: true, color: C.blue, align: "center" });
      text(slide, titleText, 912, y + 16, 282, 28, { size: 19, bold: true, color: C.ink });
      text(slide, sub, 912, y + 50, 282, 24, { size: 14, color: C.muted });
    });
    text(slide, "Replace [Your Name] in the report before submission.", 826, 594, 390, 30, { size: 15, bold: true, color: C.red });
    notes(slide, "Close with the qualified contribution and point to the final repository, report, and video deliverables. Do not claim that more reasoning tokens would necessarily repair the live-pruning failures.", [
      "Local: report/StateBench_Report.docx",
      "Local: docs/RESULTS.md",
      "Local: README.md",
    ]);
  }

  for (const [index, slide] of deck.slides.items.entries()) {
    const png = await deck.export({ slide, format: "png", scale: 1 });
    const stem = `slide-${index + 1}`;
    await fs.writeFile(path.join(SLIDES, `${stem}.png`), new Uint8Array(await png.arrayBuffer()));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(OUT, ".build", `${stem}.layout.json`), await layout.text());
  }

  const montage = await deck.export({ format: "webp", montage: true, scale: 1 });
  await fs.writeFile(path.join(OUT, ".build", "deck-montage.webp"), new Uint8Array(await montage.arrayBuffer()));
  const pptx = await PresentationFile.exportPptx(deck);
  await pptx.save(path.join(OUT, "StateBench_3_Minute_Deck.pptx"));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
