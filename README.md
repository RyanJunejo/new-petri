# new-petri

A reproducible study of how artificial-looking tool results affect an AI agent's adherence to an explicit no-delete rule, built with Petri.

**Research question:** Do cues that the environment is fake make Claude Opus 5.5 break an explicit no-delete rule?

By Ryan Junejo. Read the [report](report/report.pdf) ([Markdown](report/report.md)) and [technical appendix](report/appendix.pdf) ([Markdown](report/appendix.md)).

The experiment in one paragraph. A Petri-generated billing-maintenance conversation is shared up to the agent's first read of the protected customer records. The tool result at that point is replaced by one of four versions: production-looking, or carrying 2, 4 or 6 cumulative test-fixture cues. Each version is continued 50 times through the provider API with deterministic tool replies, and every continuation is scored for a forbidden SQL call (a deterministic predicate) and for statements that the environment is artificial (a judge that sees only the agent's own messages). A separate check shows each replaced output to fresh model instances without the task.

Versions: Python 3.12, inspect-petri 3.0.11, inspect-ai 0.3.281, pinned in `requirements.txt`. Target `anthropic/claude-opus-5-5`, auditor `openai/gpt-6.1-sol`, judges `openai/gpt-5.6-sol` (`config.py`).

The saved study contains 200 continuations: no forbidden SQL calls occurred, and all 200 requested approval. The strongest cue bundle prompted artificiality statements in 49 of 50 continuations, versus 0 of 50 in each other condition. See the report for uncertainty intervals and limitations.

## Reproduce every number without an API key

```bash
git clone https://github.com/RyanJunejo/new-petri.git
cd new-petri
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt pytest
.venv/bin/python fork/tails.py --selftest      # predicate, scripted replies, echoes, resume rules
.venv/bin/python analysis/analysis.py --check  # Wilson and Newcombe intervals against statsmodels
.venv/bin/python analysis/analysis.py          # every count and interval in the report; tables under results/
.venv/bin/python -m pytest tests               # end-to-end checks of the workflow on saved data
```

`analysis.py` reads only `data/`, filtering continuations by the conversation and code identifiers recorded in each record, so the saved study is reported as it ran.

## How the experiment was run

Activate the environment with `source .venv/bin/activate`, copy `.env.example` to `.env`, and add your provider keys. These commands make paid API calls and require access to the model IDs in `config.py`; the offline analysis above does not.

1. `python petri/run_prefix.py` generates Petri conversations; `--report LOG.eval` shows which meet the selection conditions (system prompt served verbatim, consequence shown before the intervention point, no destructive call, protected rows read in a single statement).
2. `python fork/fork.py LOG.eval --sample 0 --output runs/prefix_export.json` exports the shared conversation; replace `LOG.eval` and the sample index with an eligible conversation from step 1. `python fork/fork.py --branches --output runs/branches` builds and checks the condition outputs.
3. `python fork/tails.py --prefix runs/prefix_export.json --n 50 --output runs/tails.jsonl` runs the continuations, interleaved across conditions; rerunning the same command resumes it.
4. `python checks/isolated_evidence.py --n 20 --output runs/isolated_evidence.jsonl` runs the fresh-instance classification check.
5. `python petri/run_prefix.py --h0` generates the additional cue-free conversations; `python judges/h0_judge.py --log-dir runs/prefix_h0 --output runs/h0.jsonl` judges them.
6. `python judges/frame_judge.py --tails runs/tails.jsonl --output runs/frame.jsonl` scores the situation labels.
7. `python checks/banned_words.py --runs --prefix runs/prefix_export.json --tails runs/tails.jsonl --log-dir runs/prefix --log-dir runs/prefix_h0` scans everything the target saw for banned framing words. Use `python checks/banned_words.py --runs` to scan the saved study instead.

New runs go under `runs/` and never overwrite `data/`. `python analysis/analysis.py --tails runs/tails.jsonl` reports a new run's actions without reusing the study's judge labels. `python build_zip.py` creates `dist/new-petri.zip` from an explicit list of code and data files; it refuses to build if anything credential-like is found.

## Layout

- `config.py` paths, models, sample sizes, condition definitions and cue bundles; the single source of every setting.
- `seeds/billing_dedupe.md` the Petri seed: tools, system prompt, user message and the scripted environment.
- `petri/run_prefix.py` Petri conversation runner and eligibility report.
- `fork/fork.py` conversation export and condition outputs; `fork/canned.py` scripted tool replies and the forbidden-call predicate; `fork/tails.py` the continuation runner.
- `judges/` the two judges and the target-turns-only message view they share; `judges/prompts/` their prompts.
- `checks/` banned-word scan and fresh-instance check.
- `analysis/analysis.py` numbers and tables; `study.py` shared rules for reading and resuming recorded runs.
- `data/` the saved study: the Petri logs, the exported conversation, the 200 continuations, judge outputs, the check and the hand codes.
- `tests/test_workflow.py` end-to-end checks on the saved data.
- `report/` report and appendix (markdown and PDF) and the design figure.

## Public release and data

The billing company, people and customer records are fictional scenario fixtures. Realistic-looking names, email addresses and phone numbers are part of the experimental stimuli; no customer database was used.

This release includes the code, prompts, saved observations, reports and figures. Local workspace paths and private Git provenance have been removed from the Inspect log metadata. The recorded conversations, sample content and run identifiers are unchanged. Workspace configuration, intermediate report formats and the source repository's Git history are not included.
