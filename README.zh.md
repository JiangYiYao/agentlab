# AgentLab

[English](README.md) · [中文](README.zh.md)

改完一份 Skill（或自己写的工作流目录）后，用本机已经能跑的命令，按你在乎的几件事比改前改后，看这次能不能发。

这是一个标准 skill：把 [`skills/agentlab/`](skills/agentlab/) 拷进你的 coding agent 即可。CLI 在 skill 里面（`scripts/cli.py`），对话里的 agent 会自己执行它。

MIT。Python 3.11+。仅 macOS / Linux。

## 你怎么用

1. 把整个 `skills/agentlab/` 拷到 agent 的 skills 目录（和这份 `SKILL.md` 同级的文件夹都要带上）。
2. 本机有 Python 3.11+，并执行 `pip install pydantic pyyaml`。
3. 在对话里说要测哪个目录。

例如：

> 测一下 `~/code/scutio/skills/scutio-trading`。希望时间和 token 降下来，方向和动作标签还要和原来一致，报告里仍要有「结论 / 依据 / 改变判断的条件」，产物还是写在 `$SCUTIO_HOME/cache/trading`。我这台机器用 `claude --print`。用仓库里的合成回放数据，别去拉实时新闻。

它可能还会问 prompt 怎么传、预算多少、要不要把某条改法做成一个候选。你确认测评标准之后，它会把实验写到 `experiments/<id>/`，跑完再逐条讲门禁。

分数以这次跑出来的结果为准。想再改一处对比，直接说要试哪一处。

没有 `SKILL.md` 的目录也可以：启动命令指到你的程序即可。

Agent 实际执行的是：

```text
python3 <skills/agentlab>/scripts/cli.py brief --exp <实验目录> --confirm-criteria
python3 <skills/agentlab>/scripts/cli.py run --exp <实验目录> --gate
python3 <skills/agentlab>/scripts/cli.py report --exp <实验目录>
```

## 这个 skill 里有什么

```text
skills/agentlab/
  SKILL.md                 立项说明（给 agent 读）
  scripts/cli.py           命令入口
  scripts/agentlab/        实现
  references/case2.md     交易 skill 的推荐关注点
```

被测的那个 Skill 不会被拷进 `~/.claude/skills`。要让模型按 Skill 做事，prompt 里写阅读 `${program_root}/SKILL.md`。

实验跑完后，目录里会有 `experiment.yaml`、`criteria.md`、候选和 `report.md`。这些文件留在你本机（仓库不发布示例实验树）。

## 开发这个仓库 / CI

在仓库根目录：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

`pip install -e .` 也会在 PATH 里提供 `agentlab`，和 `scripts/cli.py` 是同一套代码。立项之后 `experiment.yaml` 里会有 `criteria.sha256`。

`--gate` 退出码：`0` 可以过门禁，`1` 有候选没过，`2` 契约不合法，`3` 预算到了但没跑完。

| 子命令 | 作用 |
|---|---|
| `brief` | 检查契约。`--confirm-criteria` 写入 `criteria.sha256`。 |
| `run [--gate]` | 隔离、执行命令、打分；`--gate` 再判定能不能发。 |
| `report` | 写出 `report.md`。 |
| `promote --only-variant ID` | 更新 `promotion.json`。 |

当前没有 Windows、Docker 隔离和网页报告。用真 `claude` / `codex` 跑历史行情也不在这个例子里。
