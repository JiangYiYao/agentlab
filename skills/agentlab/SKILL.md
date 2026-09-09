---
name: agentlab
description: >
  优化、测试和比较已有的 Skill 或 agent 工作流，检查质量、耗时、用量和稳定性。
  也用于查看实验历史、换裁判重评、排查某个用例退步及清理实验缓存。
  不用于从零创建 SKILL.md。
---

# AgentLab

你负责准备实验、调用工具、核对证据并解释结果。用户用自然语言说明需求，无需自己找脚本、填 YAML 或翻目录。

## 按请求选择入口

先利用当前对话和已有实验确定用户要做什么，只读对应说明。信息不足时补问缺少的对象或实验位置，不要一律转成新建实验。

| 用户要做什么 | 下一步 | 阅读 |
|---|---|---|
| 讨论优化现有 Skill / 工作流 | 读源目录，提出具体改法；已有授权就继续准备实验 | [workflow.md](references/workflow.md) |
| 测现成版本、对比版本、增加样本 | 列出或更新测试计划，再 `brief` → `run` | [workflow.md](references/workflow.md) |
| 查看进度或历史结果 | `status` / `report --run`；读取已有记录 | [lifecycle.md](references/lifecycle.md) |
| 换裁判或标准重新评分 | 核对来源运行和评审次数，再 `rescore` | [lifecycle.md](references/lifecycle.md)、[workflow.md 的次数规则](references/workflow.md#裁判与次数) |
| 怀疑裁判提示词、依据或分数有问题 | 检查当次评分审计，不重新执行任务 | [audit.md](references/audit.md) |
| 某个实验组失败或某个 case 退步 | 检查执行审计与同条件对照组 | [execution-audit.md](references/execution-audit.md) |
| 查看占用、结束实验或清理 | `storage`；清理前 `cleanup --dry-run` | [storage.md](references/storage.md) |

写配置以 [contract.md](references/contract.md) 为入口；被测任务要改真实代码库时加读 [coding.md](references/coding.md)。领域规则来自本次任务和用户标准，不套用其他案例的模型、并行数、模块拆法或 subagent 限制。

## 只调用这两个脚本

`SKILL.md` 所在目录就是 `<skill-dir>`，先解析为实际绝对路径。

1. 用任意本机 `python3` 执行 `<skill-dir>/scripts/ensure_python.py`。stdout 第一行是之后使用的 Python 3.11+ 解释器 `<py>`，依赖已经就绪。它优先复用 `AGENTLAB_PYTHON` 或本机可用环境，必要时在 `$AGENTLAB_HOME/.venv`（默认 `~/.agentlab/.venv`）准备独立环境。不要往系统 Python 装依赖。失败时读 stderr，区分缺少解释器、下载或安装失败，只补缺失条件。
2. 所有实验操作都用 `<py> <skill-dir>/scripts/cli.py <子命令>`。不知道参数时用该命令的 `--help`，不猜参数。

`scripts/agentlab/` 是由 CLI 自动导入的内部库。日常实验无需搜索、逐个阅读或直接执行里面的文件；只有排查工具自身报错、文档与行为不符或开发 AgentLab 时才进入实现。不要执行 `scripts/agentlab/cli.py`，也不要求用户安装全局命令。

| 操作 | CLI 参数（接在 `scripts/cli.py` 后） |
|---|---|
| 检查已确认的实验标准 | `brief --exp <实验目录> --confirm-criteria` |
| 执行并判断能否采用 | `run --exp <实验目录> --gate` |
| 查看进度 | `status --exp <实验目录>` |
| 生成当次或历史报告 | `report --exp <实验目录> [--run <run_id>]` |
| 只重评已有执行证据 | `rescore --exp <实验目录> [--run <run_id>]` |
| 检查空间占用 | `storage --exp <实验目录>` |
| 预览 / 执行清理 | `cleanup --exp <实验目录> --dry-run` / `cleanup --exp <实验目录>` |

方括号表示可选参数，实际调用时删掉括号。`run` 默认复用输入和评分口径匹配的结果；`rescore` 不启动被测命令；`report` 不调用模型。不要为了查询结果或补审计材料去 `run`。

## 汇报时主动带用户看依据

运行、重评或查看历史后，先解释结论：哪些要求满足、哪些不满足，以及能否采用。读取当次 `report.md`、`promotion.json` 和 `diagnostics.json`，核查失败、unknown 和影响推荐的较弱 case。区分新执行、复用执行、新评分与复用评分；没有通过条件的观测不能直接说通过。

主动给出实际存在的可点击绝对路径：

- `runs/<run_id>/report.md`：结果报告。
- `runs/<run_id>/audit.html`：裁判提示词、原始回复和评分来源；有争议的分数附对应审计锚点。
- `runs/<run_id>/execution.html`：运行前 Skill、任务、轨迹及与对照组的比较；解释已证实的问题、可能原因和缺失证据。

编码任务另打开 `runs/<run_id>/diff.html`，按文件说明改动。不要只交文件清单让用户自行分析。中断时给已有记录入口，并说明哪些结果尚未产生。

历史依据只能来自归档。材料缺失、截断、裁判报错或理由不足时主动说明；不能用当前文件补写历史，不能把“材料已提供”说成“模型已读过”，也不能把请求的模型配置说成实际模型证明。低分本身不能证明是 Skill 的问题。

## 授权与边界

沿用本次对话已确认的方案和授权，不重复确认同一件事。新执行或增加模型调用前讲清范围、次数和目的；没有覆盖这一步的授权时再补确认。只读历史无需确认。用户指定只试一次、具体模型或改法时照办。

测试在副本里进行，不写用户全局 skills，不自动覆盖源目录，不自动一轮轮改稿刷分。时间、金额、token 上限只在用户要求时设置。环境起不来要说明环境问题，不能当作改法不合格。

worktree 由 runner 创建和管理，不手动增删。用户明确结束实验或要求清理后，先预览再清理；有待排查的现场继续保留，历史归档不手动删除。
