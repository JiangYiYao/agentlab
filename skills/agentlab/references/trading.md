# 交易向 Skill（可选）

只在用户要测的目录是交易/研报类 Skill（会写 `$SCUTIO_HOME/cache/trading`、报告里有方向和动作）时读这份。其它目录不要套用。

## 还要问清的

- 新闻是用已经存好的一份，还是上网现搜（默认用已经存好的，不要现搜）。
- 要用存好的：问清本地目录在哪。两样都没有就不要开跑。
- 产物是不是必须仍写在 `$SCUTIO_HOME/cache/trading` 下（默认是）。

## 关注点怎么写

一条 concern 只有一个 `measure`。质量不要压成一个 LLM 分。时间、token 若只是看看，写成 `role: objective`，**不要**加 `pass`（假跑的墙钟是噪声）。

建议的 gate（可按用户原话增删）：

- `label-align`：`type: label_extract`，从新 run 的 `analysis/report.md` 抽方向、动作，和用户给的期望比。
- `no-upgrade-without-evidence`：`type: no_upgrade`，`from: 无法判断`，`to: 介入`。
- `sections-present`：`type: section_present`，标题必须有 `结论`、`依据`、`改变判断的条件`。
- `counterarg-inline`：`type: counterarg_inline`（依据段里出现反证用语即可，不必再要一个「最强反证」标题）。
- `output-path-invariant`：`type: path_under`，新 run 目录前缀是这次注入的 `SCUTIO_HOME`，路径里带 `cache/trading`。

`label_extract` / 读报告的 `source` 用 `${trial_out}/replay.json`，并加：

```yaml
report_from: { json_path: "$.new_run_dir", suffix: "analysis/report.md" }
```

prompt 里写死 `TRADING_SKILL_DIR=${program_root}`，不要去跑用户家目录或仓外的作者原树。

隔离用 `homedir`：产物写独立的 `SCUTIO_HOME`。取数用用户本机已经能用的解释器和 toolkit（例如 `SCUTIO_PYTHON` 指到已有 venv），不要只建一个空家目录导致取不到数。有回放数据时由 runner 注入 Python wrapper。不要改 skill 去「复用已有 run」。
