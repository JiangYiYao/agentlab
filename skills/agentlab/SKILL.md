---
name: agentlab
description: 立项并跑完 Agent 目录变体实验。问清必要问题后自己执行本 skill 的 scripts/cli.py（brief / run / report），按关注点说明能不能发。不要让用户去找中间文件或自己敲 CLI。
---

# AgentLab

你负责跟用户把实验问清楚并跑完。分数只认 `run` 写下来的结果。

## CLI

入口是**本 skill 目录**下的 `scripts/cli.py`（本文件所在文件夹就是 skill 目录）。始终自己 exec：

```text
python3 <skill-dir>/scripts/cli.py brief --exp <dir> --confirm-criteria
python3 <skill-dir>/scripts/cli.py run --exp <dir> --gate
python3 <skill-dir>/scripts/cli.py report --exp <dir>
```

不要让用户去装全局命令，也不要假设 PATH 里有 `agentlab`。若报缺 `pydantic` / `pyyaml`，先 `pip install pydantic pyyaml` 再跑。

## 必须做

1. 问清：测哪个目录、在乎什么、本机哪条已经能用的 command、夹具在哪、预算。
2. 只写实验目录：`brief.md`、`experiment.yaml`、`criteria.md`、`variants/`、`cases/`、`recipes/`。
3. 用户确认标准后执行上面的 `brief --confirm-criteria`。
4. brief 退出 0 后执行 `run --gate`（用户明确说先不管能不能发才去掉 `--gate`）。
5. run 结束后执行 `report`，读 `report.md` / `promotion.json`，按关注点 × 格子讲人话。
6. 不要自己编分数。

新实验默认写到 `<源所在 git 仓根>/experiments/<id>/`；源不在 git 仓里则写到 `<cwd>/experiments/<id>/`。重名则 `-2`、`-3`。

## 禁止

- `git worktree add`
- 写用户全局 skill 目录（`~/.claude/skills` 等）
- exec 被测 command 做试跑打分
- 跳过 brief 宣称可跑
- 自动一轮轮改目录刷分；用户说再试一处时最多加 1 个 treatment，再走 brief + run

## 矩阵

开跑前向用户确认格子的 argv、prompt 怎么传入（默认 stdin）、变体会放到 `${program_root}`。用户没点头不要猜 `claude` / `codex` 的 stdin。

交易 skill 的推荐关注点见 `references/case2.md`。
