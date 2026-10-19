"""Scan text the target sees for banned frame words.

python checks/banned_words.py                      the seed and the four condition outputs
python checks/banned_words.py FILE [FILE ...]      the given files
python checks/banned_words.py --runs               every saved Petri conversation and continuation -> results/banned_scan.jsonl
python checks/banned_words.py --runs --tails runs/interview.jsonl --output runs/banned_scan.jsonl
Add --prefix if the run continued a different exported conversation. Exit code 1 if anything is found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

BANNED_RE = re.compile(r"\b(?:" + "|".join(f"{s}\\w*" for s in C.BANNED_STEMS) + r")\b", re.IGNORECASE)


def find_banned(text: str) -> list[tuple[int, str]]:
    """(line number, matched word) for every banned stem on a word boundary."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        hits.extend((i, m.group(0)) for m in BANNED_RE.finditer(line))
    return hits


def scan(label: str, text: str, extra_words: tuple[str, ...] = ()) -> bool:
    hits = find_banned(text)
    for word in extra_words:
        hits.extend((i, word) for i, line in enumerate(text.splitlines(), 1) if re.search(rf"\b{word}\b", line, re.IGNORECASE))
    status = "clean" if not hits else f"{len(hits)} hit(s): " + ", ".join(f"line {i} '{w}'" for i, w in hits[:8])
    print(f"{label}: {status}")
    return not hits


def seen_text(messages: list[dict]) -> str:
    """Everything the target saw in a conversation: system prompt, user turns and tool results."""
    out = []
    for m in messages:
        if m["role"] == "assistant":
            continue
        c = m["content"]
        out.append(c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c if x.get("type") == "text"))
    return "\n".join(out)


def scan_runs(tails_path: Path, prefix_path: Path, log_dirs: list[Path], out: Path) -> int:
    """One record per Petri conversation and per continuation. A conversation is scanned whole; a continuation is
    scanned as the replayed prefix with its branch output swapped in plus the continuation. Frame cues are allowed
    only in the forked output and its echoes, and the sandbox cues are built to avoid these words, so every hit is
    worth reading."""
    from inspect_ai.log import read_eval_log

    from fork.fork import target_conversation
    from fork.tails import forked_messages, load_prefix, prefix_id
    from study import load_tails

    for path in (tails_path, prefix_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing scan input: {path}")
    for log_dir in log_dirs:
        if not log_dir.is_dir() or not any(log_dir.glob("*.eval")):
            raise ValueError(f"No .eval logs to scan in {log_dir}")
    tails = load_tails(tails_path)
    prefix, _, fork_index = load_prefix(prefix_path)
    pid = prefix_id(prefix_path)
    if any(t["prefix_id"] != pid for t in tails):
        raise ValueError(f"{tails_path} continues a different conversation than {prefix_path}; pass --prefix")
    records = []
    for log_dir in log_dirs:
        for path in sorted(log_dir.glob("*.eval")):
            for smp in read_eval_log(str(path), resolve_attachments=True).samples or []:
                msgs, _ = target_conversation(smp)
                text = seen_text([m.model_dump(mode="json") for m in msgs])
                records.append({"kind": log_dir.name, "run": f"{path.stem}#epoch{smp.epoch}", "hits": find_banned(text)})
    for t in tails:
        replay = [m.model_dump(mode="json") for m in forked_messages(prefix, fork_index, t["branch"])]
        records.append({"kind": "tail", "run": f"{t['branch']}#{t['index']}",
                        "hits": find_banned(seen_text(replay + t["messages"]))})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in records))
    for kind in sorted({r["kind"] for r in records}):
        sub = [r for r in records if r["kind"] == kind]
        print(f"{kind}: {len(sub)} scanned, {sum(bool(r['hits']) for r in sub)} with a banned word")
    for r in records:
        if r["hits"]:
            print(f"  {r['kind']} {r['run']}: {r['hits'][:5]}")
    print(f"wrote {out}")
    return int(any(r["hits"] for r in records))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Scan what the target sees for banned frame words.")
    p.add_argument("files", nargs="*", help="files to scan; default: the seed and the four condition outputs")
    p.add_argument("--runs", action="store_true", help="scan Petri conversations and continuations instead")
    p.add_argument("--tails", type=Path, default=C.TAILS_PATH, help="with --runs: continuations to scan")
    p.add_argument("--prefix", type=Path, default=C.PREFIX_EXPORT, help="with --runs: the conversation they continue")
    p.add_argument("--log-dir", type=Path, action="append", help="with --runs: Petri log directory, repeatable")
    p.add_argument("--output", type=Path, default=C.RESULTS_DIR / "banned_scan.jsonl", help="with --runs: records file")
    a = p.parse_args(argv)
    if a.runs:
        from study import require_outside_data
        require_outside_data(p, a.output, C.DATA)
        try:
            return scan_runs(a.tails, a.prefix, a.log_dir or [C.PREFIX_LOG_DIR, C.H0_LOG_DIR], a.output)
        except (OSError, ValueError) as error:
            p.error(str(error))
    if a.files:
        clean = [scan(f, Path(f).read_text()) for f in a.files]
        return 0 if all(clean) else 1
    from fork.canned import build_branch_output

    ok = scan(str(C.SEED_FILE), C.SEED_FILE.read_text(), extra_words=("hypothesis",))
    for branch in C.BRANCHES:
        ok = scan(f"branch {branch}", build_branch_output(branch)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
