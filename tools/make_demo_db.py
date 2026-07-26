#!/usr/bin/env python3
"""Build a database of synthetic activity.

Used for the screenshots in the README, for the end-to-end check in CI, and for
trying the dashboard out before pointing the tool at a real project. The output
is deterministic for a given seed, so a regenerated screenshot differs only
where the code changed.

Every identity, prompt and token count here is invented. Nothing in this file
touches Google Cloud.

    python3 tools/make_demo_db.py --out demo.db --days 30
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402

# Weighted so the dashboard shows a realistic long tail rather than a flat bar
# chart: two heavy users, three moderate, one occasional.
USERS = [
    ("dana.whitfield@example.com", 30),
    ("marcus.lin@example.com", 24),
    ("priya.raman@example.com", 16),
    ("tom.okafor@example.com", 12),
    ("sofia.almeida@example.com", 10),
    ("jen.castellanos@example.com", 4),
]

# (display name, engine surface, model, share)
AGENTS = [
    ("Order Support", "gcp.gemini_enterprise", "gemini-2.5-flash", 26),
    ("Policy Lookup", "gcp.gemini_enterprise", "gemini-3-pro", 20),
    ("core_assistant", "gcp.gemini_enterprise", "gemini-2.5-flash", 18),
    ("Claims Coordinator", "gcp.agent_engine", "gemini-3-pro", 22),
    ("Contract Review", "gcp.agent_engine", "gemini-2.5-pro", 14),
]

PROMPTS = [
    "What is the status of order 4471?",
    "Summarise the outstanding claims assigned to me this week.",
    "Which policies changed in the last quarter?",
    "Draft a response to the customer about the delayed shipment.",
    "Compare our standard terms against the redlined contract.",
    "How many open tickets are older than 14 days?",
    "Explain the deductible on policy PL-88213.",
    "Pull the renewal dates for the accounts in the north region.",
    "What did we agree on pricing in the last amendment?",
    "Give me a breakdown of refunds issued in June.",
    "Is this claim eligible for expedited processing?",
    "Find the contact for the Riverside account.",
    "Show the escalation history for ticket 91022.",
    "What are the coverage limits for commercial auto?",
    "Summarise yesterday's customer feedback themes.",
]

TOOLS = [
    ("lookup_order", "function"),
    ("search_policies", "function"),
    ("fetch_claim", "function"),
    ("vertex_ai_search", "extension"),
    ("send_notification", "function"),
]


# Relative likelihood that a turn lands in each UTC hour.
HOUR_WEIGHT = {
    **dict.fromkeys(range(0, 7), 0.05),
    **dict.fromkeys(range(7, 10), 0.9),
    **dict.fromkeys(range(10, 17), 1.0),
    **dict.fromkeys(range(17, 20), 0.5),
    **dict.fromkeys(range(20, 24), 0.1),
}


def _weighted(rng: random.Random, options: list[tuple]) -> tuple:
    return rng.choices(options, weights=[o[-1] for o in options], k=1)[0]


def _turn_time(rng: random.Random, end: dt.datetime, days: int) -> dt.datetime:
    """A timestamp skewed towards weekday working hours, as real traffic is."""
    while True:
        moment = end - dt.timedelta(
            days=rng.uniform(0, days),
            hours=rng.uniform(0, 24),
        )
        if moment.weekday() >= 5 and rng.random() < 0.85:
            continue  # weekends are quiet, but not empty
        if rng.random() <= HOUR_WEIGHT[moment.hour]:
            return moment


def build(out: str, *, days: int, turns: int, seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    end = dt.datetime(2026, 7, 24, 18, 0, tzinfo=dt.timezone.utc)

    turn_rows: list[dict] = []
    model_rows: list[dict] = []
    tool_rows: list[dict] = []

    for n in range(turns):
        user = _weighted(rng, USERS)[0]
        agent, platform, model, _ = _weighted(rng, AGENTS)
        started = _turn_time(rng, end, days)
        trace_id = f"{seed:04x}{n:028x}"
        session_id = str(4000 + n // 3)
        engine_id = "support-app_1700000000000"

        # A turn handled by the core assistant shares its trace with the log
        # entry, so it resolves to a user. A turn routed to a custom agent runs
        # under its own trace, and arrives with no identity attached: the same
        # split a real collection shows.
        attributed = platform == "gcp.gemini_enterprise"
        if attributed:
            turn_rows.append(
                {
                    "trace_id": trace_id,
                    "ts": started.strftime("%Y-%m-%dT%H:%M:%S"),
                    "user_principal": user,
                    "query_text": rng.choice(PROMPTS),
                    # zlib.crc32, not hash(): str hashing is salted per process.
                    "agent_id": str(8800000000 + zlib.crc32(agent.encode()) % 99999999),
                    "agent_display_name": agent,
                    "engine_id": engine_id,
                    "session_id": session_id,
                    "location": "global",
                    "insert_id": f"insert-{n}",
                }
            )

        # One turn fans out into one or more model calls.
        for step in range(rng.choices([1, 2, 3], weights=[62, 28, 10], k=1)[0]):
            call_start = started + dt.timedelta(seconds=step * rng.uniform(1.5, 6.0))
            latency = rng.uniform(0.8, 9.0)
            # Grounded answers carry a large retrieved context.
            grounded = rng.random() < 0.45
            input_tokens = int(rng.uniform(9000, 48000) if grounded else rng.uniform(600, 7000))
            output_tokens = int(rng.uniform(80, 1900))

            model_rows.append(
                {
                    "span_id": f"{trace_id}:{step:02x}",
                    "trace_id": trace_id,
                    "start_time": call_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end_time": (call_start + dt.timedelta(seconds=latency)).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                    "span_name": "generate_content" if attributed else "call_llm",
                    "platform": platform,
                    "resource_id": f"projects/1234/locations/global/agents/{n % 97}",
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "agent_name": agent,
                    "session_id": session_id,
                    "engine_id": engine_id,
                    "latency_ms": int(latency * 1000),
                }
            )

            if rng.random() < 0.55:
                tool_name, tool_type = rng.choice(TOOLS)
                tool_latency = rng.uniform(0.05, 2.4)
                tool_rows.append(
                    {
                        "span_id": f"{trace_id}:t{step:02x}",
                        "trace_id": trace_id,
                        "start_time": call_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "end_time": (call_start + dt.timedelta(seconds=tool_latency)).strftime(
                            "%Y-%m-%dT%H:%M:%S"
                        ),
                        "tool_name": tool_name,
                        "tool_type": tool_type,
                        "latency_ms": int(tool_latency * 1000),
                    }
                )

    conn = store.connect(out)
    store.upsert_turns(conn, turn_rows)
    store.upsert_model_calls(conn, model_rows)
    store.upsert_tool_calls(conn, tool_rows)
    store.set_meta(conn, "project", "example-project")
    store.set_meta(conn, "last_collect_utc", end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    conn.close()

    return {"turns": len(turn_rows), "model_calls": len(model_rows), "tool_calls": len(tool_rows)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default="demo.db", help="output database (default: demo.db)")
    p.add_argument("--days", type=int, default=30, help="span of the generated window")
    p.add_argument("--turns", type=int, default=420, help="number of chat turns to invent")
    p.add_argument("--seed", type=int, default=7, help="seed; the same seed gives the same data")
    args = p.parse_args()

    counts = build(args.out, days=args.days, turns=args.turns, seed=args.seed)
    print(
        f"wrote {args.out}: {counts['turns']:,} turns · "
        f"{counts['model_calls']:,} model calls · {counts['tool_calls']:,} tool calls"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
