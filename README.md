# AgentLab

[English](README.md) · [中文](README.zh.md)

Plan and run experiments on an existing Skill or agent workflow when you want one capability to get better — faster, cheaper, fewer missed facts, or fit to ship. This is not for scaffolding a new SKILL.md from scratch.

AgentLab is a standard skill. Copy [`skills/agentlab/`](skills/agentlab/) into your coding agent. The CLI lives in that folder (`scripts/cli.py`); the agent runs it.

MIT. Python 3.11+. macOS and Linux only.

## How you use it

1. Copy the whole `skills/agentlab/` directory into your agent’s skills folder (everything next to `SKILL.md`).
2. Use Python 3.11+ and `pip install pydantic pyyaml`.
3. In chat, say which directory you want to test.

For example:

> Test `~/code/scutio/skills/scutio-trading`. I want time and tokens down, with the same direction/action labels, the report still having 结论 / 依据 / 改变判断的条件, and outputs still under `$SCUTIO_HOME/cache/trading`. I use `claude --print` on this machine. Use the synthetic replay fixture in this repo; don’t fetch live news.

It may still ask how the prompt is passed, what the budget is, and which change to turn into a candidate. After you confirm the criteria it writes the experiment under `experiments/<id>/`, runs it, and goes through each gate.

Scores are whatever that run wrote down. To compare another change, say which one to try.

A directory without `SKILL.md` works the same way: point the command at your program.

What the agent actually runs:

```text
python3 <skills/agentlab>/scripts/cli.py brief --exp <experiment-dir> --confirm-criteria
python3 <skills/agentlab>/scripts/cli.py run --exp <experiment-dir> --gate
python3 <skills/agentlab>/scripts/cli.py report --exp <experiment-dir>
```

## What’s in the skill

```text
skills/agentlab/
  SKILL.md                 briefing instructions
  scripts/cli.py           command entry
  scripts/ensure_python.py isolate a 3.11+ interpreter
  scripts/agentlab/        implementation
  references/contract.md  experiment.yaml shape
  references/trading.md   only when the target is a trading skill
```

The Skill under test is not copied into `~/.claude/skills`. If the model should follow a Skill, the prompt tells it to read `${program_root}/SKILL.md`.

After a run, the experiment directory has `experiment.yaml`, `criteria.md`, the candidates, and `report.md`. Those files stay on your machine (the repo does not publish example experiment trees).

## Working on this repo / CI

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

`pip install -e .` also puts `agentlab` on PATH; it is the same code as `scripts/cli.py`. After briefing, `experiment.yaml` contains `criteria.sha256`.

`--gate` exit codes: `0` the candidate may pass, `1` a candidate failed, `2` the contract is invalid, `3` the budget stopped an incomplete run.

| Subcommand | What it does |
|---|---|
| `brief` | Check the contract. `--confirm-criteria` writes `criteria.sha256`. |
| `run [--gate]` | Isolate, run the command, score; `--gate` then decides ship / no-ship. |
| `report` | Write `report.md`. |
| `promote --only-variant ID` | Update `promotion.json`. |

Not included: Windows, Docker isolation, an HTML report. Real `claude` / `codex` runs on historical market data are not in this example.
