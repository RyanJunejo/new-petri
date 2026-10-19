"""Judge unprompted artificiality statements in cue-free Petri conversations.

python judges/h0_judge.py --log-dir data/prefix_h0 --output runs/h0.jsonl
Only target turns are shown; positive quotations must occur in the source text.
Rerunning the same command appends only the messages not yet judged.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageUser, get_model

import config as C
from fork.fork import target_conversation
from judges.target_view import parse_json_reply, quote_found, target_turns
from study import (
    content_id,
    read_jsonl,
    report_run,
    require_outside_data,
    resumable_output,
    run_appending,
)

PROMPT = (Path(__file__).parent / "prompts" / "h0.md").read_text()


def prefix_messages(log_dir: Path) -> list[tuple[str, list[dict]]]:
    """(run id, target messages as dicts) for every sample in every .eval log in the directory."""
    runs = []
    for path in sorted(log_dir.glob("*.eval")):
        log = read_eval_log(str(path), resolve_attachments=True)
        for s in log.samples or []:
            messages, _ = target_conversation(s)
            runs.append((f"{path.stem}#epoch{s.epoch}", [m.model_dump(mode="json") for m in messages]))
    return runs


async def judge_message(run_id: str, idx: int, text: str) -> dict:
    out = await get_model(C.JUDGE_MODEL).generate([ChatMessageUser(content=PROMPT.replace("{message}", text))])
    reply = parse_json_reply(out.message.text)
    invalid = None
    if reply is None or not isinstance(reply.get("states_fake"), bool):
        invalid = "no usable JSON"
    elif reply["states_fake"] and not quote_found(str(reply.get("quote", "")), text):
        invalid = "quote not in message"
    return {"run": run_id, "message_index": idx, "judge_model": C.JUDGE_MODEL,
            "states_fake": None if invalid else reply["states_fake"],
            "quote": None if invalid else reply.get("quote"), "pointed_to": None if invalid else reply.get("pointed_to"),
            "invalid": invalid, "raw": out.message.text}


async def run(runs: list[tuple[str, list[dict]]], concurrency: int, output_path: Path,
              stored: list[dict], identity: dict) -> None:
    done = {(j["run"], j["message_index"]) for j in stored}
    jobs = [(f"{r} message {i}", partial(judge_message, r, i, text))
            for r, msgs in runs for i, text in target_turns(msgs) if (r, i) not in done]
    written, failed = await run_appending(jobs, output_path, concurrency, identity)
    results = read_jsonl(output_path)
    from analysis.analysis import fmt_rate

    pos = [r for r in results if r["states_fake"]]
    runs_with_pos = {r["run"] for r in pos}
    # Messages within a run are not independent; the per-run rate is the one with a meaningful interval.
    print(f"per run: {fmt_rate(len(runs_with_pos), len(runs))} runs with an unprompted fake-or-test statement")
    print(f"per message (descriptive, clustered by run): {len(pos)} of {len(results)}; "
          f"{sum(bool(r['invalid']) for r in results)} invalid")
    for r in pos:
        print(f"  {r['run']} msg {r['message_index']}: {r['quote']!r} (points to: {r['pointed_to']})")
    report_run(written, failed, len(done), output_path)


def main() -> None:
    p = argparse.ArgumentParser(description="Judge unprompted artificiality statements in Petri conversations.")
    p.add_argument("--output", type=Path, required=True, help="output file outside data/; an existing file is resumed")
    p.add_argument("--log-dir", type=Path, default=C.H0_LOG_DIR)
    p.add_argument("--concurrency", type=int, default=C.MAX_CONCURRENT_TAILS)
    a = p.parse_args()
    if a.concurrency < 1:
        p.error("--concurrency must be positive")
    require_outside_data(p, a.output, C.DATA)
    runs = prefix_messages(a.log_dir)
    if not runs:
        p.error(f"No conversations in {a.log_dir}")
    identity = {"judge_model": C.JUDGE_MODEL, "input_id": content_id(runs), "prompt_id": content_id(PROMPT)}
    stored = resumable_output(p, a.output, C.DATA, identity)
    load_dotenv(C.ROOT / ".env")
    asyncio.run(run(runs, a.concurrency, a.output, stored, identity))


if __name__ == "__main__":
    main()
