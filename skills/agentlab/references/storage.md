# 磁盘存储和清理

排查空间占用、搬迁实验或清理现场时使用本说明。默认实验位于 `$AGENTLAB_HOME/experiments/`，未设置时为 `~/.agentlab/experiments/`。

## 目录职责

```text
<experiment>/
  experiment.yaml、criteria.md、cases/、variants/  当前输入和候选
  executions/<id>/                             一次实际执行的元数据和原始产物视图
    inputs/、trace/                             运行前输入快照、执行器导出的轨迹
  evaluations/<run_id>/trials/<trial_id>/        评分记录、执行引用和新增/修改的产物
  evaluations/<run_id>/compare/                 并排评审材料及结果
  artifacts/sha256/                             按内容和文件权限共享的归档文件
  runs/<run_id>/                               本次配置、报告、结论和产物引用
    audit.html                                 从报告内各条分数跳转的审计页
    execution.html、diagnostics.json            同 case 对照排查页与观测索引
  workspaces/<run_id>/                          可写的执行现场
  cache/trials/<trial_id>/                      可重建的试验工作副本
  trials/                                      指向 cache/trials/ 的兼容入口
  report.md、promotion.json                    最近结果
```

`executions/` 在被测命令结束、评分开始前保存原始产物。`evaluations/` 保存评分生成或修改的文件，并引用原执行中未变的内容。即使评分脚本改写了同名输出，原始执行产物也不会被覆盖。

归档文件存入 `artifacts/`；相同内容及权限只保存一份。各次运行的 `runs/<run_id>/trials/<trial_id>/outputs/` 保留可直接浏览的视图，用相对链接指向执行或评分归档。重评、复用及多个裁判不会为相同的大文件再存一份内容。

运行时的工作副本是独立文件，不与归档共享可写文件。评审结束并归档后，缓存中重复的裁判工作区和证据副本会移除，小型日志保留。引擎创建的临时 HOME 属于运行环境，不作为结果归档。工作副本在运行期间仍会占空间，内容共享并不意味着执行过程零复制。

## 查看空间

```text
<py> <skill-dir>/scripts/cli.py storage --exp <实验目录>
<py> <skill-dir>/scripts/cli.py storage --exp <实验目录> --json
```

按归档内容、执行记录、评分记录、运行记录、缓存和工作区等目录统计文件字节数。同一 inode 只计一次；符号链接不重复计算目标内容。数字不包含文件系统元数据、目录/链接本身的开销，也不包含外部 CLI 在其他目录中的会话和缓存。

## 清理

用户已明确要求清理或结束实验后，先查看预览，再执行清理；沿用已有授权，不重复确认同一范围：

```text
<py> <skill-dir>/scripts/cli.py cleanup --exp <实验目录> --dry-run
<py> <skill-dir>/scripts/cli.py cleanup --exp <实验目录>
```

清理会注销本实验的 Git worktree，删除普通执行工作区，并回收已经归档且未被修改的试验缓存。它不会删除历史运行、原始执行产物、评分记录、候选、用例或源仓库。

- 正在执行实验时拒绝清理，运行与清理共用同一个锁。
- 没有归档凭据、缓存内容与归档时不同的目录会跳过，并在 `skipped` 中列出。
- 无法找到源仓库、无法安全识别的 Git 工作区会保留。
- 清理不要求当前 criteria 仍然存在或与配置哈希一致，会同时参考历史运行中的仓库配置。
- 清理缓存后，下次运行可从归档恢复并复用匹配的执行；不会仅因缓存被删除就重跑任务。

历史结果没有自动过期策略，不能把 `artifacts/` 或 `evaluations/` 当作缓存单独删除。归档引用依赖这些目录；需要备份或搬迁时保留整棵实验目录及相对链接。

## 旧实验

已有 `trials/` 实体目录继续使用，不自动搬动其中可能注册过的旧 worktree。旧版完整复制的归档仍可读取和用于复用；新执行采用共享存储，不自动重写全部旧历史。

`runs/<run_id>/workspaces/` 是新工作区位置的兼容入口，旧报告的产物浏览路径继续保留。相对归档链接可以随整棵实验目录搬迁；命令或输入中自行写入的外部绝对路径仍需另行检查。
