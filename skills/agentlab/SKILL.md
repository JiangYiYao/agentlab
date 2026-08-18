---
name: agentlab
description: 测一份 Agent 目录改动能不能发。问清必要问题后自己跑本 skill 的 scripts/cli.py（brief / run / report），按关注点说明结果。不要让用户找 yaml 或自己敲命令。
---

# AgentLab

跟用户把实验问清楚，你自己写文件、自己跑完、按关注点讲能不能发。分数只认 `run` 写下来的结果。

## 定位本 skill 和 Python

`SKILL.md` 所在目录就是 skill 目录。CLI 是 `scripts/cli.py`。先解析出绝对路径，再拼命令，不要把 `<skill-dir>` 当字面量留下。

用 **3.11+** 的解释器。按顺序试 `python3.12`、`python3.11`，再试 `python3`（先 `--version`，低于 3.11 就丢掉）。对选中的解释器执行：

```text
<py> -c "import pydantic, yaml"
```

缺解释器或缺包时，问用户用哪套已经装好依赖的环境（venv 路径或可执行文件）。**不要**自行对系统 Python 执行 `pip install`。用户点名某个 venv 后，只用那个 venv 的 `python`。

之后一律：

```text
<py> <skill-dir>/scripts/cli.py brief --exp <实验目录> --confirm-criteria
<py> <skill-dir>/scripts/cli.py run --exp <实验目录> --gate
<py> <skill-dir>/scripts/cli.py report --exp <实验目录>
```

不要让用户去装全局 `agentlab`，也不要假设 PATH 里有它。

## 对用户

只问还缺的事实：测哪个目录、在乎什么、本机哪条已经能登录使用的命令、任务说明怎么喂给这条命令（默认从标准输入读；没点头不要猜 `claude`/`codex`）、夹具在哪、预算大概多少。

用对方能懂的话说：改动会拷到这次试验自己的目录里再跑，不会装进他的全局 skills。

确认测评标准时，用几句话复述「看什么、怎样算过」，等他点头。不要让他去打开或编辑 yaml。

讲结果时按「每条关注点在每个格子上过没过、能不能发」。需要改一处再比，他说试哪处即可。不要甩文件清单当作业。

## 你自己做

契约怎么写见 `references/contract.md`。先把源目录完整拷到 `variants/baseline/`，用户勾选的改法再做成 treatment 目录。

新实验目录：源在某个 git 仓里 → `<仓根>/experiments/<id>/`；否则 → `<cwd>/experiments/<id>/`。已有同名且已是本实验则接着用；撞车则 `<id>-2`。

用户确认标准后跑 `brief --confirm-criteria`。退出 0 再 `run --gate`（他说先不管能不能发就去掉 `--gate`）。跑完 `report`，读 `report.md` / `promotion.json`，按关注点讲。不要自己编分数。

`brief` 退出 2：读错误码，你改契约或再问缺的那一项，不要把栈甩给用户。`run --gate` 退出 1：按报告讲哪条门禁没过。退出 3：预算到了，实验没跑完。

目录像交易/研报 Skill（产物在 `$SCUTIO_HOME/cache/trading`、报告有方向和动作）时，再读 `references/trading.md`。其它目录不要套那份关注点。

## 禁止

- `git worktree add`
- 写用户全局 skill 目录（`~/.claude/skills` 等）
- 为了打分去 exec 被测 command
- 跳过 brief 宣称可跑
- 自动一轮轮改目录刷分；他说再试一处时最多加 1 个 treatment，再走 brief + run
