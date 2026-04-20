# graph-cli: CLI vs MCP Token Usage Benchmark

Measurement of `graph-cli` invoked in up to four configurations on the same read-only tasks:

- **CLI (minimal)** — `graph-cli` via the Bash tool, minimal system prompt ("use graph-cli via Bash").
- **CLI + hints** — Bash surface, system prompt includes a cookbook of common read-only commands for the benchmarked tasks.
- **MCP (minimal)** — `graph-cli mcp` stdio MCP server, tools prefixed `mcp__graph-cli__`, minimal system prompt.
- **MCP + hints** — same MCP surface, system prompt includes a task-to-tool cookbook.

Same underlying tool and Microsoft Graph APIs — only the tool surface and priming differ.

## Totals

| Metric | CLI (minimal) | CLI + hints | MCP (minimal) | MCP + hints |
|---|---|---|---|---|
| Total cost (21 runs each) | $3.15 | $2.22 | $3.78 | $3.64 |
| Total wall time | 836s | 556s | 337s | 307s |
| Total turns | 108 | 57 | 78 | 69 |
| Total output tokens | 12922 | 7375 | 9451 | 8525 |
| Cost per turn | $0.0291 | $0.0390 | $0.0484 | $0.0528 |
| Duration per turn | 7.7s | 9.8s | 4.3s | 4.5s |

![Aggregate totals](charts/totals.png)

## Methodology

- **7 read-only tasks** across graph-cli surfaces (user, mail, calendar, chat).
- **3 runs** per task/mode. Totals in tables are summed across runs; per-task values are medians.
- Each run is a fresh `claude -p` process from a clean temp CWD. `CLAUDE.md` auto-discovery is disabled. System prompt differs only in the surface the agent is told to use (Bash vs MCP) and whether a task-to-command cookbook is included.
- `--strict-mcp-config` restricts available MCP servers to the configured `graph-cli` server. `--allowed-tools` / `--disallowed-tools` restrict the tool surface to the mode under test.
- Token, cost, and duration figures are taken from Claude's `--output-format json` usage envelope.
- 5-second delay between runs to avoid Graph API throttling.

## Per-task cost comparison

![Cost per task](charts/cost_per_task.png)

## Per-task latency

![Duration per task](charts/duration_per_task.png)

## Per-task turn count

![Turns per task](charts/turns_per_task.png)

## Token composition

![Token stacked](charts/tokens_stacked.png)

Observed: CLI modes accumulate larger `cache_read` token volumes than MCP modes. Each Bash call returns raw JSON into the transcript, which becomes part of the cached prefix and is read again on each subsequent turn. MCP's tool schema catalog contributes to higher one-time `cache_creation` tokens, but the per-turn input size is smaller in these runs.

## Latency decomposition

Total wall time — bare CLI: **836s**, MCP: **337s** (ratio 2.48x). The gap decomposes into two factors:

1. **Turn count.** Bare CLI used 108 turns; MCP used 78 (+38%). Each additional turn is an additional model round-trip. Transcript inspection shows Bash-mode turns include occasional `--help` exploration and command re-reads; MCP-mode turns invoke tools directly from pre-loaded schemas.
2. **Per-turn duration.** Bare CLI: 7.7s/turn; MCP: 4.3s/turn (ratio 1.79x). Contributing factors observed in the data and implementation:
    - **Process spawn per call.** Each Bash invocation starts a new `graph-cli.exe` process, including .NET runtime initialization, token-cache read, and HTTP client construction. The MCP server is spawned once per session; subsequent tool calls reuse the process, auth context, and HTTP connections.
    - **Input tokens per turn.** CLI turns carry more cached context (JSON output of prior calls), which increases the tokens the model processes on each turn.
    - **Output tokens per turn.** CLI responses in the final assistant message tend to include or restate more JSON content than MCP responses.

Adding task-to-command hints to the system prompt reduces turn count for both surfaces but does not affect per-turn process-spawn overhead or context size, which are properties of the surface itself.

## Surface-specific capabilities: compound operations

The Bash surface supports shell composition — piping output between programs, filtering with `jq`, selecting fields, aggregating. The MCP surface does not; each tool call returns a full response into the model's context, and any filter/transform logic runs in the model itself.

This difference is not exercised by the benchmarked tasks above (they request full records) but it is relevant for realistic workflows. A worked example:

**Task:** "From my last 50 Inbox emails, list just the sender and subject of any whose body mentions 'invoice'."

*Bash path — 1 tool call:*

```bash
graph-cli mail list --top 50 --folder Inbox --timezone "Asia/Karachi" \
  | jq '[.[] | select(.bodyPreview|test("invoice";"i")) | {from: .from.emailAddress.address, subject}]'
```

```mermaid
flowchart LR
    A([Agent]) -->|1 Bash call| B[graph-cli mail list]
    B -->|50 records, stdout| C[jq filter + project]
    C -.->|~3 small records<br/>into context| A
    A --> R[Final response]
```

What enters context: only the filtered and projected records. If 3 of 50 emails match, the model sees ~3 small JSON objects.

*MCP path — 1 tool call, filter and project in-model:*

```
# Step 1 — tool call (returns all 50 records into context)
mcp__graph-cli__mail_list(
    top: 50,
    folder: "Inbox",
    timezone: "Asia/Karachi"
)

# Step 2 — model-side (no tool call, but all 50 records consumed as input tokens)
# iterate records, keep where bodyPreview matches /invoice/i

# Step 3 — model-side projection, emitted in the final assistant message
# for each match: { from: from.emailAddress.address, subject: subject }
```

```mermaid
flowchart LR
    A([Agent]) -->|1 MCP call| B[mcp__graph-cli__mail_list]
    B -.->|50 full records<br/>into context| A
    A -->|filter in-model| F[3 matching records]
    F -->|project in-model| P[3 from+subject pairs]
    P --> R[Final response]
```

What enters context: all 50 full email records. The model performs the filter and projection itself — those steps cost input tokens (re-reading the 50 records) and output tokens (writing the filtered list) rather than additional tool calls.

If the required filter field is not present in the `mail_list` response (e.g., matching on full message body rather than `bodyPreview`), the MCP path must fall back to a list-then-get-per-id pattern:

```
mcp__graph-cli__mail_list(top: 50, folder: "Inbox", ...)
mcp__graph-cli__mail_get(id: "<id-1>")
mcp__graph-cli__mail_get(id: "<id-2>")
...  # up to one per message
```

```mermaid
flowchart LR
    A([Agent]) -->|call 1| B[mcp__graph-cli__mail_list]
    B -.->|50 IDs + metadata| A
    A -->|calls 2..N+1| G[mcp__graph-cli__mail_get<br/>one per id]
    G -.->|full body each| A
    A -->|filter + project in-model| R[Final response]
```

The Bash pipeline absorbs this case without extra round-trips because the shell can filter the full list in-process before returning to the model.

Order-of-magnitude comparison on the example above (with 50 emails × ~500 tokens per full record ≈ 25,000 tokens of tool output versus ~200 tokens of `jq`-filtered output): the Bash path can avoid ~24KB of input tokens reaching the model on that turn, at the cost of constructing a `jq` expression in the Bash command.

Implications:

- For workloads dominated by *filter/project/aggregate over list responses*, the Bash surface can be materially cheaper than MCP because the model never sees the filtered-out records.
- For workloads dominated by *single-resource lookups and structured action calls*, the surfaces are roughly equivalent on data volume, and MCP's per-turn latency advantages apply.
- None of the benchmark tasks use `jq`-style filtering; results in the tables above do not reflect this advantage.

## Full data (medians)

| Task | Mode | Cost | Dur (s) | Turns | In tok | Out tok | Cache create | Cache read |
|---|---|---|---|---|---|---|---|---|
| user_me | cli | $0.1209 | 29.1 | 4 | 9 | 353 | 9772 | 101027 |
| user_me | cli_guided | $0.0856 | 22.0 | 2 | 7 | 161 | 9292 | 45938 |
| user_me | mcp | $0.1515 | 11.3 | 3 | 13 | 215 | 19685 | 44934 |
| user_me | mcp_guided | $0.1580 | 11.1 | 3 | 13 | 222 | 20679 | 45455 |
| calendar_today | cli | $0.1410 | 32.8 | 5 | 10 | 538 | 9986 | 129077 |
| calendar_today | cli_guided | $0.0856 | 21.4 | 2 | 7 | 191 | 9175 | 45954 |
| calendar_today | mcp | $0.1554 | 11.1 | 3 | 13 | 323 | 19877 | 45211 |
| calendar_today | mcp_guided | $0.1612 | 11.5 | 3 | 13 | 297 | 20861 | 45707 |
| mail_recent | cli | $0.1063 | 26.6 | 3 | 8 | 430 | 9380 | 73028 |
| mail_recent | cli_guided | $0.0885 | 23.2 | 2 | 7 | 252 | 9410 | 45949 |
| mail_recent | mcp | $0.1562 | 12.3 | 3 | 13 | 324 | 19960 | 45105 |
| mail_recent | mcp_guided | $0.1622 | 11.8 | 3 | 13 | 312 | 20974 | 45598 |
| mail_search | cli | $0.1601 | 34.1 | 5 | 10 | 561 | 12949 | 129353 |
| mail_search | cli_guided | $0.1248 | 27.1 | 3 | 8 | 377 | 12483 | 73826 |
| mail_search | mcp | $0.1726 | 14.6 | 3 | 13 | 320 | 22657 | 45087 |
| mail_search | mcp_guided | $0.1709 | 11.6 | 3 | 13 | 340 | 22251 | 45578 |
| calendar_free_slot | cli | $0.1248 | 32.6 | 4 | 9 | 509 | 9769 | 101169 |
| calendar_free_slot | cli_guided | $0.0879 | 20.4 | 2 | 7 | 259 | 9278 | 45977 |
| calendar_free_slot | mcp | $0.2142 | 24.1 | 6 | 21 | 944 | 21073 | 112171 |
| calendar_free_slot | mcp_guided | $0.1630 | 11.9 | 3 | 13 | 360 | 20903 | 45659 |
| chat_search_read | cli | $0.1690 | 41.8 | 6 | 11 | 734 | 11321 | 158709 |
| chat_search_read | cli_guided | $0.1117 | 26.9 | 3 | 8 | 393 | 10302 | 73797 |
| chat_search_read | mcp | $0.1903 | 17.9 | 4 | 14 | 448 | 21070 | 67396 |
| chat_search_read | mcp_guided | $0.1830 | 17.3 | 4 | 14 | 447 | 21933 | 68374 |
| daily_briefing | cli | $0.2260 | 58.6 | 9 | 14 | 1153 | 11770 | 246128 |
| daily_briefing | cli_guided | $0.1296 | 33.0 | 4 | 9 | 598 | 10112 | 101937 |
| daily_briefing | mcp | $0.1779 | 17.8 | 4 | 14 | 564 | 20572 | 67727 |
| daily_briefing | mcp_guided | $0.1926 | 24.3 | 4 | 14 | 848 | 21778 | 68755 |

## Observations

- **Lowest total cost:** CLI + hints ($2.22).
- **Lowest total wall time:** MCP + hints (307s).
- **Fewest total turns:** CLI + hints (57).
- Adding task-to-command hints reduced CLI total cost by 29% ($3.15 → $2.22) and CLI total turns by 47%.
- Adding task-to-tool hints reduced MCP total cost by 4% ($3.78 → $3.64) and MCP total turns by 12%.
- Per-turn cost is lower for CLI modes than MCP modes in these runs. Per-turn duration is lower for MCP modes.
- Output token totals are smaller for MCP modes than CLI modes. Contributing factor observed in transcripts: Bash-mode final responses more often restate JSON fields from tool output.

## Caveats

- N = 3 per (task, mode) cell. Variance is not fully captured; larger N would tighten the estimates.
- Claude API latency varies across time of day and API traffic. Comparing runs across different days or hours introduces noise unrelated to the surface under test.
- All runs used the same model. Results with smaller or larger models may differ in both shape and magnitude.
- The hinted-mode system prompts used here are condensed cookbooks (~20-30 lines). Production workflow documentation is typically longer and more task-specific.
- Only read-only tasks were benchmarked. Write operations involve confirmation loops that may shift the profile.
- Results reflect the volume of data present in the test mailbox/calendar at run time. Sparse inboxes produce smaller responses than busy ones.
