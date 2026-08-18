---
name: agentlab
description: >
  对已经存在的 Skill 或 agent 工作流目录做对照实验：一起商量怎么改，
  再按你关心的标准比较改前和改后（快慢、花费、该有的结果有没有漏、改完还能不能用）。
  评测 agent、对比改法时用。不要用于从零创建 SKILL.md。
---

# AgentLab

帮用户把「想改 Skill / agent 的哪一项能力」谈清楚，一起定实验方案，你来改、跑对照、按关注点讲改前改后有没有变好、改完还能不能用。对象是已经存在的目录（Skill 或自己写的工作流），不是从零新建一个 skill。

分数只认 `run` 写下来的结果。不要让用户去翻 yaml 或自己敲命令。

## 定位本 skill 和 Python

`SKILL.md` 所在目录就是 skill 目录。CLI 是 `scripts/cli.py`。先解析出绝对路径，不要把占位符当字面量留下。

解释器**默认自己解决**，不要为这个问用户。执行：

```text
<任意本机 python3> <skill-dir>/scripts/ensure_python.py
```

stdout 第一行就是要用的解释器（3.11+，已能 import pydantic / yaml）。脚本会：先用已就绪的 `AGENTLAB_PYTHON` / `python3.12` / `python3.11` / 合格的 `python3`；都不行就在 `~/.agentlab/.venv`（或 `$AGENTLAB_HOME/.venv`）建隔离环境并只往那里装依赖。**禁止**对系统 Python 执行 `pip install`。

只有 `ensure_python.py` 退出非 0（机器上没有 3.11+）时，才问用户从哪装一个 3.11+。用户若主动指定 venv，设 `AGENTLAB_PYTHON` 后再跑同一脚本。

之后一律：

```text
<py> <skill-dir>/scripts/cli.py brief --exp <实验目录> --confirm-criteria
<py> <skill-dir>/scripts/cli.py run --exp <实验目录> --gate
<py> <skill-dir>/scripts/cli.py report --exp <实验目录>
```

不要让用户去装全局 `agentlab`，也不要假设 PATH 里有它。

## 对用户

只问还缺的事实：测哪个目录、打算改哪里、在乎什么、本机哪条已经能登录使用的命令、任务说明怎么喂给这条命令（默认从标准输入读；用户没确认不要猜 `claude`/`codex`）、夹具在哪、预算大概多少。

**同一条回复里**写出本次实验计划（不要先问一轮、等回复、再另发计划）。计划必须写清：

- 对照什么（现在的目录对这次改法）
- 几个用例、每个重复几次（见下）
- 是否并行、为什么
- 看哪些关注点、怎样算过
- 产物放哪

没有用户确认（或回复里明确改计划）之前，不要 `run`。`brief` 可以在他确认后立刻做。

改动会拷到这次试验自己的目录里再跑，不会装进他的全局 skills。确认标准时用几句话复述「看什么、怎样算过」。不要让他打开或编辑 yaml。

讲结果时按「每条关注点在每个格子上过没过、改完还能不能用」。不要甩文件清单当作业。

## 规模与并行（计划里必须写明）

外搜、真 CLI、LLM 裁判这类有噪声的对照：**不要默认 1 个用例 × 1 次重复**。默认提案是 **至少 3 次重复**（同一用例），或 **至少 2 个用例且各重复 ≥2**。确定性假 command 才可以用 1×1。

能并行就并行（`budget.max_parallel` ≥ 格子数与重复的合理并发，默认 2～4）。只有用户要求、或会抢同一登录/额度时才改成串行，并在计划里写原因。

用户可以说「再多跑几次」或「这次先试 1 次」；按他的话改 `repetitions` / 用例数后再跑。

## 你自己做

契约字段见 `references/contract.md`。用户确认后，在实验目录里写齐这些再跑 `brief`：

- `experiment.yaml`（按契约草稿，含其中的 repetitions / max_parallel / max_trials）
- `criteria.md`（按关注点分节）
- `cases/<id>/prompt.md`
- `variants/baseline/`：源目录完整拷贝
- `variants/<treatment-id>/`：再拷一份 baseline，只在副本上改，不要改用户的源目录

新实验目录：源在某个 git 仓里 → `<仓根>/experiments/<id>/`；否则 → `<cwd>/experiments/<id>/`。已有同名且已是本实验则接着用；撞车则 `<id>-2`。

用户确认后跑 `brief --confirm-criteria`。退出 0 再 `run --gate`（他说这次只比较、先不判断能不能用，就去掉 `--gate`）。跑完 `report`，读 `report.md` / `promotion.json`，按关注点讲。不要自己编分数。

`brief` 退出 2：读错误码，你改契约或再问缺的那一项，不要把栈甩给用户。`run --gate` 退出 1：按报告讲哪条门禁没过。退出 3：预算到了，实验没跑完。

目录像交易/研报 Skill（产物在 `$SCUTIO_HOME/cache/trading`、报告有方向和动作）时，再读 `references/trading.md`。其它目录不要套那份关注点。

## 禁止

- 自己执行 `git worktree add`（要比工作区改动时在契约里写 `isolation.type: git-worktree`，由 runner 建沙盒）
- 写用户全局 skill 目录（`~/.claude/skills` 等）
- 为了打分去 exec 被测 command
- 跳过 brief 宣称可跑
- 计划未讲清规模/并行就开跑
- 自动一轮轮改目录刷分；他说再试一处时最多加 1 个 treatment，再走 brief + run
