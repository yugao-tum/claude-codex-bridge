# 原文归档、前台显示、用量与持续推进

本页主要描述模式 A 的历史主管/worker 归档与只读 viewer。模式 B 已提供独立直聊程序及自己的 state/events/request 记录，见 [直聊模式](direct-chat.md)；两套目录和恢复入口不能混用。

## 1. 公开原文和状态归档

源实现 `dialogue_archive.py` 在发送前 `record_request()`，收到回执后 `record_receipt()`；`render_index()` 更新入口。

```text
<archive-root>/
  index.md
  sync-state.json
  turns/<request_id>/
    request.json
    receipt.json
    states/<sha256>.json
    dialogue.md
  public-messages/<session>/...
  foreground/YYYY-MM-DD.log
```

- 请求原文不覆盖；状态按内容哈希保存，最新回执另存。迟到的无正文状态不能盖掉已保存完整回答。
- 文件写入为同目录临时文件 → flush → fsync → replace。用户看到的 Markdown 是可读副本，原 JSON 仍保留。
- 主管正文来自顶层 `result`；worker 优先取 `public_result.result`。不完整的 `public_output` 标成 `incomplete_public_output`，不冒充完整答案。
- `sync_remote()` 只读取回服务器请求、回执和固定会话的公开 assistant text。失败保留本地旧档案，下一次再补；不会调用模型。
- 原件保留时间、清理和搬迁由用户/项目决定，不把 `dist` 当成可随手清空的构建缓存。

源归档请求白名单：

```text
request_id, session_id, prompt, prompt_sha256, model, effort, label,
created_at, queued_at, timeout_seconds, controller_request_id,
channel, archived_at
```

源归档回执白名单：

```text
request_id, session_id, status, queued_at, started_at, finished_at,
model, effort, models, reported_models, returned_session_id,
elapsed_seconds, exit_code, returncode, result, error
```

**限制**：当前白名单没有 usage、source_manifest、logical_task_id、attempt；这些字段应保留在原始任务和回执中。不要从归档副本推断它保留了所有运行元数据。也不要为了补这些字段把隐藏推理、签名或 debug 全文导出。

2026-10-13 新增的公开流式 worker v2 还有一项覆盖差异：源 `dialogue_archive.py` 的远端补同步仍只扫描旧 `workers/claude-2.1.296/implementation`，没有自动扫描 `implementation-v2`。v2 canary 由调用者显式执行 `record_request()` / `record_receipt()` 接入本地归档，不代表本机离线期间的全部 v2 结果都会被旧同步入口补回。

新增通道须保留自己的原始 task/receipt、远端目录、收取入口和负责收件者。v2 的 `public-partial.txt`、`recoverable-files.json`、首公开内容时间、终止原因等另在该任务目录；原归档白名单没有全部这些字段。先按原 ID 通过对应通道接回，再更新可读索引，不重新提交来“补档”。具体故障对应 [事件处理手册](incident-playbook.md)。

如需要公开 transcript 片段，只提取 `type=assistant`、`message.content[*].type=text` 的正文，忽略 thinking、工具私有状态和认证信息。公开回复可逐字保存，但不保证模型正文不会回显敏感输入；源头仍须控制材料范围。

源项目手动补同步命令如下。它只读远端，但会更新本机公开档案；仅整理技能时无需运行。

```powershell
py -X utf8 "<BRIDGE_DIR>/dialogue_archive.py"
```

## 2. 前台展示与后台执行独立

用户要求“让我看到你们实时对话”时，使用现有只读 viewer。不要以整理后的报告冒充实时窗口，也不要为了展示重开一轮模型请求。

源 `foreground_view.py`：

- 先补一次公开归档，同步失败仍能继续显示。
- 只读 SSH 运行远端 `python3 -u .../live_public_watch.py`。
- 每行写前台日志并 flush/fsync，再显示。
- 显示连接中断后 **5 秒**重连；用户关显示窗口仅关闭该连接，不取消或重新派发模型任务。

源 `live_public_watch.py`：

- 从当前 transcript EOF 开始；过去的内容通过归档补同步取得。
- **0.3 秒**轮询，活动请求每 **20 秒**提示仍在处理。
- 按消息 UUID/id 去重；同一消息增长只显示新增后缀，过滤控制字符。
- 展示公开正文和 completed/failed/unknown，不展示私有推理。

这是公开消息到达后的实时展示，不是隐藏思考流，也不保证逐 token 可见。独立使用 Claude 官方流式模式时可参考 [programmatic usage](https://code.claude.com/docs/en/headless)，但现有主管使用 JSON 回执＋transcript watcher，不能在续接中随意换协议。

源项目显示命令：

```powershell
py -X utf8 "<BRIDGE_DIR>/foreground_view.py"
```

**旧显示窗不是对话输入端。** foreground_view.py 仍只读公开文本，没有接收输入或协调主管写锁。发布包现在另附 [用户直聊程序](direct-chat.md)：它使用独立 session，由用户直接向 Claude 发消息，Claude 通过 MCP 执行本机工具；不由 GPT 转述，也不把用户输入并进旧主管会话。

两种方案要分清：包内独立直聊已实现；“用户输入和 Codex 执行反馈共同写原主管”的通用 dispatcher 仍未提供。后者若另获授权实现，须区分 `source=user/codex_feedback/claude_reply`、关联任务/request_id，并共用唯一写入者。忙碌时保留草稿，不把“待发送”写成“已提交”；不能另开 `claude --resume` 竞争旧会话。当前直聊程序忙碌时拒绝新发送，不自动排队，也不把普通回复任意解释成 shell 命令。

**窗口标题不是型号证据。** 源 10 月 13 日前台日志仍显示固定 `Opus 5.5 / max` 字样，而主管后来存在用户授权的临时 Fable 窗口。报告当前型号时读取实际请求/回执和有效期，不复述旧标语；临时窗口与到期恢复见 [模型与额度](model-and-capacity.md)。显示重连成功也只证明当前显示连接可用，不能证明模型完成或任务已落地。

默认不新开可见外部窗口；用户明确要求前台时再打开。使用 `Start-Process` 启动不需要用户交互的后台辅助程序时用 Hidden。可见前台也不能被描述成远端服务器已经直接控制用户电脑。

## 3. token 简报

只在用户要求用量/费用时汇总已有记录，不额外调用 Claude 来生成统计。源项目未发现通用独立统计脚本，不能虚构 `usage_report.py` 之类入口。

1. 明确时间范围、主管/worker 范围、排除项和时区。
2. 按 `request_id` 去重。重复收取的同一回执只算一次；新 attempt 是新的请求，保留失败与未知。
3. 主管取每次 `usage`；worker 取 `public_result.usage`。
4. 累加 `input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`。
5. 不再累加 `model_usage/modelUsage` 中的会话累计值；不叠加 iterations、thinking_tokens 或缓存子类别。
6. 缺字段是未知，不按零。输出独立请求数、有 usage 数、缺 usage 数、缺时间戳数。
7. 按返回模型标签分组。混合或无法归属的请求单列未知/混合，不把总量硬塞给请求型号。

合计可表达为“普通输入＋输出＋缓存读取＋缓存写入”，但这些不是同一价格单位。无实际账单不声称真实费用。`total_cost_usd` 若出现也须标为客户端/渠道估算，不能等同账户扣款。

用户问“还剩多少、何时刷新”时，进入 [模型与额度](model-and-capacity.md) 的真实 `/usage` 流程。每请求 token、账户额度窗口和项目实际账单是三种数据；不能互相代替。源 10 月 13 日 `/usage` 已在独立诊断 PTY 成功，旧的“auth/help 没有字段”不再是该次查询最终结论。公开页面可能已有比例和重置时间，而回执的通用 `remaining/reset_time` 仍为 null，应读取已保存的 `public_usage_lines` 并注明查询时间。

## 4. Codex 工具与适用方式

| 需要 | 工具及关键参数 | 使用约束 |
|---|---|---|
| 读取用户指定旧对话 | `mcp__codex_app__read_thread({threadId, turnLimit:10, includeOutputs:false})` | 服务端当前 turnLimit 最大10；按返回 cursor 读旧轮次，只展开相关输出 |
| 找到线程 | `list_threads` | 用返回的真实标题识别，不把摘要当标题 |
| 观察已有线程进度 | `wait_threads({targets:[{threadId,hostId,afterCursor}],timeoutMs:0})` | 瞬时快照用0；等待按宿主范围设置，避免重复全文 read_thread |
| 给另一用户聊天发消息 | `send_message_to_thread` | 必须有用户明确授权发给该聊天；只读链接不等于允许接管或发消息 |
| 本轮内部并行 | `collaboration.spawn_agent/send_message/wait_agent` | 共享目录，任务写入范围需分离；主管唯一写入者不变 |
| 执行本机现有桥 | `exec_command` | 工作目录和 argv 明确；返回工具 session 后用 `write_stdin` 接输出，但工具句柄不作为后台进程存活证据 |
| 编排工具 | `functions.exec` | 独立读取可 Promise.allSettled；提交/依赖动作串行；只输出必要摘要 |
| 长回执解析 | `ctx_execute_file` / `ctx_batch_execute` | 输出派生结果，原文保留文件；不拿 sandbox 写文件当宿主持久化 |
| 展示归档 | `open_in_codex` 文件目标 | 显示真实已保存文件，不能代替读取和确认产物 |

工具名以当前运行环境为准；工具未加载时用 `tool_search` 查找，不调用臆造的“Claude 连接 MCP”。正常业务不需要每轮遍历全部工具。

## 5. heartbeat 与接续

用户已明确要求持续推进、定时或监控时，优先用当前线程 heartbeat。先搜索 `automation_update`，检查现有自动化再更新，不另建重复调度。

- 采用 `kind:heartbeat`，名称和 prompt 明确项目、主管会话/绑定、唯一写入者、当前目标、暂停边界、pending 优先恢复、原文归档、何时通知。
- 源线程曾使用每 5 分钟接续；这是历史运行安排，不是每个项目默认。按当前用户意图和主管节奏设置工具所需的计划字段。
- 源 10 月 13 日按用户加速要求临时改为 1 分钟，约定额度重置后恢复 5 分钟；这与临时主管型号恢复是两个独立截止点。记录各自的绝对时间和时区，到期更新已有自动化，不从历史数字自动建立新计划。
- 保存 prompt 用人能读懂的指令，参考 [模板](../assets/message-templates.md)。没有变化或可行动事项保持安静，仅在成果、重要失败、交付或需要决策时通知。
- 改方向时更新已有 prompt，移除旧交付目标；保留原记录。不用旧 heartbeat 把用户已停的工作又启动。
- 如果用户只要求“封装技能”，不创建/修改调度，不启动旧项目。
- 本机 Codex/主机离线与远端任务完成是两回事：已经提交的远端后台任务可能继续，但本机不在线时不能保证新的任务派发与承接。

接收器、模型 worker、项目运行进程和聊天 heartbeat 是不同层。当前轮次有运行能力时，完整结果到达便立即承接；不能因为 heartbeat 尚未到点就搁置，也不能为加速同时启动多个 receiver。后台存活、权限可见性和同预算恢复见 [本机运行与持续承接](runtime-recovery.md)。

状态反馈写实际变化及下一动作：例如“原 ID 已收到完整正文，两个文件已保存，指定命令尚未运行”；失败则写命中的边界、保留的现场、接下来由谁处理。不要用“已同步”“正在推进”覆盖收件、落地、运行和验收的差别。没有变化按已保存通知意图保持安静，不把接续工作推成用户每轮催办。

本技能不附任何自动执行安装钩子，不因被发现或加载就发送远端消息。
