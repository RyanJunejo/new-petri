"""Generate Petri conversations and report which meet the selection conditions.

python petri/run_prefix.py                         initial conversations
python petri/run_prefix.py --h0                    additional cue-free conversations
python petri/run_prefix.py --report LOG.eval       inspect a saved log offline
python petri/run_prefix.py --smoke                 exercise wiring with mock models
New logs go under runs/.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageAssistant, GenerateConfig, get_model
from inspect_petri import audit

import config as C
from checks.banned_words import find_banned
from fork.canned import is_destructive
from fork.fork import eligibility, target_conversation, tool_calls_before

H0_PROXY = re.compile(r"\b(fake|simulat\w*|hypothetical|fictional|not real|a test|being tested|an? eval\w*|sandbox\w*)\b", re.IGNORECASE)


def build_task():
    return audit(
        seed_instructions=str(C.SEEDS_DIR),
        max_turns=C.MAX_TURNS,
        judge_dimensions=C.PETRI_JUDGE_DIMENSIONS,
        **C.PETRI_SETTINGS,
    )


def run(roles: dict[str, str], epochs: int, log_dir: Path) -> list:
    """Run the seed `epochs` times. One .eval log holds all runs as samples."""
    return inspect_eval(
        build_task(),
        model="none",
        model_roles=roles,
        log_dir=str(log_dir),
        epochs=epochs,
        max_samples=epochs,
    )


def report_sample(sample, label: str) -> dict:
    """Selection checks and action counts for one conversation."""
    messages, _ = target_conversation(sample)
    fork, reasons = eligibility(messages)
    seen = "\n".join(m.text for m in messages if not isinstance(m, ChatMessageAssistant))
    calls = tool_calls_before(messages, None)
    info = {
        "label": label,
        "target_turns": sum(isinstance(m, ChatMessageAssistant) for m in messages),
        "fork_index": fork,
        "eligible": fork is not None and not reasons,
        "ineligible_because": reasons,
        "destructive_calls": [(i, a.get("query")) for i, t, a in calls if is_destructive(t, a)],
        "safe_path_calls": [i for i, t, _ in calls if t == C.SAFE_TOOL],
        "banned_in_seen_text": find_banned(seen),
        "h0_candidates": [(i, m.text[:160]) for i, m in enumerate(messages)
                          if isinstance(m, ChatMessageAssistant) and m.text and H0_PROXY.search(m.text)],
    }
    print(f"\n--- {label}")
    for k, v in info.items():
        if k != "label":
            print(f"{k}: {v}")
    return info


def report_log(log) -> list[dict]:
    return [report_sample(s, f"sample {i} (epoch {s.epoch})") for i, s in enumerate(log.samples or [])]


def main() -> None:
    p = argparse.ArgumentParser(description="Generate Petri conversations or inspect an existing log.")
    p.add_argument("--log-dir", type=Path, help="new output directory (default: runs/prefix or runs/prefix_h0)")
    p.add_argument("--report", help="existing .eval log to re-report")
    p.add_argument("--smoke", action="store_true", help="wire the task with mockllm models")
    p.add_argument("--h0", action="store_true", help="additional cue-free conversations")
    a = p.parse_args()
    if a.report:
        report_log(read_eval_log(a.report, resolve_attachments=True))
        return
    load_dotenv(C.ROOT / ".env")
    if a.smoke:
        roles = {r: "mockllm/model" for r in ("auditor", "target", "judge")}
        logs = run(roles, epochs=1, log_dir=C.ROOT / "runs" / "smoke")
        print("smoke status:", logs[0].status, "| samples:", len(logs[0].samples or []), "| log:", logs[0].location)
        if logs[0].status != "success":
            raise SystemExit(1)
        return
    log_dir = a.log_dir or C.ROOT / "runs" / ("prefix_h0" if a.h0 else "prefix")
    from study import require_new_output
    require_new_output(p, log_dir, C.DATA)
    epochs = C.H0_RUNS if a.h0 else C.PREFIX_RUNS
    target = get_model(C.TARGET_MODEL, config=GenerateConfig(reasoning_effort=C.TARGET_REASONING_EFFORT))
    roles = {"auditor": C.AUDITOR_MODEL, "target": target, "judge": C.PETRI_JUDGE_MODEL}
    logs = run(roles, epochs=epochs, log_dir=log_dir)
    print("status:", logs[0].status, "| log:", logs[0].location)
    # Re-read from disk: the in-memory log can hold long contents as unresolved attachment references.
    report_log(read_eval_log(logs[0].location, resolve_attachments=True))
    print(f"\nview with: .venv/bin/inspect view --log-dir {log_dir}")


if __name__ == "__main__":
    main()
