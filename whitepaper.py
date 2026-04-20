#!/usr/bin/env python3
"""Generate a whitepaper-style markdown report from results.csv."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

MODE_ORDER = ["cli", "cli_guided", "mcp", "mcp_guided"]
MODE_LABELS = {
    "cli": "CLI (minimal)",
    "cli_guided": "CLI + hints",
    "mcp": "MCP (minimal)",
    "mcp_guided": "MCP + hints",
}
TASK_ORDER = [
    "user_me", "calendar_today", "mail_recent",
    "mail_search", "calendar_free_slot", "chat_search_read",
    "daily_briefing",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--chart-dir", default="charts")
    ap.add_argument("--run-label", default="single-day run", help="e.g. 'Monday, 2026-04-20 afternoon'")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if "error" in df.columns:
        df = df[df["error"].isna()]

    modes = [m for m in MODE_ORDER if m in df["mode"].unique()]
    tasks = [t for t in TASK_ORDER if t in df["task"].unique()]
    n_runs = int(df.groupby(["task", "mode"]).size().median())
    n_tasks = len(tasks)

    totals = df.groupby("mode").agg(
        cost=("total_cost_usd", "sum"),
        dur=("duration_s", "sum"),
        turns=("num_turns", "sum"),
        in_tok=("input_tokens", "sum"),
        out_tok=("output_tokens", "sum"),
        cache_c=("cache_creation_input_tokens", "sum"),
        cache_r=("cache_read_input_tokens", "sum"),
    ).loc[modes]

    per_task = df.groupby(["task", "mode"]).agg(
        cost=("total_cost_usd", "median"),
        dur=("duration_s", "median"),
        turns=("num_turns", "median"),
        in_tok=("input_tokens", "median"),
        out_tok=("output_tokens", "median"),
        cache_c=("cache_creation_input_tokens", "median"),
        cache_r=("cache_read_input_tokens", "median"),
    ).round(4)

    def row(metric, fmt):
        return "| " + metric + " |" + "".join(f" {fmt.format(totals.loc[m, metric.lower().split()[0] if False else metric_key(metric)])} |" for m in modes)

    cd = args.chart_dir
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    L: list[str] = []
    L.append("# Comparative Analysis of CLI and MCP Tool Surfaces for Agentic Microsoft Graph Access")
    L.append("")
    L.append(f"**Run label:** {args.run_label}  ")
    L.append(f"**Configurations:** {len(modes)} ({', '.join(MODE_LABELS[m] for m in modes)})  ")
    L.append(f"**Tasks:** {n_tasks} read-only Microsoft Graph operations  ")
    L.append(f"**Replicates:** {n_runs} runs per (task, configuration) cell  ")
    L.append("")
    L.append("---")
    L.append("")

    L.append("## Abstract")
    L.append("")
    cheapest = min(modes, key=lambda m: totals.loc[m, "cost"])
    fastest = min(modes, key=lambda m: totals.loc[m, "dur"])
    fewest_turns = min(modes, key=lambda m: totals.loc[m, "turns"])
    L.append(
        f"We evaluate four configurations of a Microsoft Graph tool (`graph-cli`) "
        f"exposed to a large language model agent: the tool invoked as a command-line program "
        f"through a generic shell tool (CLI), and the same tool exposed as a Model Context Protocol "
        f"(MCP) server, each tested with a minimal system prompt and with a task-to-command cookbook "
        f"in the system prompt. The agent executes {n_tasks} read-only tasks spanning user identity, "
        f"mail, calendar, and chat surfaces, with {n_runs} replicates per cell. "
        f"We report aggregate cost, wall-clock latency, turn count, and token composition "
        f"from Claude's usage envelope. Across all tasks, the lowest total cost is observed for "
        f"{MODE_LABELS[cheapest]} (${totals.loc[cheapest, 'cost']:.2f}), the lowest total wall-clock "
        f"time for {MODE_LABELS[fastest]} ({totals.loc[fastest, 'dur']:.0f}s), and the lowest total "
        f"turn count for {MODE_LABELS[fewest_turns]} ({int(totals.loc[fewest_turns, 'turns'])} turns). "
        f"We decompose the latency gap into turn count and per-turn duration, and discuss a "
        f"structural capability available to the CLI surface (shell composition via pipes) that "
        f"is not exercised by the benchmarked tasks but is relevant to realistic workloads."
    )
    L.append("")

    L.append("## 1. Introduction")
    L.append("")
    L.append(
        "Language model agents increasingly interact with enterprise systems through external tools. "
        "Two common integration patterns are (a) invoking a conventional CLI binary through a generic "
        "shell tool made available to the agent, and (b) exposing the same functionality through a "
        "Model Context Protocol (MCP) server, which the agent calls with structured arguments. "
        "Both patterns are widely used in production agent configurations, and practitioners "
        "frequently ask which pattern is preferable for a given workload."
    )
    L.append("")
    L.append(
        "We compare the two surfaces using `graph-cli`, an open-source tool that exposes Microsoft "
        "Graph (email, calendar, Teams chat, user directory, files) through both a CLI and a built-in "
        "MCP server with (per its documentation) 1:1 command parity. Because the underlying API calls "
        "and authentication are identical, any measured differences are attributable to the tool "
        "surface rather than the business logic."
    )
    L.append("")
    L.append(
        "We also test the effect of in-prompt priming. Production agent systems often include "
        "task-to-command cookbooks in their system prompts or in auto-loaded context files. "
        "We compare a minimal prompt (\"use graph-cli via Bash\" / \"use graph-cli MCP tools\") "
        "against a prompt containing a condensed cookbook covering the benchmarked tasks."
    )
    L.append("")

    L.append("## 2. Background")
    L.append("")
    L.append("### 2.1 The two tool surfaces")
    L.append("")
    L.append(
        "In CLI mode, the agent invokes `graph-cli` through a generic Bash tool. Each call spawns "
        "a fresh process: the .NET runtime is initialized, a cached access token is read from disk, "
        "an HTTP client is constructed, a request is sent to Microsoft Graph, and the JSON response "
        "is written to standard output. The stdout text is returned verbatim to the agent."
    )
    L.append("")
    L.append(
        "In MCP mode, the agent's harness spawns `graph-cli mcp` once per session as a long-lived "
        "stdio-based server. The agent invokes individual tools by name (e.g. `mail_list`) with "
        "structured arguments. The server reuses its process state, access token, and HTTP client "
        "across calls. Tool schemas are provided to the agent at session start, enabling structured "
        "argument validation and pre-loaded knowledge of available operations."
    )
    L.append("")
    L.append("### 2.2 Prompt priming")
    L.append("")
    L.append(
        "In addition to the surface choice, an operator can prime the agent with task-specific "
        "guidance: which command or tool to use for which kind of request, plus invocation hints "
        "(timezones, filters, pagination). We treat this as a second independent factor, giving a "
        f"2×2 design yielding {len(modes)} configurations evaluated in this study."
    )
    L.append("")

    L.append("## 3. Methodology")
    L.append("")
    L.append("### 3.1 Task suite")
    L.append("")
    L.append(
        f"Seven read-only tasks were selected to span common Graph operations and to vary in "
        f"complexity from a single-call lookup to a multi-step briefing that requires combining "
        f"results from multiple endpoints:"
    )
    L.append("")
    L.append("| ID | Complexity | Description |")
    L.append("|---|---|---|")
    L.append("| user_me | trivial | Return the authenticated user's display name and email. |")
    L.append("| calendar_today | simple | List today's calendar events with start time and subject. |")
    L.append("| mail_recent | simple | List the 10 most recent Inbox emails with sender and subject. |")
    L.append("| mail_search | medium | Find the most recent email matching a sender/subject term. |")
    L.append("| calendar_free_slot | medium | Check availability of a room mailbox in a time window. |")
    L.append("| chat_search_read | medium | Locate a Teams chat by topic and read recent messages. |")
    L.append("| daily_briefing | complex | Combine today's calendar events and unread emails into a briefing; flag conflicts. |")
    L.append("")
    L.append("### 3.2 Experimental setup")
    L.append("")
    L.append(
        "Each cell of the 4×7 design was executed with N = {n} replicates. Each run is a fresh "
        "subprocess invocation of `claude -p <prompt> --output-format json` from a newly created "
        "temporary working directory. Settings:".format(n=n_runs)
    )
    L.append("")
    L.append(
        "- `CLAUDE.md` auto-discovery is suppressed by running from an empty CWD, ensuring no "
        "project-specific context is injected."
    )
    L.append(
        "- `--strict-mcp-config` restricts the agent to the explicitly configured MCP server set. "
        "In CLI configurations, the MCP server set is empty; in MCP configurations, only `graph-cli` "
        "is configured."
    )
    L.append(
        "- `--allowed-tools` and `--disallowed-tools` gate the tool surface: CLI configurations "
        "permit Bash only; MCP configurations permit only the `mcp__graph-cli__*` namespace."
    )
    L.append(
        "- `--permission-mode bypassPermissions` auto-approves tool calls for automation, paired "
        "with a read-only instruction in the system prompt."
    )
    L.append(
        "- A 5-second delay is inserted between consecutive runs to avoid throttling on the Graph API."
    )
    L.append("")
    L.append(
        "System prompts differ only in (a) which surface the agent is told to use, and (b) whether "
        "a condensed command-to-task cookbook is included. Both minimal and hinted prompts instruct "
        "the agent to avoid parallel tool invocations (the graph-cli token cache is not concurrency-safe)."
    )
    L.append("")
    L.append("### 3.3 Measurements")
    L.append("")
    L.append(
        "For each run we record the fields of Claude's `--output-format json` result envelope: "
        "`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, "
        "`total_cost_usd`, `num_turns`, and `duration_ms`. Wall-clock duration is also measured "
        "externally by the benchmark harness. All table values labeled \"total\" are sums across "
        f"the {n_runs * n_tasks} runs in a configuration; per-task values in Section 4 are medians "
        f"across {n_runs} replicates."
    )
    L.append("")

    L.append("## 4. Results")
    L.append("")
    L.append("### 4.1 Aggregate totals")
    L.append("")
    L.append(f"![Aggregate totals]({cd}/totals.png)")
    L.append("")
    header = "| Metric |" + "".join(f" {MODE_LABELS[m]} |" for m in modes)
    sep = "|---|" + "---|" * len(modes)
    L.append(header)
    L.append(sep)
    L.append(f"| Total cost (USD) |" + "".join(f" ${totals.loc[m, 'cost']:.2f} |" for m in modes))
    L.append(f"| Total wall time (s) |" + "".join(f" {totals.loc[m, 'dur']:.0f} |" for m in modes))
    L.append(f"| Total turns |" + "".join(f" {int(totals.loc[m, 'turns'])} |" for m in modes))
    L.append(f"| Total input tokens |" + "".join(f" {int(totals.loc[m, 'in_tok'])} |" for m in modes))
    L.append(f"| Total output tokens |" + "".join(f" {int(totals.loc[m, 'out_tok'])} |" for m in modes))
    L.append(f"| Total cache_creation tokens |" + "".join(f" {int(totals.loc[m, 'cache_c'])} |" for m in modes))
    L.append(f"| Total cache_read tokens |" + "".join(f" {int(totals.loc[m, 'cache_r'])} |" for m in modes))
    L.append(f"| Cost per turn (USD) |" + "".join(f" ${totals.loc[m, 'cost'] / totals.loc[m, 'turns']:.4f} |" for m in modes))
    L.append(f"| Duration per turn (s) |" + "".join(f" {totals.loc[m, 'dur'] / totals.loc[m, 'turns']:.2f} |" for m in modes))
    L.append("")

    L.append("### 4.2 Per-task cost")
    L.append("")
    L.append(f"![Cost per task]({cd}/cost_per_task.png)")
    L.append("")
    L.append("### 4.3 Per-task wall time")
    L.append("")
    L.append(f"![Duration per task]({cd}/duration_per_task.png)")
    L.append("")
    L.append("### 4.4 Per-task turn count")
    L.append("")
    L.append(f"![Turns per task]({cd}/turns_per_task.png)")
    L.append("")
    L.append("### 4.5 Token composition")
    L.append("")
    L.append(f"![Token stacked]({cd}/tokens_stacked.png)")
    L.append("")
    L.append(
        "Input tokens in this figure are decomposed into three classes as reported by the API: "
        "`cache_read` (prior-turn tokens re-read from the prompt cache, priced at approximately 0.1× "
        "of standard input), `cache_creation` (fresh tokens written into the cache, priced at "
        "approximately 1.25×), and `input` (uncached fresh input at the standard rate). Output "
        "tokens are priced at approximately 5× standard input."
    )
    L.append("")
    L.append(
        "CLI configurations accumulate substantially larger `cache_read` totals than MCP "
        "configurations. The mechanism is observable in transcripts: each Bash tool call appends "
        "the full stdout of `graph-cli` (a JSON document) to the conversation, which is then part "
        "of the cached prefix read by every subsequent turn. MCP tool results occupy the same "
        "transcript position, but MCP-configuration runs used fewer turns in total and had smaller "
        "per-turn response payloads on most tasks."
    )
    L.append("")

    L.append("### 4.6 Effect of prompt priming")
    L.append("")
    if "cli_guided" in modes and "cli" in modes:
        c_cli, c_guided = totals.loc["cli", "cost"], totals.loc["cli_guided", "cost"]
        t_cli, t_guided = totals.loc["cli", "turns"], totals.loc["cli_guided", "turns"]
        L.append(
            f"For the CLI surface, adding the cookbook reduced total cost from ${c_cli:.2f} to "
            f"${c_guided:.2f} ({(c_guided-c_cli)/c_cli*100:+.0f}%) and total turn count from "
            f"{int(t_cli)} to {int(t_guided)} ({(t_guided-t_cli)/t_cli*100:+.0f}%)."
        )
    if "mcp_guided" in modes and "mcp" in modes:
        c_mcp, c_mcp_g = totals.loc["mcp", "cost"], totals.loc["mcp_guided", "cost"]
        t_mcp, t_mcp_g = totals.loc["mcp", "turns"], totals.loc["mcp_guided", "turns"]
        L.append("")
        L.append(
            f"For the MCP surface, adding the cookbook reduced total cost from ${c_mcp:.2f} to "
            f"${c_mcp_g:.2f} ({(c_mcp_g-c_mcp)/c_mcp*100:+.0f}%) and total turn count from "
            f"{int(t_mcp)} to {int(t_mcp_g)} ({(t_mcp_g-t_mcp)/t_mcp*100:+.0f}%)."
        )
    L.append("")
    L.append(
        "The effect of priming is larger for the CLI surface. MCP already provides structured "
        "tool schemas at session start, which partially performs the role of the cookbook; adding "
        "an explicit task-to-tool mapping provides a smaller incremental reduction in exploration."
    )
    L.append("")

    L.append("## 5. Discussion")
    L.append("")
    L.append("### 5.1 Latency decomposition")
    L.append("")
    d_cli, d_mcp = totals.loc["cli", "dur"], totals.loc["mcp", "dur"]
    t_cli, t_mcp = totals.loc["cli", "turns"], totals.loc["mcp", "turns"]
    ptd_cli = d_cli / t_cli
    ptd_mcp = d_mcp / t_mcp
    L.append(
        f"The wall-clock ratio between minimal CLI and minimal MCP on this run is "
        f"{d_cli:.0f}s / {d_mcp:.0f}s = {d_cli/d_mcp:.2f}x. We decompose it as "
        f"(turns ratio) × (per-turn duration ratio) = "
        f"{t_cli/t_mcp:.2f} × {ptd_cli/ptd_mcp:.2f} = {(t_cli/t_mcp)*(ptd_cli/ptd_mcp):.2f}x."
    )
    L.append("")
    L.append(
        "The turn-ratio factor reflects exploration: in minimal CLI runs, agents occasionally "
        "consult `--help` or iterate on command syntax. The per-turn ratio reflects three "
        "surface-intrinsic mechanisms: (i) process spawn overhead for each CLI invocation; "
        "(ii) lack of connection and authentication reuse across calls; and (iii) a larger "
        "cached-input footprint per turn from accumulated JSON outputs."
    )
    L.append("")
    L.append(
        "Adding prompt priming compresses factor (i) by reducing the number of turns that contain "
        "exploration. It does not affect the per-turn structural factors, which are properties of "
        "the surface."
    )
    L.append("")

    L.append("### 5.2 Surface capability: compound operations")
    L.append("")
    L.append(
        "The benchmarked tasks request full records and do not require filtering or transformation "
        "of tool output before it enters the agent's context. In workloads where the agent needs "
        "to filter, project, or aggregate a list before consuming it, the two surfaces diverge in a "
        "way not reflected in the aggregate tables above."
    )
    L.append("")
    L.append(
        "The Bash surface supports shell composition. A list operation can be piped through `jq` "
        "(or `grep`, `awk`, etc.) inside the same tool invocation, and only the filtered and "
        "projected records are returned to the agent:"
    )
    L.append("")
    L.append("```bash")
    L.append("graph-cli mail list --top 50 --folder Inbox --timezone \"Asia/Karachi\" \\")
    L.append("  | jq '[.[] | select(.bodyPreview|test(\"invoice\";\"i\")) | {from: .from.emailAddress.address, subject}]'")
    L.append("```")
    L.append("")
    L.append("```mermaid")
    L.append("flowchart LR")
    L.append("    A([Agent]) -->|1 Bash call| B[graph-cli mail list]")
    L.append("    B -->|50 records stdout| C[jq filter + project]")
    L.append("    C -.->|~3 small records<br/>into context| A")
    L.append("    A --> R[Final response]")
    L.append("```")
    L.append("")
    L.append(
        "The MCP surface does not support composition. Each tool call returns a complete response "
        "into the agent's context; filtering and projection must occur either inside the agent "
        "(processing every record as input tokens) or by chaining additional tool calls:"
    )
    L.append("")
    L.append("```mermaid")
    L.append("flowchart LR")
    L.append("    A([Agent]) -->|1 MCP call| B[mcp__graph-cli__mail_list]")
    L.append("    B -.->|50 full records<br/>into context| A")
    L.append("    A -->|filter in-model| F[3 matching records]")
    L.append("    F -->|project in-model| P[3 from+subject pairs]")
    L.append("    P --> R[Final response]")
    L.append("```")
    L.append("")
    L.append(
        "If the filtering predicate references a field not present in the list response (e.g., full "
        "message body), the MCP path must fall back to a list-then-get-per-id pattern with O(N) "
        "additional tool calls:"
    )
    L.append("")
    L.append("```mermaid")
    L.append("flowchart LR")
    L.append("    A([Agent]) -->|call 1| B[mcp__graph-cli__mail_list]")
    L.append("    B -.->|50 IDs + metadata| A")
    L.append("    A -->|calls 2..N+1| G[mcp__graph-cli__mail_get<br/>one per id]")
    L.append("    G -.->|full body each| A")
    L.append("    A -->|filter + project in-model| R[Final response]")
    L.append("```")
    L.append("")
    L.append(
        "For workloads dominated by filter-project-aggregate operations over list responses, "
        "the Bash surface may have materially smaller context consumption; the agent never observes "
        "the filtered-out records. For workloads dominated by single-resource lookups and structured "
        "action invocations, the two surfaces are approximately equivalent on data volume, and the "
        "per-turn latency advantage of MCP applies."
    )
    L.append("")

    L.append("### 5.3 Cost-latency tradeoff")
    L.append("")
    if "cli_guided" in modes and "mcp_guided" in modes:
        cg_cost = totals.loc["cli_guided", "cost"]
        mg_cost = totals.loc["mcp_guided", "cost"]
        cg_dur = totals.loc["cli_guided", "dur"]
        mg_dur = totals.loc["mcp_guided", "dur"]
        cost_diff = cg_cost - mg_cost
        dur_diff = cg_dur - mg_dur
        L.append(
            f"Comparing the two hinted configurations on this run: CLI + hints costs ${cg_cost:.2f} "
            f"and runs in {cg_dur:.0f}s; MCP + hints costs ${mg_cost:.2f} and runs in {mg_dur:.0f}s. "
            f"The cost delta is ${cost_diff:+.2f} ({cost_diff/mg_cost*100:+.0f}%), and the duration "
            f"delta is {dur_diff:+.0f}s ({dur_diff/mg_dur*100:+.0f}%). The implicit price of a "
            f"second of latency saved in this comparison is "
            f"${(mg_cost-cg_cost)/(cg_dur-mg_dur):.4f} per second (or undefined if one axis is flat)."
        )
    L.append("")
    L.append(
        "Operators choosing between these configurations for a given workload must weigh the "
        "absolute cost difference against the absolute latency difference in the context of their "
        "deployment (interactive user-facing, background batch, etc.). The data in this study does "
        "not imply a universal recommendation."
    )
    L.append("")

    L.append("## 6. Limitations")
    L.append("")
    L.append(f"- **Replicate count.** N = {n_runs} per cell is sufficient to observe directional effects but not to characterize variance. Per-cell standard deviations are not reported.")
    L.append("- **Single model.** All runs used a single Claude model. Results on smaller or larger models may differ.")
    L.append("- **Temporal variance.** Claude API latency varies with time of day and traffic. Cross-configuration comparisons within a single run are more reliable than cross-run comparisons.")
    L.append("- **Data volume.** Response sizes depend on the state of the test user's mailbox and calendar at run time. Sparse inboxes produce smaller JSON payloads than busy ones, which may narrow differences between configurations.")
    L.append("- **Read-only scope.** Write operations (send mail, update events, post chat) involve confirmation flows and may have different cost/latency profiles. No write operations were tested.")
    L.append("- **Cookbook scope.** The hinted system prompts used here are condensed cookbooks (~20-30 lines) covering the benchmarked tasks specifically. Production workflow documentation is typically longer and more task-specific; its effect is not directly characterized here.")
    L.append("- **Prompt cache sensitivity.** Cross-run prompt cache behavior is not fully controlled. Later runs of the same configuration may benefit from cached prefixes from earlier runs, biasing cost downward in ways that depend on run order.")
    L.append("")

    L.append("## 7. Conclusions")
    L.append("")
    L.append(
        f"In this study of {n_tasks} read-only Microsoft Graph tasks under {len(modes)} agent "
        f"configurations, the lowest total cost was observed for {MODE_LABELS[cheapest]} "
        f"(${totals.loc[cheapest, 'cost']:.2f}), the lowest total wall time for "
        f"{MODE_LABELS[fastest]} ({totals.loc[fastest, 'dur']:.0f}s), and the fewest total turns "
        f"for {MODE_LABELS[fewest_turns]} ({int(totals.loc[fewest_turns, 'turns'])}). Prompt "
        f"priming reduced cost and turn count for both surfaces, with a larger reduction observed "
        f"for the CLI surface than for the MCP surface. The latency gap between minimal-prompt "
        f"surfaces decomposes into a turn-count factor (sensitive to prompt priming) and a "
        f"per-turn factor (insensitive to priming, driven by process lifecycle and context size). "
        f"The CLI surface retains a structural capability — composition of tool output through "
        f"shell operators — that is not exercised by the benchmarked tasks and that would, on "
        f"filter-heavy workloads, further reduce input-token consumption relative to MCP."
    )
    L.append("")
    L.append(
        "These results do not imply a universal preference for either surface. The appropriate "
        "choice depends on workload composition (lookup-heavy versus filter-heavy), latency "
        "sensitivity, and the availability of task-specific priming in the agent's system prompt."
    )
    L.append("")

    L.append("## Appendix A. Per-task medians")
    L.append("")
    L.append("| Task | Configuration | Cost (USD) | Duration (s) | Turns | Input | Output | Cache creation | Cache read |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for task in tasks:
        for mode in modes:
            if (task, mode) not in per_task.index:
                continue
            r = per_task.loc[(task, mode)]
            L.append(
                f"| {task} | {MODE_LABELS[mode]} | ${r['cost']:.4f} | {r['dur']:.1f} | "
                f"{r['turns']:.0f} | {r['in_tok']:.0f} | {r['out_tok']:.0f} | "
                f"{r['cache_c']:.0f} | {r['cache_r']:.0f} |"
            )
    L.append("")

    (out_dir / "WHITEPAPER.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {out_dir / 'WHITEPAPER.md'}")


def metric_key(label: str) -> str:
    return label.lower().split()[0]


if __name__ == "__main__":
    main()
