#!/usr/bin/env python3
"""Generate charts + markdown report from results.csv."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


COMPLEXITY_ORDER = ["trivial", "simple", "medium", "complex"]
TASK_ORDER = [
    "user_me", "calendar_today", "mail_recent",
    "mail_search", "calendar_free_slot", "chat_search_read",
    "daily_briefing",
]
MODE_ORDER = ["cli", "cli_guided", "mcp", "mcp_guided"]
MODE_LABELS = {
    "cli": "CLI (minimal)",
    "cli_guided": "CLI + hints",
    "mcp": "MCP (minimal)",
    "mcp_guided": "MCP + hints",
}
COLORS = {"cli": "#2E86AB", "cli_guided": "#6A994E", "mcp": "#E63946", "mcp_guided": "#BC4749"}


def bar_compare(df: pd.DataFrame, metric: str, title: str, ylabel: str, out: Path, fmt: str = "{:.0f}") -> None:
    tasks = [t for t in TASK_ORDER if t in df["task"].unique()]
    modes = [m for m in MODE_ORDER if m in df["mode"].unique()]
    n_modes = len(modes)

    x = np.arange(len(tasks))
    w = 0.8 / n_modes
    fig, ax = plt.subplots(figsize=(12, 5.5))

    for i, mode in enumerate(modes):
        vals = [df[(df["task"] == t) & (df["mode"] == mode)][metric].median() for t in tasks]
        offset = (i - (n_modes - 1) / 2) * w
        bars = ax.bar(x + offset, vals, w, label=MODE_LABELS[mode], color=COLORS[mode])
        for bar, v in zip(bars, vals):
            if pd.isna(v):
                continue
            ax.annotate(fmt.format(v), xy=(bar.get_x() + bar.get_width() / 2, v),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels(tasks, rotation=20, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(out, dpi=140)
    plt.close()


def stacked_tokens(df: pd.DataFrame, out: Path) -> None:
    tasks = [t for t in TASK_ORDER if t in df["task"].unique()]
    modes = [m for m in MODE_ORDER if m in df["mode"].unique()]
    labels = []
    cache_c, cache_r, in_tok, out_tok = [], [], [], []
    for t in tasks:
        for m in modes:
            sel = df[(df["task"] == t) & (df["mode"] == m)]
            if sel.empty:
                continue
            short_m = {"cli": "cli", "cli_guided": "cli+g", "mcp": "mcp", "mcp_guided": "mcp+g"}[m]
            labels.append(f"{t}\n({short_m})")
            cache_c.append(sel["cache_creation_input_tokens"].median())
            cache_r.append(sel["cache_read_input_tokens"].median())
            in_tok.append(sel["input_tokens"].median())
            out_tok.append(sel["output_tokens"].median())

    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(16, 6))
    c1 = ax.bar(x, cache_r, label="cache read (0.1x cost)", color="#A8DADC")
    c2 = ax.bar(x, cache_c, bottom=cache_r, label="cache creation (1.25x cost)", color="#F4A261")
    b_in = np.array(cache_r) + np.array(cache_c)
    c3 = ax.bar(x, in_tok, bottom=b_in, label="fresh input (1x)", color="#457B9D")
    b_out = b_in + np.array(in_tok)
    c4 = ax.bar(x, out_tok, bottom=b_out, label="output (5x)", color="#E63946")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("tokens (median)")
    ax.set_title("Token composition per task/mode — bigger cache_read = more context churn")
    ax.legend(loc="upper left")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(out, dpi=140)
    plt.close()


def totals_summary(df: pd.DataFrame, out: Path) -> None:
    modes = [m for m in MODE_ORDER if m in df["mode"].unique()]
    grouped = df.groupby("mode").agg(
        cost=("total_cost_usd", "sum"),
        dur=("duration_s", "sum"),
        turns=("num_turns", "sum"),
        out_tok=("output_tokens", "sum"),
    ).loc[modes]

    metrics = ["cost", "dur", "turns", "out_tok"]
    titles = ["Total cost ($)", "Total duration (s)", "Total turns", "Total output tokens"]
    fig, axes = plt.subplots(1, 4, figsize=(17, 5))
    short = {"cli": "CLI", "cli_guided": "CLI+g", "mcp": "MCP", "mcp_guided": "MCP+g"}
    for ax, metric, title in zip(axes, metrics, titles):
        vals = grouped[metric].values
        bars = ax.bar([short[m] for m in modes], vals, color=[COLORS[m] for m in modes])
        ax.set_title(title)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        for bar, v in zip(bars, vals):
            ax.annotate(f"{v:.2f}" if metric == "cost" else f"{v:.0f}",
                        xy=(bar.get_x() + bar.get_width() / 2, v),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=9)
    fig.suptitle("Aggregate totals across all 7 tasks (3 runs each, summed)")
    plt.tight_layout()
    plt.savefig(out, dpi=140)
    plt.close()


def build_report(df: pd.DataFrame, out: Path, chart_dir: str) -> None:
    modes_present = [m for m in MODE_ORDER if m in df["mode"].unique()]
    per_task = df.groupby(["task", "mode"]).agg(
        cost=("total_cost_usd", "median"),
        dur=("duration_s", "median"),
        turns=("num_turns", "median"),
        in_tok=("input_tokens", "median"),
        out_tok=("output_tokens", "median"),
        cache_c=("cache_creation_input_tokens", "median"),
        cache_r=("cache_read_input_tokens", "median"),
    ).round(4)

    totals = df.groupby("mode").agg(
        cost=("total_cost_usd", "sum"),
        dur=("duration_s", "sum"),
        turns=("num_turns", "sum"),
        out_tok=("output_tokens", "sum"),
    ).loc[modes_present]

    def pick(metric):
        return {m: totals.loc[m, metric] for m in modes_present}

    cost = pick("cost"); dur = pick("dur"); turns = pick("turns"); out_tok = pick("out_tok")
    has_guided = "cli_guided" in modes_present

    n_runs = int(df.groupby(["task", "mode"]).size().median())
    n_tasks = df["task"].nunique()

    lines = []
    lines.append("# graph-cli: CLI vs MCP Token Usage Benchmark")
    lines.append("")
    lines.append("Measurement of `graph-cli` invoked in up to four configurations on the same read-only tasks:")
    lines.append("")
    lines.append("- **CLI (minimal)** — `graph-cli` via the Bash tool, minimal system prompt (\"use graph-cli via Bash\").")
    if has_guided:
        lines.append("- **CLI + hints** — Bash surface, system prompt includes a cookbook of common read-only commands for the benchmarked tasks.")
    lines.append("- **MCP (minimal)** — `graph-cli mcp` stdio MCP server, tools prefixed `mcp__graph-cli__`, minimal system prompt.")
    if "mcp_guided" in modes_present:
        lines.append("- **MCP + hints** — same MCP surface, system prompt includes a task-to-tool cookbook.")
    lines.append("")
    lines.append("Same underlying tool and Microsoft Graph APIs — only the tool surface and priming differ.")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    header = "| Metric |" + "".join(f" {MODE_LABELS[m]} |" for m in modes_present)
    sep = "|---|" + "---|" * len(modes_present)
    lines.append(header)
    lines.append(sep)
    lines.append(f"| Total cost ({n_tasks * n_runs} runs each) |" + "".join(f" ${cost[m]:.2f} |" for m in modes_present))
    lines.append(f"| Total wall time |" + "".join(f" {dur[m]:.0f}s |" for m in modes_present))
    lines.append(f"| Total turns |" + "".join(f" {turns[m]:.0f} |" for m in modes_present))
    lines.append(f"| Total output tokens |" + "".join(f" {out_tok[m]:.0f} |" for m in modes_present))
    lines.append(f"| Cost per turn |" + "".join(f" ${cost[m]/turns[m]:.4f} |" for m in modes_present))
    lines.append(f"| Duration per turn |" + "".join(f" {dur[m]/turns[m]:.1f}s |" for m in modes_present))
    lines.append("")
    lines.append(f"![Aggregate totals]({chart_dir}/totals.png)")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(f"- **{n_tasks} read-only tasks** across graph-cli surfaces (user, mail, calendar, chat).")
    lines.append(f"- **{n_runs} runs** per task/mode. Totals in tables are summed across runs; per-task values are medians.")
    lines.append("- Each run is a fresh `claude -p` process from a clean temp CWD. `CLAUDE.md` auto-discovery is disabled. System prompt differs only in the surface the agent is told to use (Bash vs MCP) and whether a task-to-command cookbook is included.")
    lines.append("- `--strict-mcp-config` restricts available MCP servers to the configured `graph-cli` server. `--allowed-tools` / `--disallowed-tools` restrict the tool surface to the mode under test.")
    lines.append("- Token, cost, and duration figures are taken from Claude's `--output-format json` usage envelope.")
    lines.append("- 5-second delay between runs to avoid Graph API throttling.")
    lines.append("")
    lines.append("## Per-task cost comparison")
    lines.append("")
    lines.append(f"![Cost per task]({chart_dir}/cost_per_task.png)")
    lines.append("")
    lines.append("## Per-task latency")
    lines.append("")
    lines.append(f"![Duration per task]({chart_dir}/duration_per_task.png)")
    lines.append("")
    lines.append("## Per-task turn count")
    lines.append("")
    lines.append(f"![Turns per task]({chart_dir}/turns_per_task.png)")
    lines.append("")
    lines.append("## Token composition")
    lines.append("")
    lines.append(f"![Token stacked]({chart_dir}/tokens_stacked.png)")
    lines.append("")
    lines.append("Observed: CLI modes accumulate larger `cache_read` token volumes than MCP modes. Each Bash call returns raw JSON into the transcript, which becomes part of the cached prefix and is read again on each subsequent turn. MCP's tool schema catalog contributes to higher one-time `cache_creation` tokens, but the per-turn input size is smaller in these runs.")
    lines.append("")
    lines.append("## Latency decomposition")
    lines.append("")
    lines.append(f"Total wall time — bare CLI: **{dur['cli']:.0f}s**, MCP: **{dur['mcp']:.0f}s** (ratio {dur['cli']/dur['mcp']:.2f}x). The gap decomposes into two factors:")
    lines.append("")
    lines.append(f"1. **Turn count.** Bare CLI used {turns['cli']:.0f} turns; MCP used {turns['mcp']:.0f} ({(turns['cli']-turns['mcp'])/turns['mcp']*100:+.0f}%). Each additional turn is an additional model round-trip. Transcript inspection shows Bash-mode turns include occasional `--help` exploration and command re-reads; MCP-mode turns invoke tools directly from pre-loaded schemas.")
    lines.append(f"2. **Per-turn duration.** Bare CLI: {dur['cli']/turns['cli']:.1f}s/turn; MCP: {dur['mcp']/turns['mcp']:.1f}s/turn (ratio {(dur['cli']/turns['cli'])/(dur['mcp']/turns['mcp']):.2f}x). Contributing factors observed in the data and implementation:")
    lines.append("    - **Process spawn per call.** Each Bash invocation starts a new `graph-cli.exe` process, including .NET runtime initialization, token-cache read, and HTTP client construction. The MCP server is spawned once per session; subsequent tool calls reuse the process, auth context, and HTTP connections.")
    lines.append("    - **Input tokens per turn.** CLI turns carry more cached context (JSON output of prior calls), which increases the tokens the model processes on each turn.")
    lines.append("    - **Output tokens per turn.** CLI responses in the final assistant message tend to include or restate more JSON content than MCP responses.")
    if has_guided:
        lines.append("")
        lines.append("Adding task-to-command hints to the system prompt reduces turn count for both surfaces but does not affect per-turn process-spawn overhead or context size, which are properties of the surface itself.")
    lines.append("")
    lines.append("## Surface-specific capabilities: compound operations")
    lines.append("")
    lines.append("The Bash surface supports shell composition — piping output between programs, filtering with `jq`, selecting fields, aggregating. The MCP surface does not; each tool call returns a full response into the model's context, and any filter/transform logic runs in the model itself.")
    lines.append("")
    lines.append("This difference is not exercised by the benchmarked tasks above (they request full records) but it is relevant for realistic workflows. A worked example:")
    lines.append("")
    lines.append("**Task:** \"From my last 50 Inbox emails, list just the sender and subject of any whose body mentions 'invoice'.\"")
    lines.append("")
    lines.append("*Bash path — 1 tool call:*")
    lines.append("")
    lines.append("```bash")
    lines.append("graph-cli mail list --top 50 --folder Inbox --timezone \"Asia/Karachi\" \\")
    lines.append("  | jq '[.[] | select(.bodyPreview|test(\"invoice\";\"i\")) | {from: .from.emailAddress.address, subject}]'")
    lines.append("```")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    lines.append("    A([Agent]) -->|1 Bash call| B[graph-cli mail list]")
    lines.append("    B -->|50 records, stdout| C[jq filter + project]")
    lines.append("    C -.->|~3 small records<br/>into context| A")
    lines.append("    A --> R[Final response]")
    lines.append("```")
    lines.append("")
    lines.append("What enters context: only the filtered and projected records. If 3 of 50 emails match, the model sees ~3 small JSON objects.")
    lines.append("")
    lines.append("*MCP path — 1 tool call, filter and project in-model:*")
    lines.append("")
    lines.append("```")
    lines.append("# Step 1 — tool call (returns all 50 records into context)")
    lines.append("mcp__graph-cli__mail_list(")
    lines.append("    top: 50,")
    lines.append("    folder: \"Inbox\",")
    lines.append("    timezone: \"Asia/Karachi\"")
    lines.append(")")
    lines.append("")
    lines.append("# Step 2 — model-side (no tool call, but all 50 records consumed as input tokens)")
    lines.append("# iterate records, keep where bodyPreview matches /invoice/i")
    lines.append("")
    lines.append("# Step 3 — model-side projection, emitted in the final assistant message")
    lines.append("# for each match: { from: from.emailAddress.address, subject: subject }")
    lines.append("```")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    lines.append("    A([Agent]) -->|1 MCP call| B[mcp__graph-cli__mail_list]")
    lines.append("    B -.->|50 full records<br/>into context| A")
    lines.append("    A -->|filter in-model| F[3 matching records]")
    lines.append("    F -->|project in-model| P[3 from+subject pairs]")
    lines.append("    P --> R[Final response]")
    lines.append("```")
    lines.append("")
    lines.append("What enters context: all 50 full email records. The model performs the filter and projection itself — those steps cost input tokens (re-reading the 50 records) and output tokens (writing the filtered list) rather than additional tool calls.")
    lines.append("")
    lines.append("If the required filter field is not present in the `mail_list` response (e.g., matching on full message body rather than `bodyPreview`), the MCP path must fall back to a list-then-get-per-id pattern:")
    lines.append("")
    lines.append("```")
    lines.append("mcp__graph-cli__mail_list(top: 50, folder: \"Inbox\", ...)")
    lines.append("mcp__graph-cli__mail_get(id: \"<id-1>\")")
    lines.append("mcp__graph-cli__mail_get(id: \"<id-2>\")")
    lines.append("...  # up to one per message")
    lines.append("```")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    lines.append("    A([Agent]) -->|call 1| B[mcp__graph-cli__mail_list]")
    lines.append("    B -.->|50 IDs + metadata| A")
    lines.append("    A -->|calls 2..N+1| G[mcp__graph-cli__mail_get<br/>one per id]")
    lines.append("    G -.->|full body each| A")
    lines.append("    A -->|filter + project in-model| R[Final response]")
    lines.append("```")
    lines.append("")
    lines.append("The Bash pipeline absorbs this case without extra round-trips because the shell can filter the full list in-process before returning to the model.")
    lines.append("")
    lines.append("Order-of-magnitude comparison on the example above (with 50 emails × ~500 tokens per full record ≈ 25,000 tokens of tool output versus ~200 tokens of `jq`-filtered output): the Bash path can avoid ~24KB of input tokens reaching the model on that turn, at the cost of constructing a `jq` expression in the Bash command.")
    lines.append("")
    lines.append("Implications:")
    lines.append("")
    lines.append("- For workloads dominated by *filter/project/aggregate over list responses*, the Bash surface can be materially cheaper than MCP because the model never sees the filtered-out records.")
    lines.append("- For workloads dominated by *single-resource lookups and structured action calls*, the surfaces are roughly equivalent on data volume, and MCP's per-turn latency advantages apply.")
    lines.append("- None of the benchmark tasks use `jq`-style filtering; results in the tables above do not reflect this advantage.")
    lines.append("")
    lines.append("## Full data (medians)")
    lines.append("")
    lines.append("| Task | Mode | Cost | Dur (s) | Turns | In tok | Out tok | Cache create | Cache read |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for task in TASK_ORDER:
        for mode in modes_present:
            if (task, mode) not in per_task.index:
                continue
            r = per_task.loc[(task, mode)]
            lines.append(
                f"| {task} | {mode} | ${r['cost']:.4f} | {r['dur']:.1f} | {r['turns']:.0f} | "
                f"{r['in_tok']:.0f} | {r['out_tok']:.0f} | {r['cache_c']:.0f} | {r['cache_r']:.0f} |"
            )
    lines.append("")
    lines.append("## Observations")
    lines.append("")
    cheapest = min(modes_present, key=lambda m: cost[m])
    fastest = min(modes_present, key=lambda m: dur[m])
    fewest_turns = min(modes_present, key=lambda m: turns[m])
    lines.append(f"- **Lowest total cost:** {MODE_LABELS[cheapest]} (${cost[cheapest]:.2f}).")
    lines.append(f"- **Lowest total wall time:** {MODE_LABELS[fastest]} ({dur[fastest]:.0f}s).")
    lines.append(f"- **Fewest total turns:** {MODE_LABELS[fewest_turns]} ({turns[fewest_turns]:.0f}).")
    if has_guided:
        delta_pct = (cost['cli_guided'] - cost['cli']) / cost['cli'] * 100
        lines.append(f"- Adding task-to-command hints reduced CLI total cost by {-delta_pct:.0f}% (${cost['cli']:.2f} → ${cost['cli_guided']:.2f}) and CLI total turns by {(turns['cli']-turns['cli_guided'])/turns['cli']*100:.0f}%.")
    if "mcp_guided" in modes_present:
        mcp_delta_pct = (cost['mcp_guided'] - cost['mcp']) / cost['mcp'] * 100
        lines.append(f"- Adding task-to-tool hints reduced MCP total cost by {-mcp_delta_pct:.0f}% (${cost['mcp']:.2f} → ${cost['mcp_guided']:.2f}) and MCP total turns by {(turns['mcp']-turns['mcp_guided'])/turns['mcp']*100:.0f}%.")
    lines.append("- Per-turn cost is lower for CLI modes than MCP modes in these runs. Per-turn duration is lower for MCP modes.")
    lines.append("- Output token totals are smaller for MCP modes than CLI modes. Contributing factor observed in transcripts: Bash-mode final responses more often restate JSON fields from tool output.")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append(f"- N = {n_runs} per (task, mode) cell. Variance is not fully captured; larger N would tighten the estimates.")
    lines.append("- Claude API latency varies across time of day and API traffic. Comparing runs across different days or hours introduces noise unrelated to the surface under test.")
    lines.append("- All runs used the same model. Results with smaller or larger models may differ in both shape and magnitude.")
    if has_guided:
        lines.append("- The hinted-mode system prompts used here are condensed cookbooks (~20-30 lines). Production workflow documentation is typically longer and more task-specific.")
    lines.append("- Only read-only tasks were benchmarked. Write operations involve confirmation loops that may shift the profile.")
    lines.append("- Results reflect the volume of data present in the test mailbox/calendar at run time. Sparse inboxes produce smaller responses than busy ones.")
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results.csv")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    df = df[df["error"].isna() if "error" in df.columns else slice(None)]

    out_dir = Path(args.out_dir)
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    bar_compare(df, "total_cost_usd", "Cost per task (median of 3 runs)", "USD", charts_dir / "cost_per_task.png", fmt="${:.3f}")
    bar_compare(df, "duration_s", "Wall-clock duration per task (median of 3 runs)", "seconds", charts_dir / "duration_per_task.png", fmt="{:.1f}s")
    bar_compare(df, "num_turns", "Turn count per task (median of 3 runs)", "turns", charts_dir / "turns_per_task.png", fmt="{:.0f}")
    stacked_tokens(df, charts_dir / "tokens_stacked.png")
    totals_summary(df, charts_dir / "totals.png")

    build_report(df, out_dir / "REPORT.md", chart_dir="charts")
    print(f"wrote {out_dir / 'REPORT.md'} + 5 charts in {charts_dir}/")


if __name__ == "__main__":
    main()
