# 契约（只给 agent 写盘，不要让用户去填）

`experiment.yaml` 必须能过 `brief`。不要写 `harness`、`skill_install`、`rubric`、`swap_order`、`slices`。

`id` 用小写字母、数字、连字符，至少 2 个字符（例如 `compare-change`，不能是 `t`）。

恰好一个 `role: baseline`。treatment 最多 3 个；每个 treatment 都要有 `hypothesis`（`change` / `bet` / `hurt` / `falsify`）。baseline 不要写 hypothesis。

关注点 1～8 条。`role: gate` 必须有 `scope`、`pass`，且 `aggregate: all_pass`。`llm_rubric` 不能当 gate。

`criteria.md` 按关注点分节（`## <concern.id>`）。用户口头确认标准后，用 `brief --confirm-criteria` 写入 `criteria.sha256`，不要手填哈希。

有 `llm_rubric` 时，`judge.command` 必填。`judge.mode` 为 `per_trial`（默认，每次试验各评各的）或 `compare_case`（同一用例、cell 和重复序号收齐各版本的回答及声明证据后并排评一次）。不要用 `swap_order` 冒充并排。

被测命令的第一个词必须在 PATH 上（`brief` 会查）。模型配置可省略，沿用该命令本机已有的登录。

## 配置导航

先用下方基础草稿，再按任务增加所需配置，不必一次读完全部文档。

| 配置 | 用途 | 说明 |
|---|---|---|
| variants / cases / matrix / repetitions | 版本、用例、命令与样本规模 | 本文草稿；[实验计划](workflow.md) |
| concerns / judge | 测什么、怎么判断、怎样调用裁判 | 本文约束；[评分协议](lifecycle.md#脚本评分的返回协议)、[裁判次数](workflow.md#裁判与次数) |
| evidence / cases[].inputs / measure.inputs | 保留输出、声明缓存依赖 | [执行和证据](lifecycle.md) |
| trace.files | 保存外部 CLI 导出的本次过程记录 | [执行审计](execution-audit.md)；开跑前配置 |
| isolation / artifact.layout | 工作区、身份与被测目录位置 | 本文；真实仓库另见 [coding.md](coding.md) |
| recipes / env | 复用命令与环境变量 | 本文下方身份和 recipe 规则 |
| budget | 并发、试验数及用户要求的上限 | [预算与身份](lifecycle.md#预算身份与保护路径) |
| promotion | 跨试验判断能否采用 | 本文草稿；[重评和判定](lifecycle.md) |

## 最小可跑草稿

把 `<...>` 换成这次实验的值。`command` 用用户确认过的那条本机命令。

```yaml
schema_version: 1
id: <slug>
name: <短名>
artifact:
  type: dir
  name: <slug>
  layout: sidecar
  source_path: <用户给的源目录绝对路径>
criteria:
  path: criteria.md
variants:
  - id: baseline
    role: baseline
    path: variants/baseline
    created_by: import
  - id: <treatment-id>
    role: treatment
    path: variants/<treatment-id>
    parent: baseline
    created_by: skill-hypothesis
    hypothesis:
      change: "<改了什么>"
      bet: "<赌会变好的点>"
      hurt: "<可能变差的点>"
      falsify: "<怎样算证伪>"
concerns:
  - id: smoke
    intent: "<用户在乎的事>"
    role: objective
    measure:
      type: script
      command: ["true"]
matrix:
  cells:
    - id: local-cli
      command: ["<用户确认的二进制>", "<参数…>"]
      prompt: { mode: stdin }
cases:
  - id: main
    path: cases/main
    prompt_file: prompt.md
    require_exit_0: true
isolation:
  type: tempdir
  inherit_host_identity: true
budget:
  max_trials: 24
  max_parallel: 4
repetitions: 3
promotion:
  all_cells_must_pass: true
```

真实任务的默认样本与并发建议见 [workflow.md](workflow.md)；用户明确要求只试一次时可以写 `1`。不要写 `wall_clock_s` / `usd` / `tokens`，除非用户要求设上限。

被测 skill 要在真实 git 仓里改代码时，不要用上面这份 `tempdir` 草稿，改用 `coding.md` 里的草稿。根仓里还有嵌套 git 仓时，在 `isolation.nested_repos` 列出 `path` / `source` / `freeze`。扫描源码时用 measure 的 `include` / `exclude`，不要扫到缓存目录。

源目录在 git 仓里、要比工作区改动、但任务并不是「在另一份仓里改代码」时，也可以把 `isolation` 写成 `type: git-worktree`，`repo` 写该仓（绝对路径或相对实验根），`freeze` 默认 `HEAD`。

需要独立家目录时，用 `type: homedir` 并设置 `inherit_host_identity: false`；仅指定 homedir 仍默认继承宿主身份。HOME 由 runner 创建并设置。`isolation.env_inject`、`cell.env` 和 `case.env` 都不能设置 `HOME` / `CODEX_HOME` / `CLAUDE_CONFIG_DIR` 等身份目录键；自定义非身份变量可以放在 env_inject。独立身份会移除宿主身份目录变量，可能需要单独准备登录，不能悄悄启用。

复用命令可在 `recipes.<id>` 内联定义，或放在实验目录的 `recipes/<id>.yaml`、`$AGENTLAB_HOME/recipes/<id>.yaml`，cell 用 `recipe: <id>` 选择。优先使用内联、实验目录、AGENTLAB_HOME；旧 examples/recipes 位置仅作最后兼容回退。`recipe.env` 仅允许 `CODEX_HOME` / `CLAUDE_CONFIG_DIR`；仍须遵守已确认的身份设置，独立身份会移除这两项。`write_files`、`usage`、`unstable_kill` 尚未实现，不要配置。

`cases/main/prompt.md` 若要对 AI 下发 Skill，写明阅读并遵循 `${program_root}/SKILL.md`。不要把目录装进 `~/.claude/skills`。

执行/评分分离、`rescore`、`evidence`、`measure.result`、声明输入和预算预留见 [lifecycle.md](lifecycle.md)。未知字段会被拒绝；只运行测试命令的 script 默认以退出码评分，读取 JSON 时显式指定 `result: json`。
