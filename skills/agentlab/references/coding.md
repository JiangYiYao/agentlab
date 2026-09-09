# 编码任务（改真实代码库）

只在被测 skill 会在一份真实 git 仓里改代码时用这份。按这里的草稿写实验。**不要读 `scripts/agentlab/` 里的实现**，除非 `brief` 失败，或跑出来的行为和这份文档不一致。

## 还要问清的

- 任务要改的是哪一份 git 仓（绝对路径）。没有仓、或只要带上当时未提交的文件，才退回拷贝。
- freeze 钉哪一次提交（默认 `HEAD`）。不要猜。
- 本机哪条已经能登录的命令、任务说明怎么喂给它（stdin / 参数 / 文件）。命令若要模型 id，问清本机这条命令认的那个，写进 `matrix.cells[].model`，用 `${cell.model}` 传，不要猜。
- 若用 LLM 裁判：哪条命令、**形态**（独立审查还是并排对比）、以及次数（写成数字）。用户只点模型名不够。同一道题比几份 skill 稿时提案并排对比（`judge.mode: compare_case`）；要方差、各评各的才用独立审查。

## 两棵树，不要混

| 变量 | 是什么 | 实验里放哪 |
|---|---|---|
| `${program_root}` | 被测 skill 的拷贝 | `variants/` → 每次试验的 `outputs/program/` |
| `${project_root}` | 任务要改的代码仓 | runner 建的 worktree（命令的 cwd） |

`artifact.layout` 用 `sidecar`。`variants/` **只拷 skill**（`SKILL.md` 和它的脚本），不要把代码库整仓拷进来。

不要把 skill 装进全局 skills。prompt 写：阅读并遵循 `${program_root}/SKILL.md`，在 `${project_root}` 里改代码。

## 草稿

把 `<...>` 换成这次的值。通用字段含义见 `contract.md`。

```yaml
schema_version: 1
id: <slug>
name: <短名>
artifact:
  type: dir
  name: <slug>
  layout: sidecar
  source_path: <被测 skill 的绝对路径>
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
      change: "<改了 skill 的哪一段>"
      bet: "<赌代码改动会更好的点>"
      hurt: "<可能改坏或改多的点>"
      falsify: "<怎样算证伪>"
concerns:
  - id: no-extra-files
    intent: "<只许改这些路径；用户原话>"
    role: gate
    scope: case
    measure:
      type: workspace_diff
      allow_write: ["<相对 ${project_root} 的路径或 glob>"]
      exclude: ["**/.agents/**", "**/build/**"]
    pass: { op: "==", vs: value, value: true }
    aggregate: all_pass
  - id: tests
    intent: "<有测试就跑；没有这条就删>"
    role: gate
    scope: case
    measure:
      type: script
      result: exit_code
      command: ["<用户确认的测试命令>"]
      cwd: sandbox
    pass: { op: "==", vs: value, value: true }
    aggregate: all_pass
matrix:
  cells:
    - id: local-cli
      model: <本机这条命令认的模型 id；命令不吃模型则整行删掉>
      command: ["<用户确认的二进制>", "--model", "${cell.model}"]
      prompt: { mode: stdin }
cases:
  - id: main
    path: cases/main
    prompt_file: prompt.md
    require_exit_0: true
isolation:
  type: git-worktree
  repo: <任务仓的绝对路径>
  freeze: HEAD
  keep_sandbox: true
  inherit_host_identity: true
  # 根仓 worktree 里没有的嵌套 git 仓（相对 ${project_root}）：
  # nested_repos:
  #   - path: repos/foo
  #     source: <那份仓的绝对路径>
  #     freeze: HEAD
budget:
  max_trials: 24
  max_parallel: 4
repetitions: 3
promotion:
  all_cells_must_pass: true
```

命令不吃 `--model` 时，删掉 `model` 和参数里的 `${cell.model}`。prompt 不是 stdin 时：`mode: argv` 把说明追加到参数末尾；`mode: file` 再加 `flag`（默认 `--prompt-file`）指向 `prompt.md`。

没有测试命令就不要写 `tests` 那条。`workspace_diff` 看这次试验相对开跑前快照的新增、修改、删除、重命名；未跟踪文件记为 `U`，不要和已入库的新增混在一起。`allow_write` 之外的改动算不满足。要断言某文件一定出现，用 `script`。

## 怎么打分

能用文件路径、测试退出码说清的，用 `workspace_diff` / `script`，可以当必须满足的条目。

只能看改得像不像、注释好不好时，用 `llm_rubric`，**不能**当必须满足的条目。独立审查时裁判工作目录是该次代码快照根；并排对比时工作目录是匿名的 `patches/` + `after/`，stdin 不出现对照组/实验组名字。评什么以任务说明和标准为准。

并排对比在实验级写：

```yaml
judge:
  command: ["<用户确认的裁判命令>"]
  mode: compare_case
```

每次试验还会在拆 worktree 之前把这次改动写成给人看的 HTML：`trials/<id>/outputs/diff.html`，补丁在 `outputs/workspace.diff`，整次运行的目录在 `runs/<run_id>/diff.html`。讲结果时打开这份，不要只报裁判分。它不算分。

## 环境起不来就停

不要为了先试环境去完整跑一遍编码任务。`brief` 只说明契约合法，不说明这条命令能在隔离 worktree 里干活。

runner 会盯 stdout/stderr。工作区未信任、未登录、额度/不可用、未知参数：进程已经失败，或匹配后不再有新输出、像在空等，才杀掉该次并取消还没开的试验（退出 3）。进程后来正常退出 0，不当成环境失败。这不是改法失败。

对人说明是环境问题；修信任/登录/参数后再跑。环境还没跑通时，不要把整批试验铺开。

## worktree

每次试验由 runner 对 `isolation.repo` 做 `worktree add`，命令 cwd 就是这份 checkout。不要自己 `git worktree add`。

讲完结果后问：这次实验是否已经做完。用户说做完了，再：

```text
<py> <skill-dir>/scripts/cli.py cleanup --exp <实验目录>
```

拆掉这次挂在该仓上的 worktree，不动主工作区。没说做完就留着。
