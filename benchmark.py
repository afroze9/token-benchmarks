#!/usr/bin/env python3
"""
Benchmark token usage: graph-cli CLI vs MCP.

Runs the same read-only tasks through `claude -p` twice:
  - cli mode: graph-cli invoked via Bash tool
  - mcp mode: graph-cli MCP server (graph-cli mcp) as stdio MCP

Each run is a fresh, isolated session from a clean temp CWD (no CLAUDE.md
auto-discovery) with a minimal system prompt. Usage tokens come from the
JSON envelope returned by --output-format json.

Requires:
  - graph-cli already authenticated (run `graph-cli auth login` first)
  - claude CLI on PATH
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


TASKS: list[dict[str, str]] = [
    {
        "id": "user_me",
        "complexity": "trivial",
        "prompt": "What is my email address and display name? Answer in one line.",
    },
    {
        "id": "calendar_today",
        "complexity": "simple",
        "prompt": (
            "List today's calendar events in timezone Asia/Karachi. "
            "For each event show start time and subject, one per line."
        ),
    },
    {
        "id": "mail_recent",
        "complexity": "simple",
        "prompt": (
            "List my 10 most recent Inbox emails. "
            "Show sender and subject, one per line."
        ),
    },
    {
        "id": "mail_search",
        "complexity": "medium",
        "prompt": (
            "Find the most recent email with 'Simplicant' in the sender or subject. "
            "Show sender, subject, and received date. If none, say 'none found'."
        ),
    },
    {
        "id": "calendar_free_slot",
        "complexity": "medium",
        "prompt": (
            "Is alice@example.com free between 3:00 PM and 5:00 PM today (Asia/Karachi)? "
            "Answer yes or no, with a one-line reason."
        ),
    },
    {
        "id": "chat_search_read",
        "complexity": "medium",
        "prompt": (
            "Search my Teams chats for a chat with 'sqms_internal' in the topic. "
            "If found, show the last 3 messages (sender + one-line body). "
            "If not found, say 'no sqms_internal chat'."
        ),
    },
    {
        "id": "daily_briefing",
        "complexity": "complex",
        "prompt": (
            "Brief me on today (Asia/Karachi): "
            "(1) today's calendar events (time + subject), and "
            "(2) the 5 most recent unread Inbox emails (sender + subject). "
            "Flag any meeting conflicts. Keep it tight."
        ),
    },
]


_NO_PARALLEL = (
    "IMPORTANT: Invoke tools strictly one at a time. Do NOT issue parallel "
    "tool calls in the same turn — graph-cli does not support concurrent "
    "invocations (token cache contention)."
)

CLI_SYSTEM_PROMPT = (
    "You are a terse read-only agent. Answer the user's request using the "
    "`graph-cli` command-line tool via the Bash tool. "
    "Use `graph-cli --help` or `graph-cli <subcommand> --help` if unsure. "
    "Default output is JSON; parse it and answer concisely. "
    "Do NOT send mail, modify calendar, send chat, or perform any write action. "
    + _NO_PARALLEL
)

MCP_SYSTEM_PROMPT = (
    "You are a terse read-only agent. Answer the user's request using the "
    "graph-cli MCP tools (tool names prefixed with `mcp__graph-cli__`). "
    "Answer concisely. "
    "Do NOT send mail, modify calendar, send chat, or perform any write action. "
    + _NO_PARALLEL
)

CLI_GUIDED_SYSTEM_PROMPT = """You are a terse read-only agent using graph-cli via the Bash tool.

## Common read-only commands (default output is JSON — parse inline)

Always pass `--timezone "Asia/Karachi"` to commands that accept it.

**User / identity**
  graph-cli user me

**Mail (Inbox)**
  graph-cli mail list --timezone "Asia/Karachi" --top <N> --folder Inbox
  graph-cli mail list --timezone "Asia/Karachi" --top <N> --folder Inbox --filter "isRead eq false"
  graph-cli mail search "<query>" --timezone "Asia/Karachi"
  graph-cli mail get <message-id> --timezone "Asia/Karachi"

**Calendar**
  graph-cli calendar events --timezone "Asia/Karachi" --start <ISO> --end <ISO>
  graph-cli calendar schedule --timezone "Asia/Karachi" --users "<e1,e2,...>" --start <ISO> --end <ISO>

**Teams chat**
  graph-cli chat search --query "<topic>" --format json
  graph-cli chat messages "<chat-id>" --top <N> --format json

## Rules
- Output is JSON by default — parse it yourself, never use `--format table`.
- For "today" in Asia/Karachi, build ISO timestamps like `2026-04-19T00:00:00` and `2026-04-19T23:59:59`.
- Answer concisely. No preamble, no restating the JSON.
- Do NOT send mail, modify calendar, send chat, or perform any write action.
""" + _NO_PARALLEL

MCP_GUIDED_SYSTEM_PROMPT = """You are a terse read-only agent using the graph-cli MCP tools (prefix `mcp__graph-cli__`).

## Tool picks by task

Always pass `timezone: "Asia/Karachi"` to tools that accept it.

**User / identity** → `mcp__graph-cli__user_me`

**Mail (Inbox)**
  List:   `mcp__graph-cli__mail_list` with `folder: "Inbox"`, `top: <N>`, `timezone: "Asia/Karachi"`
  Unread: same, plus `filter: "isRead eq false"`
  Search: `mcp__graph-cli__mail_search` with `query: "<term>"`, `timezone: "Asia/Karachi"`
  Read:   `mcp__graph-cli__mail_get` with `id: "<message-id>"`

**Calendar**
  Events today: `mcp__graph-cli__calendar_events` with `start`, `end` (ISO in Asia/Karachi), `timezone`
  Check availability: `mcp__graph-cli__calendar_schedule` with `users: ["..."]`, `start`, `end`, `timezone`

**Teams chat**
  Find chat: `mcp__graph-cli__chat_search` with `query: "<topic>"`
  Read messages: `mcp__graph-cli__chat_messages` with `chatId: "<id>"`, `top: <N>`

## Rules
- For "today" in Asia/Karachi, build ISO timestamps like `2026-04-19T00:00:00` and `2026-04-19T23:59:59`.
- Answer concisely. No preamble, no restating the tool output.
- Do NOT send mail, modify calendar, send chat, or perform any write action.
""" + _NO_PARALLEL


def build_mcp_config(mode: str) -> dict:
    if mode in ("cli", "cli_guided"):
        return {"mcpServers": {}}
    if mode in ("mcp", "mcp_guided"):
        return {
            "mcpServers": {
                "graph-cli": {
                    "command": "graph-cli",
                    "args": ["mcp"],
                }
            }
        }
    raise ValueError(mode)


def run_one(prompt: str, mode: str, model: str | None, timeout_s: int) -> dict[str, Any]:
    """Run a single claude -p invocation. Returns parsed JSON + timing."""
    mcp_config = build_mcp_config(mode)

    if mode == "cli":
        system_prompt = CLI_SYSTEM_PROMPT
        allowed = ["Bash"]
        disallowed = ["Write", "Edit", "NotebookEdit"]
    elif mode == "cli_guided":
        system_prompt = CLI_GUIDED_SYSTEM_PROMPT
        allowed = ["Bash"]
        disallowed = ["Write", "Edit", "NotebookEdit"]
    elif mode == "mcp_guided":
        system_prompt = MCP_GUIDED_SYSTEM_PROMPT
        allowed = ["mcp__graph-cli"]
        disallowed = ["Bash", "Write", "Edit", "NotebookEdit"]
    else:
        system_prompt = MCP_SYSTEM_PROMPT
        allowed = ["mcp__graph-cli"]
        disallowed = ["Bash", "Write", "Edit", "NotebookEdit"]

    with tempfile.TemporaryDirectory(prefix="claude-bench-") as cwd:
        mcp_path = Path(cwd) / "mcp.json"
        mcp_path.write_text(json.dumps(mcp_config))

        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--mcp-config", str(mcp_path),
            "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "--append-system-prompt", system_prompt,
            "--allowed-tools", *allowed,
            "--disallowed-tools", *disallowed,
            "--setting-sources", "user",
            "--no-session-persistence",
        ]
        if model:
            cmd.extend(["--model", model])

        t0 = time.time()
        try:
            res = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True,
                timeout=timeout_s, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return {"_error": "timeout", "_duration_s": time.time() - t0}
        dur = time.time() - t0

        if res.returncode != 0:
            return {
                "_error": f"exit {res.returncode}",
                "_stderr": res.stderr[-500:],
                "_stdout": res.stdout[-500:],
                "_duration_s": dur,
            }
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError as e:
            return {
                "_error": f"bad json: {e}",
                "_stdout": res.stdout[-500:],
                "_duration_s": dur,
            }
        data["_duration_s"] = dur
        return data


def extract_row(task: dict, mode: str, run_idx: int, data: dict) -> dict:
    base = {
        "task": task["id"],
        "complexity": task["complexity"],
        "mode": mode,
        "run": run_idx,
    }
    if "_error" in data:
        base.update({
            "error": data["_error"],
            "duration_s": round(data.get("_duration_s", 0), 2),
        })
        return base
    usage = data.get("usage") or {}
    base.update({
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "total_cost_usd": data.get("total_cost_usd"),
        "num_turns": data.get("num_turns"),
        "duration_ms": data.get("duration_ms"),
        "duration_s": round(data.get("_duration_s", 0), 2),
        "is_error": data.get("is_error"),
        "result_preview": (data.get("result") or "")[:180].replace("\n", " "),
    })
    return base


def print_row(row: dict) -> None:
    if "error" in row:
        print(f"  ERR: {row['error']} ({row['duration_s']}s)")
        return
    in_t = row["input_tokens"]
    out_t = row["output_tokens"]
    cache_r = row["cache_read_input_tokens"]
    cache_c = row["cache_creation_input_tokens"]
    cost = row["total_cost_usd"] or 0.0
    turns = row["num_turns"]
    dur = row["duration_s"]
    print(
        f"  in={in_t:>6} out={out_t:>5} "
        f"cache_r={cache_r:>7} cache_c={cache_c:>6} "
        f"cost=${cost:.4f} turns={turns} dur={dur}s"
    )


def summarize(rows: list[dict]) -> None:
    print("\n=== MEDIAN ACROSS RUNS ===")
    header = f"{'task':<22} {'mode':<4} {'in':>7} {'out':>6} {'cache_r':>8} {'cache_c':>7} {'cost':>8} {'turns':>5} {'dur':>6}"
    print(header)
    print("-" * len(header))
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if "error" in r:
            continue
        by.setdefault((r["task"], r["mode"]), []).append(r)

    def med(xs):
        xs = [x for x in xs if x is not None]
        return statistics.median(xs) if xs else 0

    tasks_seen = sorted({k[0] for k in by.keys()})
    for task in tasks_seen:
        cli = by.get((task, "cli"), [])
        mcp = by.get((task, "mcp"), [])
        for mode, runs in [("cli", cli), ("mcp", mcp)]:
            if not runs:
                continue
            print(
                f"{task:<22} {mode:<4} "
                f"{med(r['input_tokens'] for r in runs):>7.0f} "
                f"{med(r['output_tokens'] for r in runs):>6.0f} "
                f"{med(r['cache_read_input_tokens'] for r in runs):>8.0f} "
                f"{med(r['cache_creation_input_tokens'] for r in runs):>7.0f} "
                f"${med(r['total_cost_usd'] for r in runs):>7.4f} "
                f"{med(r['num_turns'] for r in runs):>5.0f} "
                f"{med(r['duration_s'] for r in runs):>5.1f}s"
            )

    # Head-to-head delta
    print("\n=== CLI vs MCP DELTA (mcp - cli, median) ===")
    print(f"{'task':<22} {'d_input':>10} {'d_output':>10} {'d_cost':>10} {'d_turns':>8}")
    for task in tasks_seen:
        cli = by.get((task, "cli"), [])
        mcp = by.get((task, "mcp"), [])
        if not cli or not mcp:
            continue
        d_in = med(r['input_tokens'] for r in mcp) - med(r['input_tokens'] for r in cli)
        d_out = med(r['output_tokens'] for r in mcp) - med(r['output_tokens'] for r in cli)
        d_cost = med(r['total_cost_usd'] for r in mcp) - med(r['total_cost_usd'] for r in cli)
        d_turns = med(r['num_turns'] for r in mcp) - med(r['num_turns'] for r in cli)
        print(f"{task:<22} {d_in:>+10.0f} {d_out:>+10.0f} {d_cost:>+10.4f} {d_turns:>+8.0f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3, help="runs per (task, mode)")
    ap.add_argument("--modes", nargs="+", default=["cli", "mcp"], choices=["cli", "cli_guided", "mcp", "mcp_guided"])
    ap.add_argument("--append", action="store_true", help="append to existing --out CSV instead of overwriting")
    ap.add_argument("--tasks", nargs="+", default=None, help="task ids (default: all)")
    ap.add_argument("--model", default=None, help="override model (e.g. sonnet, haiku)")
    ap.add_argument("--timeout", type=int, default=300, help="per-run timeout seconds")
    ap.add_argument("--delay", type=float, default=5.0, help="seconds to sleep between runs (Graph rate-limit safety)")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--raw-dir", default=None, help="also dump raw JSON envelopes here")
    ap.add_argument("--smoke", action="store_true", help="1 task x 1 run per mode, for validation")
    args = ap.parse_args()

    tasks = TASKS
    if args.smoke:
        tasks = TASKS[:1]
        args.runs = 1
    elif args.tasks:
        tasks = [t for t in TASKS if t["id"] in args.tasks]
        if not tasks:
            print(f"no matching tasks. available: {[t['id'] for t in TASKS]}")
            return 2

    raw_dir = Path(args.raw_dir) if args.raw_dir else None
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    total = len(tasks) * len(args.modes) * args.runs
    n = 0
    for task in tasks:
        for mode in args.modes:
            for i in range(1, args.runs + 1):
                n += 1
                if n > 1 and args.delay > 0:
                    time.sleep(args.delay)
                print(f"[{n}/{total}] {task['id']} mode={mode} run={i}")
                data = run_one(task["prompt"], mode, args.model, args.timeout)
                row = extract_row(task, mode, i, data)
                rows.append(row)
                print_row(row)
                if raw_dir:
                    fname = f"{task['id']}__{mode}__run{i}.json"
                    (raw_dir / fname).write_text(json.dumps(data, indent=2))

    out_path = Path(args.out)
    if rows:
        fieldnames = [
            "task", "complexity", "mode", "run",
            "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens",
            "total_cost_usd", "num_turns", "duration_ms", "duration_s",
            "is_error", "error", "result_preview",
        ]
        mode_str = "a" if args.append and out_path.exists() else "w"
        with out_path.open(mode_str, newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if mode_str == "w":
                w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"\n{'appended' if mode_str == 'a' else 'wrote'} {len(rows)} rows to {out_path}")

    summarize(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
