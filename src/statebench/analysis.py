from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
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
    "dependency_pruning": "Dependency pruning",
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
    figure, axis = plt.subplots(figsize=(9.1 if len(available_strategies) > 4 else 8.2, 4.35))
    pivot.plot(kind="bar", ax=axis, color=[COLORS[item] for item in available_strategies], width=0.78)
    axis.set(
        xlabel="Task configuration",
        ylabel="Exact success rate",
        ylim=(0, 1.14),
        title="Reliability by task configuration",
    )
    axis.set_xticklabels(pivot.index, rotation=0, ha="center")
    axis.legend(
        title=None,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=len(available_strategies),
        fontsize=8,
        frameon=False,
    )
    axis.grid(axis="y", alpha=0.18)
    for container in axis.containers:
        labels = [f"{int(round(bar.get_height() * 5))}/5" for bar in container]
        axis.bar_label(container, labels=labels, padding=2, fontsize=8)
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
        figure, axes = plt.subplots(1, 2, figsize=(10.4, 3.55), gridspec_kw={"wspace": 0.28})
        colors = [COLORS[item] for item in comparison]
        reliability.plot(kind="bar", ax=axes[0], color=colors, width=0.76)
        axes[0].set(
            title="A. Reliability retained",
            xlabel="Task configuration",
            ylabel="Exact success rate",
            ylim=(0, 1.12),
        )
        axes[0].set_xticklabels(reliability.index, rotation=0)
        axes[0].grid(axis="y", alpha=0.18)
        for container in axes[0].containers:
            axes[0].bar_label(container, fmt="%.2f", padding=1, fontsize=7)
        state_size.plot(kind="bar", ax=axes[1], color=colors, width=0.76)
        axes[1].set(
            title="B. Added-state compression",
            xlabel="Task configuration",
            ylabel="Cumulative state bytes per trial",
        )
        axes[1].set_xticklabels(state_size.index, rotation=0)
        axes[1].grid(axis="y", alpha=0.18)
        for container in axes[1].containers:
            axes[1].bar_label(container, fmt="%.0f", padding=1, fontsize=7)
        for axis in axes:
            if axis.get_legend() is not None:
                axis.get_legend().remove()
        handles, labels = axes[0].get_legend_handles_labels()
        figure.legend(handles, labels, loc="upper center", ncol=len(comparison), frameon=False, fontsize=8)
        figure.subplots_adjust(left=0.08, right=0.99, bottom=0.17, top=0.82, wspace=0.28)
        figure.savefig(figures / "live_pruning_comparison.png", dpi=200)
        plt.close(figure)
