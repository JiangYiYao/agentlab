# 执行、证据和重评

在已有实验上改输入、换评估器、重跑或读取历史时，使用这份说明。

## 三个独立步骤

- 执行身份包含候选文件内容、实际任务 prompt、声明的输入、命令/脚本、cell 配置、隔离仓库的冻结 SHA 和执行限制。修改候选只使该候选的执行失效。
- 每条测量有独立依据：执行身份、measure 配置、声明的评分输入和评分脚本。LLM 还包含 criteria 内容与 judge 配置。
- pass、aggregate、scope 和 promotion 是判定规则。只改这些规则，沿用已有测量值重新判定。

`run` 默认复用匹配的已完成执行和测量。`--no-reuse` / `--force` 产生新的物理执行；输出从空目录开始。`--retry-failed` 重新执行失败任务。失败时保留的 worktree 不阻塞下一次执行。

改 skill 时先更新候选副本；缓存只能识别副本的实际内容，不会替你从源目录更新它。外部数据、被脚本间接导入的模块等，在 `cases[].inputs`（执行）或 `measure.inputs`（评分）声明；系统不会推测所有隐式依赖。已知的命令脚本文件自动纳入指纹。外部服务状态、宿主工具配置变动无法完整自动识别，需要重新执行时用 `--no-reuse`。

## 只重新评分

```text
<py> <skill-dir>/scripts/cli.py rescore --exp <实验目录> --run <已有 run_id>
```

省略 `--run` 使用最近一次运行。此命令绝不启动被测命令：没有匹配的执行证据就失败。LLM 重新评分；未变化的脚本测量复用。普通 `run` 中只改 judge 也只重新评分，不重跑被测。

并排评分的依据包含整组执行身份。增减对比对象、更新任意答卷或换裁判，会重评该组。完全相同的组可复用评分。`--only-variant` 只选择该版本，不自动补跑对照；需要相对对照的评分时使用完整的组，让 runner 自动复用对照。

修改需工作区的评分脚本后，重评需要完整工作区证据。原执行未保存时结果为 unknown，应明确说明缺了什么；不能偷偷重新执行任务。

## 通用证据

stdout、stderr、实际 prompt、usage、workspace.diff 自动进入证据包。额外输出文件从 `${trial_out}` 下选取：

```yaml
evidence:
  files: [answer.json, reports/*.md]
  workspace: false
  max_file_bytes: 4194304
```

文本任务直接评 stdout；数据任务可评声明的 JSON；文件任务可评声明的文件；编码任务可评补丁及改后文件。独立和并排裁判都能访问 `evidence/`；并排按 A/B 等标记分组。

manifest 列出文件、来源、存储内容哈希以及缺失或截断状态。单文件大小限制控制裁判材料，不是任务运行预算。独立代码审查兼容保存工作区的行为；并排默认只保存补丁、改后文件和声明的证据，不复制整仓。需要以后在完整工作区重跑脚本时显式设 `workspace: true`。不完整或截断的工作区不能作为完整快照运行脚本。

## 脚本评分的返回协议

只判断测试是否成功：

```yaml
measure:
  type: script
  result: exit_code
  command: [python3, -m, pytest, -q]
  cwd: sandbox
```

退出 0 的测量值为 true，非零为 false。

读取数值：

```yaml
measure:
  type: script
  result: json
  command: [python3, '${experiment_root}/eval.py']
  output_json: outputs/eval/out.json
  value_path: $.score
  inputs: [gold.json]
  env: {CHECK_MODE: strict}
```

JSON 模式的非零退出、缺失或非法结果为 unknown。默认未指定 result 时，提供 output_json/value_path 就使用 JSON，否则按退出码判断。老的脚本若依赖默认 `outputs/eval/out.json`，需补 `result: json`。

文件清单缺失会在 brief 阶段报错；未知字段和未实现的配置会被拒绝。`label_extract.pattern` 可以指定任意字段和正则捕获组；旧投资报告的默认提取约定保留在 compatibility 模块，新实验应使用显式 pattern 或外部评分脚本。

## 历史和现场

`runs/<run_id>/` 保存契约快照、评分、完整输出归档、证据和报告。每次新执行的工作区在该 run 的 `workspaces/`；`trials/<id>/` 是最近一次对应试验的工作副本。

```text
<py> <skill-dir>/scripts/cli.py report --exp <实验目录> --run <run_id>
```

历史报告使用当时契约，不依赖当前 criteria 或候选文件。旧版本没有契约快照、执行身份的历史数据仍可查看；不能作为新缓存可信复用，需要重新执行一次。

`keep_sandbox: true` 保留成功工作区，`keep_on_fail: true`（默认）保留失败现场。新执行不会覆写已保留的工作区。用户确认结束后运行 cleanup；它移除实验下已注册的 worktree，不还原用户主仓。

## 预算、身份与保护路径

总金额/token 上限要求 `budget.per_trial` 提供单次预留额；有 LLM 时还需 `budget.per_judge` 的预留额。启动前原子预留，完成后按 usage.json 结算；用量未知时按预留额记账并在报告标为 unknown_calls。外部命令必须配合报告/限制实际消费，预留不能保证一个任意外部进程不会超额消费；发现超额后停止后续调用。未设置上限就不设上限。

任务和裁判可在自身 cwd 写 usage.json，字段为 tokens_in、tokens_out、usd；任务统一写 `${trial_out}/usage.json`。墙钟上限会传给进程超时；`case.timeout_s` 进一步限制单个任务。金额与 token 的预留逻辑对 `on_exceed: stop` 和兼容值 `skip_remaining` 都会停止后续调用。

judge 支持 stdin、argv、file 三种 prompt 模式。`inherit_host_identity: false` 给它独立 HOME 并移除宿主身份目录变量。记录的 command/model 是请求配置，不自动当作实际模型证明；实际模型和工具轨迹可由运行包装脚本导出为声明的证据文件。

隔离工作区不是操作系统权限沙箱。源目录、主仓、嵌套仓及 `isolation.protected_paths` 会检查内容变化；检测到变化则失败并报告路径，不自动还原已有文件。不要把包含实验输出的祖先目录列为保护路径。
