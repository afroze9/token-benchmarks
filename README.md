# token-benchmarks

Token usage and latency benchmarks comparing **CLI** and **MCP** tool surfaces for a large-language-model agent interacting with Microsoft Graph via [`graph-cli`](https://github.com/afroze9/graph-cli).

The same tool (`graph-cli`) exposes both surfaces with 1:1 command parity, so any measured differences are attributable to the surface itself rather than the business logic.

## Reports

- **[WHITEPAPER.md](runs/monday/WHITEPAPER.md)** — full academic-style writeup for the Monday (2026-04-20) run.
- **[runs/monday/REPORT.md](runs/monday/REPORT.md)** — compact markdown report.
- **[runs/sunday/REPORT.md](runs/sunday/REPORT.md)** — compact markdown report from a sparser-data day.

Charts are embedded in each report and also live under `runs/<day>/charts/`.

## What's measured

Four configurations on seven read-only Microsoft Graph tasks, N=3 replicates per cell:

| Configuration | Surface | Priming |
|---|---|---|
| `cli` | `graph-cli` via Bash tool | minimal system prompt |
| `cli_guided` | `graph-cli` via Bash tool | task-to-command cookbook in prompt |
| `mcp` | `graph-cli mcp` stdio MCP server | minimal system prompt |
| `mcp_guided` | `graph-cli mcp` stdio MCP server | task-to-tool cookbook in prompt |

For each run the harness captures: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `total_cost_usd`, `num_turns`, `duration_ms`, and wall-clock duration — from Claude Code's `--output-format json` envelope.

### Tasks

| id | complexity | what it exercises |
|---|---|---|
| `user_me` | trivial | single tool call, tiny response |
| `calendar_today` | simple | one read with timezone param, list output |
| `mail_recent` | simple | one read, list output |
| `mail_search` | medium | search + pick latest |
| `calendar_free_slot` | medium | scheduling probe, structured params |
| `chat_search_read` | medium | chained calls (search → messages) |
| `daily_briefing` | complex | multiple reads, synthesis, conflict detection |

## Scripts

- `benchmark.py` — driver. Spawns `claude -p` subprocesses per (task, mode, run), writes results CSV.
- `report.py` — generates `REPORT.md` plus 5 PNG charts from a results CSV.
- `whitepaper.py` — generates a longer academic-style `WHITEPAPER.md` from the same CSV.

## Reproducing

Prerequisites:

- [Claude Code](https://claude.com/claude-code) CLI on `PATH`
- [`graph-cli`](https://github.com/afroze9/graph-cli) installed and authenticated (`graph-cli auth login`)
- Python 3.10+ with `pandas` and `matplotlib`

Run the full benchmark:

```bash
python benchmark.py --modes cli cli_guided mcp mcp_guided --runs 3 \
    --out runs/<label>/results.csv --raw-dir runs/<label>/raw/
```

Generate reports:

```bash
python report.py --csv runs/<label>/results.csv --out-dir runs/<label>/
python whitepaper.py --csv runs/<label>/results.csv --out-dir runs/<label>/ \
    --run-label "<e.g. Tuesday 2026-04-21 afternoon>"
```

A full 4-mode × 7-task × 3-run suite takes 30-60 minutes and costs a few USD in Claude API charges depending on the active model.

## Privacy note

The `raw/` directories produced by `benchmark.py` contain Claude's complete tool-call transcripts, which include the text of real emails, calendar events, and Teams messages from the Graph account that ran the benchmark. **They are excluded from this repository via `.gitignore`.** The `results.csv` files included here have had the `result_preview` column stripped for the same reason. If you reproduce locally, treat your `raw/` outputs accordingly.

## License

MIT. See [LICENSE](LICENSE).
