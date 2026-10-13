# 请求协议、锁与断线恢复

## 标识不要混用

| 标识 | 作用 |
|---|---|
| Codex thread_id | 用户看到的 Codex 聊天 |
| Claude session_id | 主管的持久多轮对话；固定不变 |
| request_id | 一次传输/模型调用的 UUID |
| logical_task_id | 业务任务，可有多次 attempt |
| attempt | 主管决定修复或重做后的尝试序号 |
| worker session_id | 独立 worker 的会话，不得复用主管 UUID |

同一 request_id 必须绑定同一 prompt SHA-256、会话和 timeout；worker 还绑定型号与 effort。重连、重复查询，以及**同一 request_id 且绑定字段一致**的重复提交只回既有状态/结果，不再启动另一轮模型。仅正文相同但换了 UUID，不享有这条幂等保证。业务重试则保留旧请求，增加 attempt 并用新 UUID；这两个场景不能混淆。

## 源主管协议

基础远端 `bridge.py` 从 JSON stdin 接收 `check / submit / result / call`。`worker <UUID>` 是显式恢复入口。日常只通过本机 `connect.py check|chat|status` 操作。后续独立 resolution 是针对已有请求的证据处置，不能由旧本地副本推断部署端全部能力；见 F。

提交载荷：

```json
{
  "request_id": "本次新生成的 UUID",
  "prompt": "完整非空 UTF-8 文本",
  "timeout_seconds": 900
}
```

`timeout_seconds` 省略用源主管基础默认 900；基础 bridge 只接受整数 300 或 900。这不是 worker 的通用预算限制。prompt 上限 **131072 字节（128 KiB）**，按 UTF-8 字节计，不是中文字符数。本机 `chat` CLI 没有 `--timeout` 或 `--request-id` 参数，不能捏造。

查询载荷：`{"request_id":"原 UUID"}`。`check` 用 `{}`。主管回执的重要字段：

```text
request_id, session_id, prompt_sha256, timeout_seconds, deadline_at,
status, queued_at（若有）, started_at, finished_at,
exit_code, models, result, usage, model_usage, api_ms, error
```

先保存本地 pending，再提交。远端先存请求正文及 queued 回执，再启动后台进程。原子保存用临时文件、flush/fsync、同目录 replace；目录和文件使用受限权限。**pending 是恢复锚点，不是清理垃圾。**

## 状态与动作

| 状态 | 含义 | Codex 动作 |
|---|---|---|
| `ready` | 入口检查返回；还应看 transcript_exists/unresolved_requests | 不等于模型已回答或项目成功 |
| `queued` | 请求已存，可能尚未起算模型 | 继续查询；未启动的遗留请求按下述恢复 |
| `started` | 模型可能正在运行 | 不重发；承接其他独立任务 |
| `completed` | 桥接层完成判定 | 读取正文、接收成果；不等于文件完整或业务验收 |
| `failed` | 明确模型错误或派发前失败，也可能是人工处置后的历史状态 | 保留错误、phase 等；交主管决定，不自动重试 |
| `unknown` | 模型可能处理过，结果无法确认 | 保留锁关联与 pending，恢复原输出；不重调模型 |
| `resolved` | 独立恢复记录处理了原不确定状态；需读取 resolution 类型 | 原回执/pending 证据保留；confirmed_terminated 与 confirmed_not_registered 不互换，不表示业务完成 |
| `busy` | 主管会话锁已被占用 | 本次未开始；交原写入者接续 |
| `blocked` | 原会话缺失/身份不符或别的未解决请求 | 只处理该传输障碍；不新建替代会话 |
| `not_found` | 查询目标没有匹配请求/回执 | 先确认路由、目录、ID及原件，不能直接视为从未发送 |
| `transport_unknown` | worker 收取时网络失败 | 本地观察状态；不代表远端 failed |

源主管 completed 判定包括：子进程退出 0、`is_error is False`、返回 session_id 正确、modelUsage 包含指定型号、transcript 存在。只证明响应符合桥接条件。返回模型标签不是供应商后端独立证明。

## 三层串行控制

1. **人员/代理层**：主管会话只有一个写入者；收件、归档、文件承接可以并行。
2. **本机层**：`connection.lock` 使用 Windows `msvcrt.locking`；已有 `connection-pending.local.json` 时拒绝新的 chat。
3. **远端层**：`conversation.lock` 使用 Linux `fcntl.flock`；queued/started/unknown 及“请求已存但回执缺失”的状态都占用未解决请求集合。

锁冲突不通过删锁文件修复。不能在旧 worker 仍可能存活时新起一个同会话调用。一次终端结束也不能证明锁拥有者或远端请求结束。

## 恢复顺序

### A. 正常中断或本机 55 秒超时

1. 留住本机 pending、原 prompt 和原 UUID。
2. 调用 `connect.py status`；queued/started 继续等，completed 收原文。
3. `unknown` 时取原 receipt、对应 stdout/公开 transcript 和进程状态，仅恢复该请求的公开输出。
4. 如果无法确认是否执行过，向主管/原写入者报告不确定状态，不删 pending 后新发问题。

主管会话仍被占用、暂时无法调用时，将待反馈内容保存在本地，告知当前用户或本轮已有桥接写入者；不要为“反馈故障”再向被锁的主管发一条请求。给另一用户聊天发消息仍需已有明确授权。待原请求恢复后由唯一写入者转达。

### B. 请求已持久保存，但 worker 尚未启动

远端 `result` 可返回 `queued` + `recovery_needed:true`。只有原请求完整、ID/hash/会话一致、未发现 started/unknown、无其他未解决请求，并确认尚未调用模型时，才按已有实现显式运行 `worker <原 UUID>`。它沿用原 deadline，不重新给 900 秒。到期应留失败事实，再由主管安排新 attempt。

### C. not_found

先确认是否连接了正确容器、持久目录、会话和服务器。确认原远端确实未接收该 UUID 后，才可使用**原 ID、原正文、原参数**恢复提交。标准 `connect.py chat` 会生成新 ID，因此不是这个恢复动作的正确入口；由原写入者使用已有低层 `remote('submit', payload)`，并保留本机状态。不要靠删除 pending 触发新 UUID。

实际案例 `resolve_77f4_not_registered.py` 的证据组合是：**持有原主管排他锁期间**，正确远端的 request/receipt 均不存在、无匹配进程、原 transcript 没有对应公开 user prompt SHA；另存 `confirmed_not_registered_no_public_user_prompt_no_matching_process` resolution，并归档原本机 pending。它未调用模型，也没有把请求写成成功。这个脚本绑定单条历史请求，不是通用修复入口；单独的 not_found 或锁空闲不满足其证据条件。解除后再由唯一写入者按原任务授权决定接续内容。

### D. 正文不完整、JSON 损坏或 worker 超时

先取服务器保存的原始输出，分清 UI 展示截断与模型本身输出缺失。完整输出仍缺文件就报告主管，由主管决定补一个文件、改用 diff、缩小任务或增加新 attempt。其他完整成果继续接入。

先分失败类别再谈重试：`transport_unknown` 查原请求；执行超时保留预算和耗时；真正正文格式缺失才用主管给的格式修复次数。新的 attempt 记录 `logical_task_id / attempt / part / controller_request_id` 和授权依据，保留旧回执。不能把零公开输出说成零 token，也不能把旧任务迁往 v2 叫作原请求恢复。分类和 v2 完整文件恢复见 [parallel-execution.md](parallel-execution.md)。

### E. 迁移工作目录或容器

这不是普通重连。确认新挂载能看到原 session marker、transcript 和请求回执；迁移由获授权操作人完成，保留原件与会话 UUID。不要把旧 Pi planning 会话、Claude Code 会话或两个不同工作目录默认为同一个上下文。

### F. 已确认终止，但 unknown 永久挡住接续

复用已部署的独立 resolution 机制，条件和证据见 [连接故障分层与恢复](connection-recovery.md)。超时或锁释放单独都不足以解除阻塞；不能把 unknown 改成 failed，也不能删除 pending 后重发。源项目入口只支持已列明的历史请求，不是通用清锁按钮。

## 不做的恢复捷径

- 不用 `--continue`、最近文件名或时间戳猜主管会话。
- 不把 `unknown` 改为 failed 来解除锁并继续发请求。
- 不删除请求/回执、不覆盖原失败结果、不把新 UUID 叫“原请求恢复”。
- 不把“同内容重复提交不会重跑”的幂等保证扩大成任意失败都能自动重试。
- 不让固定会话故障阻止无依赖的文件收取、归档和主管已授权的本地任务。
