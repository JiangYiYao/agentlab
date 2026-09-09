# AgentLab

中文 · [English](README.en.md)

**测试和比较已有的 AI Skill 或工作流，看看改动是否真的有效。**

改了提示词，结果有没有更好？换了一套流程，能省多少时间？同一个任务多跑几次，表现还稳不稳？AgentLab 把不同版本放到相同用例下测试，记录结果、耗时和可获取的用量，帮助你判断是否采用改动。

AgentLab 本身也是一个 Skill。安装后，在对话里告诉 agent 要测什么，它会准备实验、执行测试，再带你看结果。日常使用无需手写配置文件或操作命令行。

## 安装

需要 **macOS 或 Linux、Python 3.11+**，以及一个支持本地 Skill 的 coding agent。被测工作流还需要有一条在本机能正常运行的命令；所需工具和登录由你已有的环境提供。

1. 下载或克隆本仓库。
2. 将整个 [`skills/agentlab/`](skills/agentlab/) 文件夹复制到你的 agent 的 skills 目录，保留其中的 `scripts/` 和 `references/`。
3. 按所用 agent 的方式加载 Skill，然后在对话中使用 AgentLab。

Python 依赖会由附带脚本准备，必要时安装到独立虚拟环境。无需额外安装全局 `agentlab` 命令。

## 从一句话开始

**比较两个现成版本：**

> 用 AgentLab 比较 `./skills/summarizer-v1` 和 `./skills/summarizer-v2`。用同一批文档，各跑 3 次，重点看关键信息有没有遗漏，以及耗时有没有下降。

**检查一个版本是否稳定：**

> 用 AgentLab 测一下 `./skills/data-extractor`。选几份格式不同的输入，重复运行，检查输出 JSON 是否有效、必填字段是否齐全。

**先讨论改法，再做对照测试：**

> 我想优化 `./skills/code-review`，减少重复检查，同时保留重要问题。先看看可以怎么改，方案定下来后再和原版比较。

除了 Skill，也可以测试能通过命令启动的 agent 工作流。测试任务、输入材料和评价标准都由你的实际需求决定。

## 接下来会发生什么

Agent 会先读相关目录，整理一份具体的测试计划：比较哪些版本、使用哪些用例、各跑几次、并发多少，以及怎样判断结果。如果需要模型评审，也会说明评审方式和调用次数。缺少启动命令或输入材料时，它会向你补充确认。

计划确认后开始执行。待比较的版本保存在实验目录中；编码任务可以使用独立的 Git worktree。检查文件是否存在、JSON 是否有效、测试是否通过等明确要求，用脚本验证；内容质量等需要判断的部分，可以交给模型评审。

你可以指定时间、金额或 token 上限，默认不设置。耗时会被记录；费用和 token 统计取决于所用命令是否提供相应数据，无法获取的用量会标为未知。

## 看结果，也能追溯过程

运行结束后，agent 会围绕你关心的问题解释结果：哪些要求已满足、哪些失败、哪个版本表现更好，以及还有哪些结论缺少依据。

实验默认保存在 `~/.agentlab/experiments/`，也可以指定其他位置。你可以直接查看：

| 内容 | 在哪里看 |
|---|---|
| 各版本的结果、评分和耗时 | 实验目录中的 `report.md` |
| 本次使用的配置和评价标准 | `experiment.yaml`、`criteria.md` |
| 每次运行的输出、日志和评分依据 | `runs/<run_id>/trials/` |
| 编码任务的文件改动 | `runs/<run_id>/diff.html` |

记录保存在本机；执行时是否调用外部服务，取决于你选择的命令和评审模型。

## 继续比较，不必每次从头跑

可以在原来的对话里继续提出要求：

> 保留这次的执行结果，换一个评审模型重新评分。

> 再加一组长文档用例，看看新版是否还有效。

> 这次全部重新运行，不复用之前的结果。

AgentLab 会检查版本内容、任务输入和执行配置，复用仍然适用的结果。只换评分方式时，可以用已保存的材料重评；只调整通过阈值时，可以沿用已有测量值。历史运行保留当时的配置和结论，方便回看。

重评需要相应的输出材料已经保存。具体复用规则、证据配置及旧版本升级说明，见[执行与重评说明](skills/agentlab/references/lifecycle.md)。

## 详细文档

- [实验配置](skills/agentlab/references/contract.md)：用例、执行命令、评分和预算字段。
- [编码任务](skills/agentlab/references/coding.md)：在真实 Git 仓库中测试代码改动。
- [执行与重评](skills/agentlab/references/lifecycle.md)：结果复用、证据保留、重试和兼容性。
- [Skill 使用指引](skills/agentlab/SKILL.md)：供 agent 阅读的完整工作流程。

## 本地开发

在仓库根目录执行，使用 Python 3.11+：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

开发安装后可使用 `agentlab --help` 查看 CLI。主要命令有 `brief`（检查实验配置）、`run`（执行）、`rescore`（重评）、`report`（生成报告）、`status`（查看进度）和 `cleanup`（清理实验工作区）。

采用 [MIT 协议](LICENSE)。
