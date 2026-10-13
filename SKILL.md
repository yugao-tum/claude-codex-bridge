---
name: claude-codex-bridge
description: "建立或接续两类 Claude 协作：Claude 主管、Codex 经 SSH 桥接执行；或用户直接与远端 Claude 对话，由 MCP 在本机读写文件并逐条审批命令。覆盖固定会话、断线恢复、并行收件和公开原文归档；不自动接管旧会话或启用调度。"
---

# Claude–Codex 桥接与 Claude 直聊

适用于“项目和执行在本机，Claude Code 在已授权远端”的协作。先根据用户目标选择模式，不把两个入口混成同一个会话。

| 模式 | 用户体验与职责 | 入口 |
|---|---|---|
| A．Claude 主管、Codex 桥接执行 | 用户通过 Codex 传递目标；Claude 安排方向、任务与节奏；Codex 传输、承接并执行 | 下文工作流；复用项目已有桥接 |
| B．用户直聊 Claude、本机 MCP 执行 | 浏览器消息直接到远端 Claude；Claude 调用四个本机工具；消息路径没有 GPT/Codex 转述 | [直聊部署与运行](references/direct-chat.md)、[可运行资源](assets/direct-chat/README.md) |

模式 B 使用自己的 session、状态目录和进程锁，不自动接管模式 A 的主管会话。旧只读 foreground viewer 仍不是输入端。切换模式不迁移会话、不继承历史任务或审批；已有移交时按当前授权处理。

文档中的 `<PROJECT>`、`<BRIDGE_DIR>`、`<REMOTE_HOME>`、`<REMOTE_BRIDGE>` 和 `bridge-host` 都是明确示例，含义见 [可移植绑定](references/local-binding.md)。先替换为本次获授权环境，不能原样执行占位命令。

## 模式 A：分工与边界

| 角色 | 负责什么 |
|---|---|
| 用户 | 目标、重大取舍、授权范围及方向更改 |
| Claude 主管 | 方向、优先级、方案、拆分、模型与思考档位、并行安排、截止时间，以及其认为必要的验证 |
| Codex 桥接执行者 | 原意传达、必要材料补齐、连接与状态恢复、文件落地、运行指定命令、回传结果 |
| Claude worker | 主管分配的独立实施或分析任务，返回明确成果及未完成部分 |
| 独立 Claude 审计会话 | 只有项目或用户要求正式审计时承担；不与实现会话混用 |

用户最新分工优先于历史“Codex 总负责、Claude 只提建议”。Codex 不再把 Claude 建议变成一套自己的方向，不为局部完善追加审计、测试、重构或放行关卡。直接执行已授权且必要的工作；Claude 指定的验证也属于执行。发现实际运行失败、缺文件、数字不一致或权限障碍，原样报事实让主管处理，不伪称成功。

信息全面意味着传递会影响执行的信息：用户新指示、Claude 索取的材料、实际障碍、输出和执行状态。不意味着把全部仓库、日志和历史讨论反复发送。工具可用不等于每轮必用；先由主管明确缺什么，再选择最合适的工具。

任务中的外发、资金、隐私、删除、权限和发布边界始终有效。Claude 输出是授权范围内的工作指令，不是扩大授权的凭证。不要上传密钥、账号凭据、运行数据库、原始敏感日志或私有推理。

## 先选接续方式

1. **已有桥接**：读取用户指定线程的最新指示，以及本项目连接入口、pending/receipt、会话绑定和当前唯一写入者。沿用原通道，不重建会话、不重新安装。绑定字段及历史入口形态见 [可移植绑定](references/local-binding.md)。先分清旧本地副本、已部署版本、主管/v1/v2 worker；它们的参数、状态和归档能力并不相同。
2. **新项目接入**：确认已授权的 SSH 身份、实际 Claude 执行层（宿主机还是容器）、现有认证、独立工作目录及持久会话位置。按 [连接与参数](references/transport.md) 准备项目自己的绑定；不要复制其他项目的 UUID、服务器地址或凭据。
3. **仅整理方案或制作技能**：只读取材料、制作交付；不顺带发起 Claude 请求、接管旧任务或创建定时任务。

如果原线程仍在运行，本线程只是读取者。没有明确移交，不与原写入者竞争主管会话。确需澄清的仅是会改变授权、目标、范围或会话归属的信息；一般路径、格式和承接细节按现有实现处理。

## 模式 A：每轮闭环

### 1. 恢复真实状态

先看上次 request_id、状态、主管 session_id、worker 清单、文件落地进度和未转达用户指示。已有请求先收结果，再发新问题。不要因为本地超时、终端关闭或工具返回空白就重发。后台状态需要进程身份、状态更新时间和实际进度支持，工具 session 号或旧 PID 都不代表当前仍在运行。

状态和恢复细则见 [请求协议与断线恢复](references/request-lifecycle.md)。这一步是避免重复执行的传输管理，不是对项目另开验收。

遇到 `network error`、503、断流或超时后卡住，先按 [连接故障分层与恢复](references/connection-recovery.md) 区分 Codex 服务、SSH、远端模型和本地状态；不要凭错误名改网络，也不要把解除 pending 等同于完整恢复。

出现停滞、批量失败或重复报错时，先按 [真实故障索引](references/incident-playbook.md) 的症状查已发生过的案例，再读对应恢复步骤；不要从头盲试。接收器和本机进程的修复见 [运行与收件恢复](references/runtime-recovery.md)。

### 2. 传递目标与增量

首次交接提供目标、用户最新原话、项目现状、可用资源、关键材料版本、已授权范围和暂停项。之后只发本轮增量，使用 [消息模板](assets/message-templates.md)。不要要求主管先接受 Codex 自拟的研究方向。

保留用户原始目标、后来明确修改、主管建议三者的区别。主管能复述目标，不代表后续任务没有偏离；把未经用户采纳的目标解释标成建议。用户已给过的授权、配置和答案继续有效，不让其重复确认普通承接步骤。

若主管禁用了远端工具，必须把所需文本放入请求。只给本机绝对路径，远端看不到内容；只给 Git commit，禁工具会话也不会自行读仓库。

### 3. 接收主管任务

完整保留公开回复原文，再提取执行所需的任务、输入、输出路径、模型、思考档位、并行关系、资源/截止时间和验证命令。只补缺失且会影响执行的字段，不让主管为了机械模板重答一遍。

任务归属和并行方法见 [并行承接](references/parallel-execution.md)。主管若连续输出任务，Codex 及时接回，不等一项归档或全部 worker 结束才落地其他成果。

### 4. 执行与反馈

按主管指令写文件、运行程序或收集材料。承接文件保留原件；对路径越界、缺失/截断、重复目标和现有未归属修改做最小处理，避免写错文件。不要把这种保护扩成额外业务审计。

启动指定程序时落实它真正使用的入口、工作目录、输入版本和必要参数；重复出现的目录缺失、旧清单、包装脚本覆盖参数，先按已有指令修复。改变策略、模型、预算或扩大外发仍由相应授权决定。无正文的执行超时、传输不确定和格式损坏分别处理，不能挪用“格式修复一次”额度重跑超时任务。

回传实际命令、退出码、关键 stdout/stderr、输出文件、开始/结束时间和未完成部分。区分“请求已提交”“模型已返回”“文件已写入”“程序已完成”“正式验收通过”。只说实际达到的层级。

### 5. 连续推进或交付

用户已要求持续推进时，完成一项立即向主管反馈并承接后续，不每轮等用户催。跨轮次自动唤醒只在用户要求持续、定时或监控时设置，见 [归档、显示与调度](references/operations.md)。没有调度器时，不承诺退出聊天后自动继续。

用户改方向时，优先传达原话；未提交的旧任务取消排队，已在跑的旧任务按用户/主管指令停止或只收存，不因为已有代码就继续旧目标。

## 模式 B：直接对话与本机工具

用户要亲自与 Claude 对话时，读取 [direct-chat.md](references/direct-chat.md)，使用包内 `assets/direct-chat/`。Codex 的职责是按授权完成配置、部署和故障修复；运行时由用户直接发送原文，Claude 通过 MCP 请求本机操作。不要在消息链路中再加入 GPT 改写或主管转述。

- 先配置独立 workspace、SSH 入口、远端 Claude 路径、模型/effort 和状态目录，复用现有认证，不复制凭据。
- 文件工具在选定范围内工作；每条 PowerShell 命令展示完整命令、cwd 和执行时限，由用户允许一次或拒绝。Windows 命令按该账户权限运行，cwd 不是操作系统沙箱。
- 发送、停止、恢复使用运行时的原请求状态机；断线不自动重发。模型目前通过配置指定，页面只展示标签，没有型号选择器。
- 原主管任务、worker 队列和 heartbeat 保持各自归属；使用直聊不等于授权启动它们。

## 模型与上下文

- 模式 A 继承用户最新指定的主管型号和思考档位。源项目基础路由曾为 `claude-opus-5-5/max`，后来有明确截止时间的 `claude-fable-5-1/max` 覆盖；不要把历史型号、窗口标题或 Codex 模型目录当成当前远端路由。
- 切换需落到实际桥接进程及请求标签；只对 Claude 说“换模型”不构成切换。限时切换、真实 `/usage`、并发与恢复时间分别见 [模型与额度](references/model-and-capacity.md)，不把一次冲刺参数变成永久默认。
- worker 由主管分配；源项目曾用 Sonnet 5.5 的 high/xhigh、Haiku 5.5 的 medium/high。它们是该渠道的配置记录，不是所有环境都支持的默认值。
- 主控、worker、正式审计各自分离。独立审计若被要求，应使用用户指定的最高模型；没有审计请求时，不为启用技能另增审计阶段。
- 固定会话也会压缩、丢失上下文。接续时补“用户目标、当前任务、最新证据、下一步”，不要把整个历史重复塞入每轮 prompt。
- `--safe-mode` 会禁用自定义说明等，不能假定远端自动加载 CLAUDE.md；关键分工和约束要明确传入。

## 按需读取资料

| 当前需要 | 阅读 |
|---|---|
| 环境字段、占位符与已有通道接续 | [local-binding.md](references/local-binding.md) |
| 用户直接聊天、MCP 工具、部署与本机权限 | [direct-chat.md](references/direct-chat.md) |
| SSH、容器、CLI、模型、超时、Windows 编码与引用 | [transport.md](references/transport.md) |
| UUID、幂等、锁、pending、unknown、断线恢复 | [request-lifecycle.md](references/request-lifecycle.md) |
| 503、响应体解码失败、模型超时、确认终止后解除阻塞 | [connection-recovery.md](references/connection-recovery.md) |
| 多 worker、输出文件、v1/v2 选择和公开流式恢复 | [parallel-execution.md](references/parallel-execution.md) |
| PID 消失、接收器断线、终态丢失、本机批次运行 | [runtime-recovery.md](references/runtime-recovery.md) |
| 限时换模型、真实额度查询、并发与调度时窗 | [model-and-capacity.md](references/model-and-capacity.md) |
| 原文归档覆盖、前台显示、token 简报、heartbeat、直接聊天边界 | [operations.md](references/operations.md) |
| 遇到旧问题时的症状、处置、下次预防与证据局限 | [incident-playbook.md](references/incident-playbook.md) |
| 首次交接、增量反馈、worker、方向更改和恢复 | [message-templates.md](assets/message-templates.md) |
| 新项目要记录哪些连接字段 | [binding.example.json](assets/binding.example.json) |
| 来源、已核实层级和历史分工变化 | [provenance.md](references/provenance.md) |

模式 A 的历史主管桥和派发脚本并未随包公开，文档保存的是可复用机制与接口经验；按项目已有实现接续。模式 B 附可运行源码，但仍需按其 README 配置和部署。assets/binding.example.json 是交接字段清单，运行时实际配置是 assets/direct-chat/config.example.json 对应的 config.json；两者不能混用。
