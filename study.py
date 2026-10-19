"""Read recorded runs without depending on the current source-code hash, plus the output rules shared by every
script that writes a run: nothing under data/, one experiment per file, and resumable appends."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path


def content_id(value: object) -> str:
    """Stable identity for the actual inputs and settings of an experiment."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def load_tails(path: Path) -> list[dict]:
    """Require one run identity (phase, prefix, code version, model) and unique (branch, index) pairs.

    Source edits must not hide saved observations, so the stored config_id is read, not recomputed.
    Different experiments belong in separate files; combining them would make counts and judge joins ambiguous.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"No continuations in {path}")
    identities = {(r.get("phase"), r.get("prefix_id"), r.get("config_id"), r.get("model")) for r in rows}
    if len(identities) != 1 or not all(next(iter(identities))):
        raise ValueError(f"Expected one phase, prefix, code version and model in {path}")
    keys = [(r["branch"], r["index"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate continuation indices in {path}")
    return rows


def matching_judgments(rows: list[dict], judgments: list[dict]) -> list[dict]:
    """Join judges to continuations by both recorded hashes as well as index."""
    keys = {(r["prefix_id"], r["config_id"], r["branch"], r["index"]) for r in rows}
    return [j for j in judgments
            if (j.get("prefix_id"), j.get("config_id"), j["branch"], j["index"]) in keys]


def require_outside_data(parser, path: Path, data_dir: Path) -> None:
    """Refuse to write under data/, which holds the saved study."""
    path, data_dir = path.resolve(), data_dir.resolve()
    if path == data_dir or data_dir in path.parents:
        parser.error("write new outputs outside data/, which contains the saved study")


def require_new_output(parser, path: Path, data_dir: Path) -> None:
    """For outputs that cannot be resumed: reject the saved study and any existing path before API calls."""
    require_outside_data(parser, path, data_dir)
    if path.exists():
        parser.error(f"output already exists: {path}; choose a new output path")


def resumable_output(parser, path: Path, data_dir: Path, identity: dict) -> list[dict]:
    """The records already in an output file that this run may append to. Every stored record must carry
    `identity` (the run's phase, hashes or model), so one file never holds two experiments."""
    require_outside_data(parser, path, data_dir)
    stored = read_jsonl(path)
    if any(r.get(k) != v for r in stored for k, v in identity.items()):
        parser.error(f"{path} holds a different run; choose a new --output file")
    return stored


async def run_appending(jobs: list[tuple[str, object]], path: Path, concurrency: int,
                        identity: dict | None = None) -> tuple[int, int]:
    """Run (label, coroutine factory) jobs with bounded concurrency, appending each record to `path` as it finishes.
    A failed call is reported on stderr instead of raised, so one error cannot lose the batch; rerunning the same
    command retries only what is missing. Returns (written, failed)."""
    sem = asyncio.Semaphore(concurrency)
    counts = {"written": 0, "failed": 0}

    async def one(label: str, make) -> None:
        async with sem:
            try:
                record = await make()
            except Exception as e:  # noqa: BLE001 - any provider or parsing error is reported, not fatal
                counts["failed"] += 1
                print(f"failed {label}: {type(e).__name__}: {e}", file=sys.stderr)
                return
        append_jsonl(path, {**record, **(identity or {})})
        counts["written"] += 1

    await asyncio.gather(*(one(label, make) for label, make in jobs))
    return counts["written"], counts["failed"]


def report_run(written: int, failed: int, skipped: int, path: Path) -> None:
    """Summarise a resumable run; exit non-zero if any call failed so a partial file is never mistaken for a complete one."""
    print(f"wrote {written} records to {path} ({skipped} already stored, {failed} failed)")
    if failed:
        raise SystemExit(f"{failed} calls failed; rerun the same command to retry them")
