from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


STRATEGY_ORDER = [
    "full_history",
    "paper_restatement",
    "json_state",
    "dependency_pruning",
    "live_dependency_pruning",
]
STRATEGY_LABELS = {
    "full_history": "Full history",
    "paper_restatement": "Paper restatement",
    "json_state": "JSON state",
    "dependency_pruning": "Static dependency pruning",
    "live_dependency_pruning": "Live dependency pruning",
}
CONFIG_ORDER = ["easy_depth", "deep_depth", "connected_distractors", "live_pressure"]
CONFIG_LABELS = {
    "easy_depth": "Easy",
    "deep_depth": "Deep",
    "connected_distractors": "Connected distractors",
    "live_pressure": "State pressure",
}
FAILURE_LABELS = {
    "value_not_yet_known": "Value not yet known",
    "thinking_output_budget_exhausted": "Thinking budget exhausted",
}
COLORS = {
    "full_history": "#4C78A8",
    "paper_restatement": "#F58518",
    "json_state": "#54A24B",
    "dependency_pruning": "#B279A2",
    "live_dependency_pruning": "#ECA82C",
}


def analyze(
    root: str | Path,
    raw_dir: str = "results/raw",
    processed_dir: str = "results/processed",
    figures_dir: str = "results/figures",
    task_ids: list[str] | None = None,
    strategies: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = Path(root)
    records = []
    for path in (root / raw_dir).glob("*/*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        record["failure_codes"] = ";".join(record["failure_codes"])
        records.append(record)
    if not records:
        raise RuntimeError("No raw result files found.")
    trials = pd.DataFrame(records)
    if task_ids is not None:
        trials = trials[trials["task_id"].isin(task_ids)]
    if strategies is not None:
        trials = trials[trials["strategy"].isin(strategies)]
    if trials.empty:
        raise RuntimeError("No raw result files matched the configured analysis scope.")
    trials["config_id"] = trials["task_id"].str.replace(r"-seed\d+$", "", regex=True)
    trials["efficiency"] = trials["minimum_calls"] / trials["calls"].replace(0, pd.NA)
    trials["success_per_1k_prompt_tokens"] = 1000 * trials["success"].astype(int) / trials["prompt_tokens"].replace(0, pd.NA)
    summary = trials.groupby("strategy", as_index=False).agg(
        success_rate=("success", "mean"),
        avg_prompt_tokens=("prompt_tokens", "mean"),
        avg_completion_tokens=("completion_tokens", "mean"),
        avg_calls=("calls", "mean"),
        avg_state_bytes=("state_bytes", "mean"),
        successes=("success", "sum"),
        trials=("success", "count"),
        total_prompt_tokens=("prompt_tokens", "sum"),
    )
    summary["tokens_per_success"] = summary["total_prompt_tokens"] / summary["successes"].replace(0, pd.NA)
    summary["success_per_1k_prompt_tokens"] = 1000 * summary["successes"] / summary["total_prompt_tokens"].replace(0, pd.NA)
    processed = root / processed_dir
    processed.mkdir(parents=True, exist_ok=True)
    trials.drop(columns=["events", "requests"], errors="ignore").to_csv(processed / "trial_results.csv", index=False)
    summary.to_csv(processed / "strategy_summary.csv", index=False)
    by_config = trials.groupby(["config_id", "strategy"], as_index=False).agg(
        success_rate=("success", "mean"),
        successes=("success", "sum"),
        trials=("success", "count"),
        avg_prompt_tokens=("prompt_tokens", "mean"),
        avg_calls=("calls", "mean"),
        avg_state_bytes=("state_bytes", "mean"),
    )
    by_config.to_csv(processed / "strategy_by_config.csv", index=False)
    failure_counts = (
        trials.assign(failure_code=trials["failure_codes"].replace("", "none"))
        .assign(failure_code=lambda frame: frame["failure_code"].str.split(";"))
        .explode("failure_code")
        .groupby(["strategy", "failure_code"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )
    failure_counts.to_csv(processed / "failure_counts.csv", index=False)
    _plots(root / figures_dir, summary, by_config, failure_counts)
    return trials, summary


def _plots(figures: Path, summary: pd.DataFrame, by_config: pd.DataFrame, failure_counts: pd.DataFrame) -> None:
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.titlesize": 12, "axes.labelsize": 9})

    ordered_summary = summary.set_index("strategy").reindex(STRATEGY_ORDER).dropna().reset_index()
    available_strategies = ordered_summary["strategy"].tolist()
    figure, axis = plt.subplots(figsize=(7.2, 4.2))
    for _, row in ordered_summary.iterrows():
        strategy = row["strategy"]
        axis.scatter(
            row["avg_prompt_tokens"],
            row["success_rate"],
            s=90,
            color=COLORS[strategy],
            zorder=3,
        )
    offsets = {
        "full_history": (8, 7),
        "paper_restatement": (8, 7),
        "json_state": (-58, -18),
        "dependency_pruning": (-118, 9),
        "live_dependency_pruning": (8, -18),
    }
    for _, row in ordered_summary.iterrows():
        strategy = row["strategy"]
        axis.annotate(
            STRATEGY_LABELS[strategy],
            (row["avg_prompt_tokens"], row["success_rate"]),
            xytext=offsets[strategy],
            textcoords="offset points",
            fontsize=8.5,
        )
    axis.set(
        xlabel="Average prompt tokens per trial",
        ylabel="Exact success rate",
        ylim=(max(0, float(ordered_summary["success_rate"].min()) - 0.08), 1.025),
        title="Reliability-context trade-off",
    )
    token_min = float(ordered_summary["avg_prompt_tokens"].min())
    token_max = float(ordered_summary["avg_prompt_tokens"].max())
    token_pad = max(120.0, (token_max - token_min) * 0.13)
    axis.set_xlim(token_min - token_pad, token_max + token_pad)
    axis.grid(axis="both", alpha=0.18)
    figure.tight_layout(pad=0.8)
    figure.savefig(figures / "reliability_vs_context.png", dpi=180)
    plt.close(figure)

    pivot = (
        by_config.pivot(index="config_id", columns="strategy", values="success_rate")
        .reindex(index=CONFIG_ORDER, columns=available_strategies)
        .dropna(how="all")
        .rename(index=CONFIG_LABELS, columns=STRATEGY_LABELS)
    )
    # An annotated heatmap keeps the complete condition-by-strategy comparison
    # while avoiding a second set of grouped bars in the report.
    heatmap = pivot.transpose()
    values = heatmap.to_numpy(dtype=float)
    figure, axis = plt.subplots(figsize=(8.9, 4.35))
    image = axis.imshow(values, cmap="Blues", vmin=0.5, vmax=1.0, aspect="auto")
    axis.set(
        xlabel="Task configuration",
        ylabel="Working-state strategy",
        title="Exact success across paired task configurations",
    )
    axis.set_xticks(np.arange(len(heatmap.columns)), labels=heatmap.columns)
    axis.set_yticks(np.arange(len(heatmap.index)), labels=heatmap.index)
    axis.tick_params(axis="x", rotation=0)
    for row_index in range(values.shape[0]):
        for column_index in range(values.shape[1]):
            value = values[row_index, column_index]
            axis.text(
                column_index,
                row_index,
                f"{int(round(value * 5))}/5",
                ha="center",
                va="center",
                fontsize=10,
                fontweight="bold",
                color="white" if value >= 0.82 else "#17324D",
            )
    colorbar = figure.colorbar(image, ax=axis, fraction=0.035, pad=0.025)
    colorbar.set_label("Exact success rate")
    colorbar.set_ticks([0.6, 0.8, 1.0], labels=["60%", "80%", "100%"])
    axis.set_xticks(np.arange(-0.5, len(heatmap.columns), 1), minor=True)
    axis.set_yticks(np.arange(-0.5, len(heatmap.index), 1), minor=True)
    axis.grid(which="minor", color="white", linewidth=2)
    axis.tick_params(which="minor", bottom=False, left=False)
    for spine in axis.spines.values():
        spine.set_visible(False)
    figure.tight_layout(pad=0.8)
    figure.savefig(figures / "reliability_by_configuration.png", dpi=180)
    plt.close(figure)

    observed_errors = failure_counts[failure_counts["failure_code"] != "none"].copy()
    if not observed_errors.empty:
        error_order = ["value_not_yet_known", "thinking_output_budget_exhausted"]
        pivot = (
            observed_errors.pivot(index="strategy", columns="failure_code", values="count")
            .fillna(0)
            .reindex(index=available_strategies, columns=error_order, fill_value=0)
            .rename(index=STRATEGY_LABELS, columns=FAILURE_LABELS)
        )
        figure, axis = plt.subplots(figsize=(7.2, 4.2))
        pivot.plot(kind="bar", ax=axis, color=["#72B7B2", "#E45756"], width=0.72)
        axis.set(
            xlabel="State strategy",
            ylabel="Recorded error occurrences",
            title="Trace-level error codes",
        )
        short_strategy_labels = {
            "Full history": "Full\nhistory",
            "Paper restatement": "Paper\nrestatement",
            "JSON state": "JSON\nstate",
            "Dependency pruning": "Static\npruning",
            "Live dependency pruning": "Live\npruning",
        }
        axis.set_xticklabels(
            [short_strategy_labels.get(label, label) for label in pivot.index],
            rotation=0,
            ha="center",
        )
        axis.legend(title=None, fontsize=8, frameon=False, loc="upper center", ncol=2)
        axis.grid(axis="y", alpha=0.18)
        for container in axis.containers:
            axis.bar_label(container, fmt="%.0f", padding=2, fontsize=8)
        figure.tight_layout(pad=0.8)
        figure.savefig(figures / "failure_modes.png", dpi=180)
        plt.close(figure)

        figure, axes = plt.subplots(1, 2, figsize=(11.2, 3.55), gridspec_kw={"wspace": 0.38})
        tradeoff, errors = axes
        for _, row in ordered_summary.iterrows():
            strategy = row["strategy"]
            tradeoff.scatter(
                row["avg_prompt_tokens"], row["success_rate"], s=65, color=COLORS[strategy], zorder=3
            )
            tradeoff.annotate(
                STRATEGY_LABELS[strategy],
                (row["avg_prompt_tokens"], row["success_rate"]),
                xytext=offsets[strategy],
                textcoords="offset points",
                fontsize=7.2,
            )
        tradeoff.set(
            xlabel="Average prompt tokens",
            ylabel="Exact success rate",
            ylim=(max(0, float(ordered_summary["success_rate"].min()) - 0.08), 1.025),
            title="A. Reliability-context trade-off",
        )
        tradeoff.set_xlim(token_min - token_pad, token_max + token_pad)
        tradeoff.grid(alpha=0.18)
        pivot.plot(kind="bar", ax=errors, color=["#72B7B2", "#E45756"], width=0.72)
        errors.set(
            xlabel="State strategy",
            ylabel="Error occurrences",
            title="B. Trace-level errors",
        )
        errors.set_xticklabels(
            [
                {
                    "full_history": "Full\nhistory",
                    "paper_restatement": "Paper\nrestatement",
                    "json_state": "JSON\nstate",
                    "dependency_pruning": "Static\npruning",
                    "live_dependency_pruning": "Live\npruning",
                }[item]
                for item in available_strategies
            ],
            rotation=0,
            ha="center",
        )
        if errors.get_legend() is not None:
            errors.get_legend().remove()
        errors.grid(axis="y", alpha=0.18)
        for container in errors.containers:
            errors.bar_label(container, fmt="%.0f", padding=1, fontsize=7)
        figure.subplots_adjust(left=0.07, right=0.99, bottom=0.20, top=0.84, wspace=0.38)
        figure.savefig(figures / "context_and_trace_errors.png", dpi=200)
        plt.close(figure)

    comparison = [
        item for item in ["json_state", "dependency_pruning", "live_dependency_pruning"]
        if item in available_strategies
    ]
    if "live_dependency_pruning" in comparison:
        scoped = by_config[by_config["strategy"].isin(comparison)].copy()
        reliability = (
            scoped.pivot(index="config_id", columns="strategy", values="success_rate")
            .reindex(index=CONFIG_ORDER, columns=comparison)
            .dropna(how="all")
            .rename(index=CONFIG_LABELS, columns=STRATEGY_LABELS)
        )
        state_size = (
            scoped.pivot(index="config_id", columns="strategy", values="avg_state_bytes")
            .reindex(index=CONFIG_ORDER, columns=comparison)
            .dropna(how="all")
            .rename(index=CONFIG_LABELS, columns=STRATEGY_LABELS)
        )
        figure, axes = plt.subplots(1, 2, figsize=(10.4, 3.9), gridspec_kw={"wspace": 0.34})
        colors = [COLORS[item] for item in comparison]
        markers = ["o", "s", "D"]
        config_x = np.arange(len(reliability.index), dtype=float)
        x_offsets = [-0.035, 0.0, 0.035]
        for strategy_index, (strategy, color, marker) in enumerate(zip(comparison, colors, markers)):
            label = STRATEGY_LABELS[strategy]
            values = reliability[label].to_numpy(dtype=float)
            plotted_x = config_x + x_offsets[strategy_index]
            axes[0].plot(
                plotted_x,
                values,
                color=color,
                marker=marker,
                markersize=6,
                linewidth=2,
                label=label,
            )
        for config_index, condition in enumerate(reliability.index):
            condition_values = reliability.loc[condition]
            live_rate = float(condition_values[STRATEGY_LABELS["live_dependency_pruning"]])
            baseline_rate = float(condition_values[STRATEGY_LABELS["json_state"]])
            if condition_values.nunique() == 1:
                axes[0].annotate(
                    f"All {int(round(baseline_rate * 5))}/5",
                    (config_x[config_index], baseline_rate),
                    xytext=(0, 10),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8.5,
                    fontweight="bold",
                    color="#40536A",
                )
            else:
                axes[0].annotate(
                    f"JSON + static {int(round(baseline_rate * 5))}/5",
                    (config_x[config_index], baseline_rate),
                    xytext=(0, 10),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    color="#40536A",
                )
                axes[0].annotate(
                    f"Live {int(round(live_rate * 5))}/5",
                    (config_x[config_index], live_rate),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    fontweight="bold",
                    color=COLORS["live_dependency_pruning"],
                )
        axes[0].set(
            title="A. Reliability across difficulty",
            xlabel="Task configuration",
            ylabel="Exact success rate",
            ylim=(0.52, 1.08),
        )
        axes[0].set_xticks(config_x, labels=reliability.index)
        axes[0].set_yticks([0.6, 0.8, 1.0])
        axes[0].grid(axis="y", alpha=0.18)

        config_y = np.arange(len(state_size.index))[::-1]
        y_offsets = [-0.07, 0.07, 0.0]
        for row_index, (condition, y) in enumerate(zip(state_size.index, config_y)):
            row = state_size.loc[condition]
            live_value = float(row[STRATEGY_LABELS["live_dependency_pruning"]])
            baseline_value = float(row[STRATEGY_LABELS["json_state"]])
            axes[1].hlines(y, live_value, baseline_value, color="#C9CED6", linewidth=4, zorder=1)
            for strategy_index, (strategy, color, marker) in enumerate(zip(comparison, colors, markers)):
                label = STRATEGY_LABELS[strategy]
                value = float(row[label])
                axes[1].scatter(
                    value,
                    y + y_offsets[strategy_index],
                    s=46,
                    color=color,
                    marker=marker,
                    zorder=3,
                )
            axes[1].annotate(
                f"{live_value:.0f} B",
                (live_value, y),
                xytext=(-4, -15),
                textcoords="offset points",
                ha="right",
                fontsize=8,
                color=COLORS["live_dependency_pruning"],
            )
            axes[1].annotate(
                f"{baseline_value:.0f} B\nJSON = static",
                (baseline_value, y),
                xytext=(5, -7),
                textcoords="offset points",
                ha="left",
                va="center",
                fontsize=8,
                color="#4A5563",
            )
        axes[1].set(
            title="B. Live-pruning state reduction",
            xlabel="Cumulative state bytes per trial",
        )
        compact_condition_labels = [
            "Connected\ndistractors" if item == "Connected distractors" else item
            for item in state_size.index
        ]
        axes[1].set_yticks(config_y, labels=compact_condition_labels)
        axes[1].set_xlim(0, max(660, float(state_size.max().max()) * 1.25))
        axes[1].set_ylim(-0.35, len(config_y) - 0.65)
        axes[1].grid(axis="x", alpha=0.18)
        if axes[0].get_legend() is not None:
            axes[0].get_legend().remove()
        handles, labels = axes[0].get_legend_handles_labels()
        figure.legend(handles, labels, loc="upper center", ncol=len(comparison), frameon=False, fontsize=9)
        figure.subplots_adjust(left=0.08, right=0.98, bottom=0.18, top=0.80, wspace=0.34)
        figure.savefig(figures / "live_pruning_comparison.png", dpi=200)
        plt.close(figure)
