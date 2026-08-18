# 契约（只给 agent 写盘，不要让用户去填）

`experiment.yaml` 必须能过 `brief`。不要写 `harness`、`skill_install`、`rubric`、`swap_order`、`slices`。

`id` 用小写字母、数字、连字符，至少 2 个字符（例如 `cleanup-android`，不能是 `t`）。

恰好一个 `role: baseline`。treatment 最多 3 个；每个 treatment 都要有 `hypothesis`（`change` / `bet` / `hurt` / `falsify`）。baseline 不要写 hypothesis。

关注点 1～8 条。`role: gate` 必须有 `scope`、`pass`，且 `aggregate: all_pass`。`llm_rubric` 不能当 gate。

`criteria.md` 按关注点分节（`## <concern.id>`）。用户口头确认标准后，用 `brief --confirm-criteria` 写入 `criteria.sha256`，不要手填哈希。

格子的 `command[0]` 必须在 PATH 上（`brief` 会查）。模型配置可省略，沿用该命令本机已有的登录。

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
  max_trials: 8
  max_parallel: 1
  wall_clock_s: 3600
  per_trial: { wall_clock_s: 600 }
  on_exceed: stop
repetitions: 3
promotion:
  all_cells_must_pass: true
```

有噪声的对照（真 CLI、外搜、LLM 裁判）`repetitions` 默认 **3**。只有确定性假 command 才写成 `1`。`budget.max_parallel` 默认 2～4，不要无故写成 1。

源目录在 git 仓里、要比工作区改动时，把 `isolation` 改成 `type: git-worktree`，并写 `repo`（夹具仓或源仓相对实验根的路径）和 `freeze: HEAD`。

被测程序往固定家目录写产物（例如 `$SCUTIO_HOME`）时，用 `type: homedir`，需要的 `SCUTIO_*` / `AGENTLAB_*` 写在 `isolation.env_inject`。不要在 `cell.env` 里写 `HOME` / `SCUTIO_HOME` / `CODEX_HOME` / `CLAUDE_CONFIG_DIR`。

`cases/main/prompt.md` 若要对 AI 下发 Skill，写明阅读并遵循 `${program_root}/SKILL.md`。不要把目录装进 `~/.claude/skills`。
