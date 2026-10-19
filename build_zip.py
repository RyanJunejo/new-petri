"""Build the code-and-data ZIP: python build_zip.py.

Every included file is named explicitly. Report deliverables are kept separately.
"""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE_ROOT = "new-petri"
OUT = ROOT / "dist" / f"{ARCHIVE_ROOT}.zip"
FILES = (
    "README.md", ".env.example", ".gitignore", "requirements.txt", "ruff.toml", "config.py", "study.py", "build_zip.py",
    "analysis/__init__.py", "analysis/analysis.py",
    "checks/__init__.py", "checks/banned_words.py", "checks/isolated_evidence.py",
    "fork/__init__.py", "fork/canned.py", "fork/fork.py", "fork/tails.py",
    "judges/__init__.py", "judges/frame_judge.py", "judges/h0_judge.py", "judges/target_view.py",
    "judges/prompts/frame.md", "judges/prompts/h0.md",
    "petri/__init__.py", "petri/run_prefix.py", "seeds/billing_dedupe.md",
    "data/prefix_export.json", "data/tails.jsonl",
    "data/prefix/2026-10-16T02-33-05-00-00_audit_FdzTwCFaSEmGUJykxcVvnd.eval",
    "data/prefix_h0/2026-10-16T07-43-25-00-00_audit_95ccHnkzGbyc9PXPmtRgzA.eval",
    "data/judges/frame.jsonl", "data/judges/h0_prefix.jsonl", "data/judges/h0_prefix_h0.jsonl",
    "data/checks/isolated_evidence.jsonl", "data/checks/hand_codes.csv",
    "tests/test_workflow.py",
)
KEY_PATTERNS = tuple(re.compile(p) for p in (
    r"sk-[A-Za-z0-9_-]{16,}", r"AKIA[0-9A-Z]{16}",
    r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z_]*(?:KEY|TOKEN|SECRET)[ \t]*=[ \t]*[\"']?[^\s\"']+",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
))
# Exact illustrative key in inspect-petri 3.0.11's send_tool_call_result description.
# It is present in the original logs and in that package's public source.
PETRI_EXAMPLE_KEY_SHA256 = "a8220c889208fba40af0dde45920189b0c8577fa362776f3542dbbd6aeaf14e7"


def credential_text(path: Path) -> str:
    """Inspect logs use compressed ZIP members; scan the decoded log and its attachments."""
    if path.suffix == ".eval":
        from inspect_ai.log import read_eval_log
        return read_eval_log(str(path), resolve_attachments=True).model_dump_json()
    return path.read_text(encoding="utf-8")


def files_to_ship() -> list[Path]:
    paths = [ROOT / name for name in FILES]
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing file or symlink: {path.relative_to(ROOT)}")
    return paths


def scan_for_keys(paths: list[Path]) -> None:
    secrets = [value for name, value in os.environ.items()
               if re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD)", name, re.IGNORECASE) and len(value) >= 12]
    for path in paths:
        text = credential_text(path)
        matches = (m.group() for pattern in KEY_PATTERNS for m in pattern.finditer(text))
        suspicious = any(hashlib.sha256(value.encode()).hexdigest() != PETRI_EXAMPLE_KEY_SHA256
                         for value in matches)
        if suspicious or any(value in text for value in secrets):
            raise ValueError(f"Credential-like content in {path.relative_to(ROOT)}; ZIP not built")


def main() -> None:
    paths = files_to_ship()
    scan_for_keys(paths)
    OUT.parent.mkdir(exist_ok=True)
    temporary = OUT.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in paths:
                # Fixed metadata makes repeated builds byte-identical.
                info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{path.relative_to(ROOT)}", (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        temporary.replace(OUT)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"wrote {OUT} ({len(paths)} files, {OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
