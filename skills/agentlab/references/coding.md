# 编码向 Skill（可选）

只在被测目录是「在真实代码库里做编码任务」的 Skill 时读这份。其它目录不要套用。交易/研报见 `trading.md`。

## 还要问清的

- 任务要改的是哪一份 git 仓（绝对路径）。没有仓、或只要带上当时未提交的文件，才退回拷贝。
- freeze 钉哪一次提交（默认 `HEAD`，用户说钉某个提交就用那个）。不要猜。

## 怎么隔离

`variants/` **只拷被测 skill**（`SKILL.md` 和它的脚本），不要把那份代码库整仓拷进实验。

隔离写成：

```yaml
isolation:
  type: git-worktree
  repo: <那份仓的绝对路径>
  freeze: HEAD
  inherit_host_identity: true
```

每次试验由 runner 对该仓 `worktree add`，改动发生在这份 checkout 里，不写进用户正在用的工作区。不要自己执行 `git worktree add`。

prompt 里写阅读并遵循 `${program_root}/SKILL.md`，在 `${project_root}` 里改代码。不要把 skill 装进全局 skills，也不要把命令的工作目录指到用户原仓。

## 测完拆 worktree

讲完结果之后问：这次实验是否已经做完。用户说做完了，再跑：

```text
<py> <skill-dir>/scripts/cli.py cleanup --exp <实验目录>
```

这会拆掉这次实验挂在该仓上的 worktree，不动主工作区。用户没说做完（还要看现场、再跑、再加一处改法）就留着，不要自行拆。
