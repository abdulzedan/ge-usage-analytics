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

# (display name, engine surface, model, linkage, share)
#
# `linkage` is where the agent's model calls land relative to the logged turn,
# which is what decides attribution:
#   trace    inside the turn's own trace (core assistant and agents hosted in
#            Gemini Enterprise)
#   session  under the agent's own traces, with the Gemini Enterprise session
#            stamped on the spans -- the close-the-gap recipe from the README
#   none     under the agent's own traces and its own Agent Engine session,
#            so no key matches and the calls stay unattributed
AGENTS = [
    ("Order Support", "gcp.gemini_enterprise", "gemini-2.5-flash", "trace", 26),
    ("Policy Lookup", "gcp.gemini_enterprise", "gemini-3-pro", "trace", 20),
    ("core_assistant", "gcp.gemini_enterprise", "gemini-2.5-flash", "trace", 18),
    ("Claims Coordinator", "gcp.agent_engine", "gemini-3-pro", "none", 22),
    ("Contract Review", "gcp.agent_engine", "gemini-2.5-pro", "session", 14),
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

RESEARCH_PROMPTS = [
    "Research how the EU AI Act affects our claims-handling workflows.",
    "Deep dive: what are competitors charging for embedded insurance?",
    "Put together a briefing on flood-risk trends in the north region.",
    "Research the regulatory changes to commercial auto coverage since 2024.",
    "Compile what is publicly known about the Riverside account's expansion.",
]

NOTEBOOK_ACTIONS = [
    # (action, carries a prompt, share) — the service paths the log records
    ("NotebookService.GenerateFreeFormStreamed", True, 45),
    ("NotebookService.InteractSources", True, 15),
    ("SourceService.UploadSourceFile", False, 20),
    ("SourceService.BatchCreateSources", False, 10),
    ("NotebookService.CreateNotebook", False, 10),
]

NOTEBOOK_PROMPTS = [
    "Summarise the uploaded quarterly claim reports.",
    "What do these contracts say about early termination?",
    "List the open questions across the underwriting notes.",
    "Compare the three vendor proposals in this notebook.",
    "Which sources mention the 2025 pricing review?",
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


def _research_call(
    rng: random.Random,
    trace_id: str,
    step: int,
    start_at: dt.datetime,
    agent_name: str,
    *,
    heavy: bool,
) -> dict:
    """One Deep Research model call; `heavy` marks the reading-laden sub-agents.

    No session id on any of these: as observed on a live project, Deep
    Research spans carry the engine and their own agent name but no
    `gen_ai.conversation.id`, so only calls inside the request trace resolve.
    """
    latency = rng.uniform(4.0, 30.0)
    return {
        "span_id": f"{trace_id}:{step:02x}",
        "trace_id": trace_id,
        "start_time": start_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "end_time": (start_at + dt.timedelta(seconds=latency)).strftime("%Y-%m-%dT%H:%M:%S"),
        "span_name": "generate_content",
        "platform": "gcp.gemini_enterprise",
        "resource_id": f"projects/1234/locations/global/agents/{agent_name}",
        "model": "gemini-3-pro",
        "input_tokens": int(rng.uniform(18000, 80000) if heavy else rng.uniform(2000, 9000)),
        "output_tokens": int(rng.uniform(400, 2600)),
        "agent_name": agent_name,
        "session_id": None,
        "engine_id": "support-app_1700000000000",
        "latency_ms": int(latency * 1000),
    }


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
        agent, platform, model, linkage, _ = _weighted(rng, AGENTS)
        started = _turn_time(rng, end, days)
        turn_trace = f"{seed:04x}{n:028x}"
        session_id = str(4000 + n // 3)
        engine_id = "support-app_1700000000000"

        # Every turn is logged with the signed-in account and the session,
        # whichever agent answers it. Attribution is decided lower down, by
        # where the agent's spans land (see AGENTS).
        turn_rows.append(
            {
                "trace_id": turn_trace,
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

        call_trace = turn_trace if linkage == "trace" else f"{seed:04x}a{n:027x}"
        call_session = str(90000 + n // 3) if linkage == "none" else session_id

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
                    "span_id": f"{call_trace}:{step:02x}",
                    "trace_id": call_trace,
                    "start_time": call_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end_time": (call_start + dt.timedelta(seconds=latency)).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                    "span_name": "generate_content" if linkage == "trace" else "call_llm",
                    "platform": platform,
                    "resource_id": f"projects/1234/locations/global/agents/{n % 97}",
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "agent_name": agent,
                    "session_id": call_session,
                    "engine_id": engine_id,
                    "latency_ms": int(latency * 1000),
                }
            )

            if rng.random() < 0.55:
                tool_name, tool_type = rng.choice(TOOLS)
                tool_latency = rng.uniform(0.05, 2.4)
                tool_rows.append(
                    {
                        "span_id": f"{call_trace}:t{step:02x}",
                        "trace_id": call_trace,
                        "start_time": call_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "end_time": (call_start + dt.timedelta(seconds=tool_latency)).strftime(
                            "%Y-%m-%dT%H:%M:%S"
                        ),
                        "tool_name": tool_name,
                        "tool_type": tool_type,
                        "latency_ms": int(tool_latency * 1000),
                    }
                )

    # Deep Research turns, shaped like the live behaviour (observed August
    # 2026): the request is one logged StreamAssist turn that records no
    # agentInfo; the planner and a minority of sub-agent calls execute inside
    # the request trace and resolve through it; the remaining sub-agents run
    # under traces of their own with no session stamped, so no exact key
    # exists and they stay unattributed under their deep_research_child_N
    # agent names.
    for n in range(max(2, turns // 30)):
        user = _weighted(rng, USERS)[0]
        started = _turn_time(rng, end, days)
        turn_trace = f"{seed:04x}d{n:027x}"

        turn_rows.append(
            {
                "trace_id": turn_trace,
                "ts": started.strftime("%Y-%m-%dT%H:%M:%S"),
                "user_principal": user,
                "query_text": rng.choice(RESEARCH_PROMPTS),
                "agent_id": None,
                "agent_display_name": None,
                "engine_id": "support-app_1700000000000",
                "session_id": str(7000 + n),
                "location": "global",
                "insert_id": f"insert-dr-{n}",
            }
        )

        # The planner, on the turn's own trace.
        model_rows.append(
            _research_call(rng, turn_trace, 0, started + dt.timedelta(seconds=2),
                           "deep_research", heavy=False)
        )

        # Parallel sub-agents: some land in the request trace, the rest run
        # detached and unrecoverable until the platform stamps a key.
        for k in range(rng.randint(3, 6)):
            in_trace = rng.random() < 0.4
            child_trace = turn_trace if in_trace else f"{seed:04x}e{n:013x}{k:014x}"
            sub_start = started + dt.timedelta(seconds=rng.uniform(20, 240))
            model_rows.append(
                _research_call(rng, child_trace, k + 1, sub_start,
                               f"deep_research_child_{k}", heavy=not in_trace)
            )
            tool_rows.append(
                {
                    "span_id": f"{child_trace}:t{k + 1:02x}",
                    "trace_id": child_trace,
                    "start_time": sub_start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end_time": (sub_start + dt.timedelta(seconds=1.2)).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                    "tool_name": "google_search",
                    "tool_type": "extension",
                    "latency_ms": 1200,
                }
            )

    # NotebookLM Enterprise activity: a separate log with no tokens anywhere,
    # so these rows never touch the model_calls side of the house.
    nblm_rows: list[dict] = []
    nblm_users = USERS[:4]  # the heavier chat users are also the notebook users
    for n in range(max(6, turns // 10)):
        user = _weighted(rng, nblm_users)[0]
        action, has_prompt, _ = _weighted(rng, NOTEBOOK_ACTIONS)
        moment = _turn_time(rng, end, days)
        nblm_rows.append(
            {
                "insert_id": f"nblm-{n}",
                "ts": moment.strftime("%Y-%m-%dT%H:%M:%S"),
                "user_principal": user,
                "action": action,
                "notebook_id": f"nb-{rng.randint(1, 9)}",
                "query_text": rng.choice(NOTEBOOK_PROMPTS) if has_prompt else None,
                "location": "global",
            }
        )

    conn = store.connect(out)
    store.upsert_turns(conn, turn_rows)
    store.upsert_model_calls(conn, model_rows)
    store.upsert_tool_calls(conn, tool_rows)
    store.upsert_notebooklm_activity(conn, nblm_rows)
    store.set_meta(conn, "project", "example-project")
    store.set_meta(conn, "last_collect_utc", end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    conn.close()

    return {
        "turns": len(turn_rows),
        "model_calls": len(model_rows),
        "tool_calls": len(tool_rows),
        "notebooklm": len(nblm_rows),
    }


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
        f"{counts['model_calls']:,} model calls · {counts['tool_calls']:,} tool calls · "
        f"{counts['notebooklm']:,} notebooklm activities"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
