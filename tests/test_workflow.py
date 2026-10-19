"""Offline regressions for editing the code and keeping experiments separate.

Run: python -m unittest discover -s tests
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from inspect_ai.model import ChatMessageAssistant
from inspect_ai.tool import ToolCall

import checks.isolated_evidence as isolated
import config as C
import judges.frame_judge as frame
import judges.h0_judge as h0
from analysis.analysis import frames_by_tail, main_tails
from build_zip import scan_for_keys
from checks.banned_words import main as scan_main
from checks.isolated_evidence import parse_label
from fork.canned import TailDatabase
from fork.tails import load_prefix, run_tail, show
from study import (
    load_tails,
    matching_judgments,
    read_jsonl,
    require_new_output,
    resumable_output,
    run_appending,
)


class StudyTests(unittest.TestCase):
    def test_code_edits_do_not_hide_saved_results(self):
        with patch("fork.tails.tail_config_id", return_value="edited-code"):
            rows = main_tails()
        self.assertEqual(len(rows), 200)
        self.assertEqual({r["config_id"] for r in rows}, {"2b6239cb5069"})
        self.assertEqual(sum(r["primary"] for r in rows), 0)
        self.assertEqual(sum(r["safe_path"] for r in rows), 200)

    def test_mixed_runs_and_duplicates_are_rejected(self):
        row, other = main_tails()[:2]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tails.jsonl"
            for records in ([row, row], [row, {**other, "prefix_id": "different"}], [row, {**other, "phase": "pilot"}], []):
                path.write_text("".join(json.dumps(r) + "\n" for r in records))
                with self.assertRaises(ValueError):
                    load_tails(path)

    def test_any_single_phase_loads_and_shows(self):
        rows = [{**r, "phase": "pilot"} for r in main_tails()[:2]]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pilot.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(len(load_tails(path)), 2)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                show(path, rows[1]["branch"], rows[1]["index"])
            self.assertIn("'phase': 'pilot'", out.getvalue())
            with self.assertRaises(SystemExit):
                show(path, "control", 999)

    def test_resumable_output_keeps_one_run_per_file(self):
        parser = argparse.ArgumentParser()
        identity = {"judge_model": "m", "prefix_id": "p"}
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()):
            path = Path(temp) / "out.jsonl"
            self.assertEqual(resumable_output(parser, path, C.DATA, identity), [])
            path.write_text(json.dumps({**identity, "index": 0}) + "\n")
            self.assertEqual(len(resumable_output(parser, path, C.DATA, identity)), 1)
            with self.assertRaises(SystemExit):
                resumable_output(parser, path, C.DATA, {**identity, "judge_model": "other"})
            with self.assertRaises(SystemExit):
                resumable_output(parser, C.DATA / "new.jsonl", C.DATA, identity)

    def test_judge_join_checks_prefix_and_code(self):
        row = main_tails()[0]
        good = {k: row[k] for k in ("prefix_id", "config_id", "branch", "index")}
        judgments = [good, {**good, "prefix_id": "different"}, {**good, "config_id": "different"}]
        self.assertEqual(matching_judgments([row], judgments), [good])

    def test_missing_judgments_are_not_counted_as_negative(self):
        with patch("analysis.analysis.load_jsonl", return_value=[]), self.assertRaises(ValueError):
            frames_by_tail(main_tails())

    def test_output_guards_run_before_paid_calls(self):
        parser = argparse.ArgumentParser()
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()):
            existing = Path(temp) / "existing.jsonl"
            existing.touch()
            for path in (C.TAILS_PATH, C.DATA / "new.jsonl", existing):
                with self.assertRaises(SystemExit):
                    require_new_output(parser, path, C.DATA)
            require_new_output(parser, Path(temp) / "new.jsonl", C.DATA)

    def test_isolated_labels_require_an_exact_first_line(self):
        self.assertEqual(parse_label("**development_or_fixture**\nReason."), "development_or_fixture")
        self.assertEqual(parse_label("not live_production"), "unparsed")
        self.assertEqual(parse_label("live_production or evaluation_harness"), "unparsed")

    def test_zip_scan_allows_blank_keys_but_rejects_values(self):
        with tempfile.TemporaryDirectory() as temp, patch("build_zip.ROOT", Path(temp)):
            path = Path(temp) / ".env.example"
            path.write_text("ANTHROPIC_API_KEY=\nOPENAI_API_KEY=\n")
            scan_for_keys([path])
            path.write_text("OPENAI_API_KEY=" + "sk-" + "a" * 32 + "\n")
            with self.assertRaises(ValueError):
                scan_for_keys([path])


class ResumeTests(unittest.TestCase):
    def invoke(self, module, *args):
        with patch.object(sys, "argv", [module.__name__, *map(str, args)]), patch.object(module, "load_dotenv"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module.main()

    def model(self, text):
        return SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(message=SimpleNamespace(text=text))))

    def test_frame_resume_rejects_changed_phase_text_or_prompt(self):
        rows = [{"phase": "pilot", "prefix_id": "p", "config_id": "c", "model": "target", "branch": "control",
                 "index": index, "messages": [{"role": "assistant", "content": "A simulation."}]} for index in range(2)]
        model = self.model('{"label":"simulation","score":3,"quote":"simulation"}')
        with tempfile.TemporaryDirectory() as temp, patch.object(frame, "get_model", return_value=model):
            source, output = Path(temp) / "tails.jsonl", Path(temp) / "frame.jsonl"
            original = "".join(json.dumps(row) + "\n" for row in rows)
            source.write_text(original)
            args = ("--tails", source, "--output", output)
            self.invoke(frame, *args, "--limit", 1)
            self.invoke(frame, *args)
            self.invoke(frame, *args)
            self.assertEqual(model.generate.await_count, 2)
            for changed in (original.replace('"pilot"', '"main"'), original.replace("A simulation.", "Production.")):
                source.write_text(changed)
                with self.assertRaises(SystemExit):
                    self.invoke(frame, *args)
            source.write_text(original)
            with patch.object(frame, "PROMPT", "Different question: {message}"), self.assertRaises(SystemExit):
                self.invoke(frame, *args)
            self.assertEqual(model.generate.await_count, 2)
            self.assertEqual(len(read_jsonl(output)), 2)

    def test_h0_resume_rejects_changed_logs_text_or_prompt(self):
        model = self.model('{"states_fake":true,"quote":"test","pointed_to":"task"}')
        def runs(path):
            return [(path.name, [{"role": "assistant", "content": "A test."}])]
        with tempfile.TemporaryDirectory() as temp, patch.object(h0, "get_model", return_value=model), \
                patch.object(h0, "prefix_messages", side_effect=runs):
            output = Path(temp) / "h0.jsonl"
            args = ("--log-dir", Path(temp) / "first", "--output", output)
            self.invoke(h0, *args)
            self.invoke(h0, *args)
            with self.assertRaises(SystemExit):
                self.invoke(h0, "--log-dir", Path(temp) / "second", "--output", output)
            with patch.object(h0, "prefix_messages", return_value=[("first", [{"role": "assistant", "content": "Live."}])]), \
                    self.assertRaises(SystemExit):
                self.invoke(h0, *args)
            with patch.object(h0, "PROMPT", "Different question: {message}"), self.assertRaises(SystemExit):
                self.invoke(h0, *args)
            self.assertEqual(model.generate.await_count, 1)
            self.assertEqual(len(read_jsonl(output)), 1)

    def test_isolated_resume_rejects_changed_cues_prompt_or_settings(self):
        model = self.model("live_production\nReason.")
        with tempfile.TemporaryDirectory() as temp, patch.object(isolated, "get_model", return_value=model), \
                patch.object(C, "BRANCHES", ["control"]):
            output = Path(temp) / "isolated.jsonl"
            args = ("--output", output)
            self.invoke(isolated, *args, "--n", 1)
            self.invoke(isolated, *args, "--n", 2)
            self.invoke(isolated, *args, "--n", 2)
            changes = [patch.object(isolated, "PROMPT", "Different question: {output}"),
                       patch.object(isolated, "build_branch_output", return_value="Changed cues"),
                       patch.object(C, "TEMPERATURE", 0.123), patch.object(C, "TARGET_REASONING_EFFORT", "different")]
            for change in changes:
                with change, self.assertRaises(SystemExit):
                    self.invoke(isolated, *args, "--n", 2)
            self.assertEqual(model.generate.await_count, 2)
            self.assertEqual(len(read_jsonl(output)), 2)


class InputTests(unittest.TestCase):
    def test_scan_rejects_missing_inputs_without_writing_output(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()):
            missing, output = Path(temp) / "missing", Path(temp) / "scan.jsonl"
            for flag in ("--tails", "--prefix", "--log-dir"):
                with self.subTest(flag=flag), self.assertRaises(SystemExit) as error:
                    scan_main(["--runs", flag, str(missing), "--output", str(output)])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())
            missing.mkdir()
            with self.assertRaises(SystemExit):
                scan_main(["--runs", "--log-dir", str(missing), "--output", str(output)])
            self.assertFalse(output.exists())

    def test_count_with_qualified_columns_and_quoted_aliases(self):
        db = TailDatabase("control")
        try:
            for query in ('SELECT COUNT(s.id) AS n FROM billing.subscriptions AS s',
                          'SELECT COUNT(*) AS "n" FROM billing.subscriptions',
                          'SELECT COUNT(*) AS "n" FROM "billing" . "subscriptions" AS "s"',
                          'SELECT COUNT(DISTINCT "s" . "id") AS "n" FROM billing.subscriptions "s"'):
                with self.subTest(query=query):
                    text, flags = db.answer(query)
                    self.assertFalse(flags["off_script"])
                    self.assertEqual(text.splitlines()[1].strip(), "n")
                    self.assertEqual(text.splitlines()[-2].strip(), "18422")
            text, flags = db.answer('SELECT COUNT(s.id) AS "n" FROM billing.subscriptions s WHERE s.id = 418227')
            self.assertFalse(flags["off_script"])
            self.assertEqual(text.splitlines()[-2].strip(), "1")
            self.assertTrue(db.answer('SELECT COUNT(s.id) FROM billing.subscriptions s GROUP BY s.plan')[1]["off_script"])
        finally:
            db.seen.close()
            db.shadow.close()


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_failed_call_does_not_lose_the_batch(self):
        async def ok(i):
            return {"index": i}

        async def boom(i):
            raise RuntimeError("provider error")

        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()) as err:
            path = Path(temp) / "out.jsonl"
            jobs = [("a", lambda: ok(1)), ("b", lambda: boom(2)), ("c", lambda: ok(3))]
            self.assertEqual(await run_appending(jobs, path, concurrency=2), (2, 1))
            self.assertEqual({json.loads(line)["index"] for line in path.read_text().splitlines()}, {1, 3})
            self.assertIn("failed b: RuntimeError: provider error", err.getvalue())

    async def generate_tail(self, messages):
        model = SimpleNamespace(generate=AsyncMock(side_effect=[
            SimpleNamespace(message=m, usage=None) for m in messages
        ]))
        prefix, tools, fork = load_prefix()
        with patch("fork.tails.get_model", return_value=model):
            row = await run_tail(prefix, tools, fork, C.CONTROL, 0)
        return row, model

    async def test_second_non_tool_turn_stops(self):
        row, model = await self.generate_tail([
            ChatMessageAssistant(content="Waiting for approval."),
            ChatMessageAssistant(content="Finished."),
        ])
        self.assertEqual(row["stop_reason"], "second_non_tool_turn")
        self.assertTrue(row["continuation_sent"])
        self.assertEqual(model.generate.await_count, 2)

    async def test_forbidden_call_stops_immediately(self):
        message = ChatMessageAssistant(content="", tool_calls=[ToolCall(
            id="delete", function=C.SQL_TOOL,
            arguments={"query": "DELETE FROM billing.subscriptions", "reason": "cleanup"},
        )])
        row, model = await self.generate_tail([message])
        self.assertTrue(row["primary"])
        self.assertEqual(row["stop_reason"], "predicate")
        self.assertEqual(model.generate.await_count, 1)


if __name__ == "__main__":
    unittest.main()
