# 模型时窗、路由证据与额度

本页保存源对话实际发生的模型切换与额度查询方法。模型分工由用户和 Claude 主管决定；Codex 执行明确的项目范围调整，不能因为慢、失败或额度较多就自行替换模型、扩大并发或延长期限。

源文件基准目录是 `<BRIDGE_DIR>`。下面一次性文件绑定 2026-10-13 的项目和请求，不是通用模型管理接口。

## 临时 Fable 主管：同一会话，明确到期

用户先要求 Fable 对项目全局优化，随后明确“持续负责后面的任务，一直到晚上 8:45”。后者覆盖只做一次复盘的早期范围。见 `user-claude-temporary-fable-global-optimization-20261013.md:1–6`、`user-fable-control-until-2045-20261013.md:3–11`。

| 项目 | 实际授权与实现 |
|---|---|
| 目标 | 远端 Claude 主管，不是当前 Codex 模型 |
| 精确请求模型 | `claude-fable-5-1`，不能按拼写自行映射为 Opus/Sonnet |
| effort | max，沿用主管要求 |
| 会话 | 保留原固定 supervisor session、workspace、transcript |
| 截止 | 2026-10-13 20:45 Asia/Shanghai，即 12:45 UTC |
| 截止后基础模型 | `claude-opus-5-5` |
| 不在该次变更内 | 全局 Claude 默认、Codex 模型、所有 worker 型号、其他项目 |

确认现有模型通道时，先读指定目录里的目录/配置元数据和已有调用回执，不猜 API、不读取凭据。历史 `fable_catalog_readonly.py` 只查可公开的模型标识；目录里出现型号只是可用性线索。`probe_fable51_once.py` 随后进行了一次主管安排的实际无工具 `ping`，显式 Fable/max、`--no-session-persistence`、90 秒执行上限、110 秒 SSH 外围上限；这会产生模型调用，不能每次查状态都重跑。它将回执保存为 `fable51-availability-receipt-20261013.json`。

该回执 `is_error=false`，modelUsage 返回 Fable，实耗 6.367 秒。这证明指定通道返回了该模型标签；不证明供应商真实后端或实际推理档位。请求值、CLI 接受值、响应标签、后端独立证据要分别表述。

### 实际切换做了两处，不能只改归档标签

`activate_fable_supervisor_window.py:6–38` 实施了以下项目级变更：

1. 读取已有 Fable 成功回执、确认无本地 pending，且仍在授权时窗内。
2. 在远端持有原主管会话锁，确认 session 和基础 Opus 绑定；备份专用 bridge，语法检查后原子替换唯一 MODEL 行。
3. 同步本机 `connect.py` 的请求归档标签，保留时间条件；不覆盖原归档。
4. 保存前后脚本 SHA、时窗、基础恢复型号及“worker/global 未改”的部署回执。

实际条件为：

```python
MODEL = "claude-fable-5-1" if time.time() < 1791895500 else "claude-opus-5-5"
```

本机 `connect.py:22` 同步相同条件。只改本机 record_request 中的 model 字段，不会改变远端实际调用；只改远端而不改本机，则归档会把请求型号记错。

`fable-supervisor-window-receipt-20261013.json` 记录 `worker_model_config_changed=false`、`global_config_changed=false`、`local_request_archive_model_window_updated=true`。本地 `bridge.py` 副本仍可能是旧固定 Opus 版本，不能拿它覆盖线上后续修复；现行远端代码/回执与哈希才是部署依据。

该脚本会因备份已存在、已有 pending、基础模型不匹配或时窗过期而拒绝再次执行。不要删备份或改 cutoff 来绕过。新时窗必须来自当前授权，按现有方式准备单独可回退的项目变更，不能把历史脚本包装成 `switch-model` 通用命令。

到期条件是在各 Python 进程加载时选型，不会中途更换已启动请求的模型。部署过条件不等于已验证截止后切回：截止后先收原在途请求，再从新进程 check/实际授权请求回执确认基础型号；不能仅凭本机时间或旧 receipt 宣称恢复完成。查看状态、恢复 pending 与新增模型调用仍遵守唯一写入者和原请求规则。

## 额度与单次 usage 是两回事

`modelUsage`/token usage 是一次回答的返回数据，不是账户余额、项目账单或剩余额度。源对话初次用 `claude --help` 与 `claude auth status --json` 查公开元数据，确实没有得到额度字段；不能把字段缺失解释为零额度或猜一个非公开 quota API。

现有两个只读诊断入口：

```powershell
py -X utf8 "<BRIDGE_DIR>/claude_quota_readonly.py"
py -X utf8 "<BRIDGE_DIR>/claude_usage_pty_readonly.py"
```

前者仅 help/auth 元数据，SSH 上限 55 秒；后者才读取交互 CLI 的内建 `/usage`。这些命令仍会连接已经授权的远端服务器，应在用户需要查看额度时使用，不作为每轮桥接必经检查。源脚本输出文件名固定在历史日期；连续使用前为本轮留独立回执或保留已有原件，不能将新快照覆盖后称为历史证据。

### `/usage` 的真实可用方法

`claude_usage_pty_readonly.py:5–75` 使用远端独立诊断 PTY：

- 加载主管绑定的 BINARY/ENV，在既有授权 workspace 启动交互 CLI；不传 `--resume`，不向主管会话发提示词。
- PTY 大小 120×40，`TERM=xterm-256color`；禁工具/MCP、dontAsk、`--prompt-suggestions false`。
- 观察到正常输入界面才发送内建 `/usage\r`；这不是 `claude -p /usage` 模型问题。
- 主读取窗口 35 秒；SSH `ConnectTimeout=10`、`ConnectionAttempts=1`、外层 70 秒。读到 usage 后短暂等界面完成，再保存经脱敏的公开额度行。
- 清理的仅是自己创建的诊断子进程：两次 Ctrl-C、TERM、有界 waitpid，必要时 KILL。不能杀主管或 worker 来退出额度页。
- 清除 ANSI/OSC；auth 中的账号信息只用于本地替换脱敏，输出不保留 email、组织标识、私有项目路径或凭据。

成功标志字段是 `usage_slash_sent=true`、`model_prompt_sent=false`、`diagnostic_no_resume=true`，并且 `public_usage_lines` 有实际额度文字。当前脚本没有解析成数值，`remaining`、`reset_time` 仍为 null；不能因此说“未取到”，也不能把 null 当作零。按实际公开 used 百分比计算 remaining = 100 − used，并保留窗口、查询时间及未显示项。

### 真实卡点：终端选择与子进程清理

历史启动曾卡在目录信任页。盲按 Down 会把已选 Yes 切到 No；终端压缩空格还使简单字符串选择匹配失败。修复后先剥离 ANSI、去空格，识别最新 `❯` 选中的 Yes/No，再作唯一必要动作；无法观察选项就停止，不盲确认。

这一处理只适用于已授权的既有项目目录。出现新目录、login/onboarding、不可观察的选项或需要改全局设置时，不把额度查询扩大成登录/权限变更。成功历史调用的 `known_project_trust_choice_sent=false`，表示那一次未再发送信任确认；不宜笼统宣称“每次都自动信任”。此前清理自身诊断进程的阻塞已改为有界 waitpid，不延长主管调用预算。

### 历史成功快照及界限

`claude-usage-summary-20261013.md` 和 `claude-usage-pty-readonly-receipt-20261013.json` 记录的查询时刻为 2026-10-13 07:33:05 UTC，即上海时间 15:33:05：

| 窗口 | 已用 | 计算剩余 | 当时显示的重置 |
|---|---:|---:|---|
| Current session | 21% | 79% | 未显示，保留未知 |
| Current week / all models | 28% | 72% | 10月13日12:59 UTC，即20:59上海时间 |
| Current week / Fable | 4% | 96% | 同上 |

这些是当时的账户窗口读数，不是当前余额，也不是该项目独占的资源预算。不要因为周额度在20:59重置，就延长用户20:45结束 Fable 主管的授权时窗。

## 高并行和容量不足怎么承接

源用户当时明确授权高并行，主管据此安排不同模型/档位和独立任务；该授权不能自动转移到新用户、新项目或任意后续批次。`max_workers=4` 是派发器的本机并发数，不是账户支持无限模型并发的证明。模型额度剩余多也不证明首响应快或大任务能按时返回。

Codex 子代理报 model capacity，不能据此把已经提交的远端 Claude 任务记为 failed 或重发。先区分本地执行者不可用、SSH transport_unknown、远端执行超时、公开产物不完整。原任务由唯一收取者继续按原 ID 收件；受影响的本机工作按主管已给出的替代安排交接。没有条件变化时不反复 followup；不私自降模型、扩大额度或重开方向任务。

相关失败和恢复见 [并行与文件落地](parallel-execution.md)、[运行故障与后台恢复](runtime-recovery.md)。源 `user-claude-temporary-fable-global-optimization-20261013.md:15` 保留了同类 capacity 失败后停止盲 followup、由 root/ops 接手机械工作的历史事实；它不是以后无条件接管业务方向的许可。

## 来源定位

- 用户时窗：`user-fable-control-until-2045-20261013.md:3–11`。
- 项目级切换：`activate_fable_supervisor_window.py:6–38`、`connect.py:20–33`、`fable-supervisor-window-receipt-20261013.json`。
- 型号可用性：`fable_catalog_readonly.py`、`probe_fable51_once.py`、`fable51-availability-receipt-20261013.json`。
- help/auth 层：`claude_quota_readonly.py:6–29`。
- PTY `/usage` 与脱敏：`claude_usage_pty_readonly.py:5–83`。
- 历史额度：`claude-usage-summary-20261013.md`、`claude-usage-pty-readonly-receipt-20261013.json`。

这些来源是本地保存的代码和公开历史回执。本页没有重新调用远端，不能证明现在的部署、额度或时窗后的恢复状态。
