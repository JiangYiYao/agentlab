# AgentLab

[中文](README.md) · English

**Test and compare existing AI skills or workflows to see whether a change helps.**

Did a prompt edit improve the answers? Does a new workflow save time? Does it still work when you run the same task again? AgentLab tests versions against the same cases and records their outputs, timing, and available usage data so you can decide whether to adopt a change.

AgentLab is itself a skill. Once installed, tell your agent what you want to test. It prepares the experiment, runs it, and walks you through the results. Everyday use doesn't require writing configuration files or running CLI commands yourself.

## Install

You need **macOS or Linux, Python 3.11+**, and a coding agent that supports local skills. The workflow under test also needs a command that runs on your machine, with its tools and authentication already available.

1. Download or clone this repository.
2. Copy the entire [`skills/agentlab/`](skills/agentlab/) folder into your agent's skills directory, including `scripts/` and `references/`.
3. Load the skill as your agent requires, then ask it to use AgentLab in chat.

The bundled setup script prepares Python dependencies, using a separate virtual environment when needed. No global `agentlab` installation is required.

## Start with a request

**Compare existing versions:**

> Use AgentLab to compare `./skills/summarizer-v1` and `./skills/summarizer-v2`. Use the same documents and run each case three times. Check for missing key information and compare execution time.

**Check consistency:**

> Use AgentLab to test `./skills/data-extractor` with several input formats. Repeat the runs and check that the output is valid JSON with all required fields.

**Discuss an improvement before testing it:**

> I want to improve `./skills/code-review` by reducing duplicate checks while keeping important findings. Propose a change first, then compare it with the original once we've agreed on the approach.

You can also test agent workflows that run through a command. Choose tasks, inputs, and evaluation criteria that reflect your own work.

## What happens next

The agent reads the relevant directories and proposes a concrete test plan: versions, cases, repetitions, concurrency, and evaluation criteria. If model reviewers are needed, the plan includes how they will review the outputs and how many calls they will make. The agent asks for any missing command or input details.

Execution starts after the plan is confirmed. Candidate copies live in the experiment directory; coding tasks can use separate Git worktrees. Scripts check concrete requirements such as file presence, valid JSON, and passing tests. Model reviewers can assess qualities that require judgment.

You can set time, money, or token limits; none are set by default. Execution time is recorded. Cost and token reporting depend on data provided by the commands you use; unavailable usage is marked as unknown.

## Read the results and inspect the evidence

After the run, the agent explains the results against your priorities: which requirements passed, which failed, which version performed better, and where the evidence is still insufficient.

Experiments are saved under `~/.agentlab/experiments/` by default. You can choose another location.

| Content | Location within the experiment |
|---|---|
| Results, scores, and timing by version | `report.md` |
| Settings and criteria used in that run | `runs/<run_id>/manifest.json`, `runs/<run_id>/criteria.md` |
| Outputs, logs, and scoring evidence for each trial | `runs/<run_id>/trials/` |
| Prompts, raw responses, and score provenance | Each score's audit link in the report opens `runs/<run_id>/audit.html` |
| Investigating a case that performed worse | Each execution link opens `runs/<run_id>/execution.html` |
| File changes from coding tasks | `runs/<run_id>/diff.html` |

Records stay on your machine. Whether execution contacts external services depends on the commands and review models you choose.

If a score looks wrong, follow its audit link to inspect the task, criteria, supplied material, raw response, and parsed result. The page also compares the previous assessment, making prompt and scoring changes easier to spot. These records show what was provided; they do not prove that the model read every file.

## Continue without starting over

You can follow up in the same conversation:

> Keep the execution results and score them again with a different review model.

> Add a set of long-document cases to see whether the new version still helps.

> Run everything again this time, without reusing earlier results.

AgentLab checks candidate contents, task inputs, and execution settings before reusing results. Changing the evaluator can reuse saved outputs; changing only a pass threshold can reuse existing measurements. Historical runs retain their original settings and decisions.

Re-evaluation requires the relevant output material to have been saved. See the [execution and re-evaluation guide](skills/agentlab/references/lifecycle.md) for reuse rules, evidence settings, and compatibility with older runs.

## Further reading

The detailed guides are currently in Chinese:

- [Experiment workflow](skills/agentlab/references/workflow.md): planning, call counts and preparation.
- [Experiment configuration](skills/agentlab/references/contract.md): cases, commands, scoring, and budgets.
- [Coding tasks](skills/agentlab/references/coding.md): testing changes in real Git repositories.
- [Execution and re-evaluation](skills/agentlab/references/lifecycle.md): reuse, evidence retention, retries, and compatibility.
- [Storage and cleanup](skills/agentlab/references/storage.md): shared archives, disk usage, cache cleanup, and older experiments.
- [Auditing scores](skills/agentlab/references/audit.md): judge inputs, score provenance, and assessment comparisons.
- [Investigating execution](skills/agentlab/references/execution-audit.md): pre-launch inputs, exported traces, baseline comparisons, and limits on causal conclusions.
- [Skill instructions](skills/agentlab/SKILL.md): request routing, command entry points and reporting requirements.

## Script organization

The agent uses two files: `scripts/ensure_python.py` prepares Python, then `scripts/cli.py` handles every experiment operation through subcommands. You do not need to find or invoke the other Python files. Keep the whole Skill folder when installing it.

The internal `scripts/agentlab/` package groups code into `execution/` (scheduling, trials and capture), `evaluation/` (scoring and decisions), `records/` (history and storage), `reporting/` (reports and audits), `adapters/` (workspace isolation and artifact copying), and `compat/` (legacy extraction rules). Configuration models and validation stay at the package root.

Reports read archived records without importing the execution scheduler. Both audit pages share archive and page helpers; validation and execution share command and recipe resolution. The supported interface is the CLI; internal Python import paths can change. Experiment configuration, CLI arguments and archived run layouts are preserved by this reorganization.

## Local development

From the repository root, using Python 3.11+:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

After installing for development, run `agentlab --help` to explore the CLI. Main commands include `brief` (validate an experiment), `run` (execute), `rescore` (re-evaluate), `report` (generate a report), `status` (show progress), `storage` (show disk usage), and `cleanup` (remove workspaces and archived caches).

Licensed under [MIT](LICENSE).
