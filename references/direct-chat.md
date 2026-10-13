# 用户直聊 Claude 与本机 MCP 执行

模式 B 让用户在浏览器直接与远端 Claude Code 对话。本机程序转发原文、展示结果并执行已授权工具；**没有 GPT/Codex 参与每条消息的理解或改写**。Codex 可负责安装、配置和修复，但不是运行时消息中介。

可执行资源、配置字段、前置条件与命令以 [assets/direct-chat/README.md](../assets/direct-chat/README.md) 为入口。不要把本页的模式 B 恢复操作套到模式 A 的 connect.py。

## 1. 实际架构

```text
用户浏览器（127.0.0.1）
  ↕ HTTP 状态/原文/审批；X-Direct-Token
本机 app.py → 本机 state 与公开 events
  ↕ Windows OpenSSH 双向 JSONL
远端 remote_runner.py → 独立 request/session/workspace
  ↕ Claude Code stream-json
远端专用 Claude CLI
  ↕ 仅 laptop MCP；内置工具关闭
mcp_stdio.py → 私有 Unix socket → runner → SSH → app.py
  ↕ 四个本机工具；命令逐条审批
用户选择的本机工作目录
```

- 远端已有认证从获授权执行环境使用；不把 API key、SSH 私钥、known_hosts 或账户状态打包进 skill。
- 使用独立 SID 和持久状态，既不 resume 原主管，也不调用旧 worker 派发器。一个本机服务实例、一个活动请求；忙碌时拒绝重复发送，草稿保留。
- HTTP 仅绑定回环地址，校验 Host、Origin 和本次服务 token；状态请求携带 token，页面不使用第三方 JS/CSS。不要直接把该端口发布到公网。
- 页面所有模型/工具正文用 textContent 展示，避免将返回文本作为 HTML 执行；这不代替工具输入和路径校验。
- 顶部“本机入口已连接”只证明浏览器能读取本机状态，不证明远端模型已经响应。模型名为请求/服务返回标签，不能独立认证实际后端。

## 2. 部署与配置

1. 在独立目录放置 assets/direct-chat/ 内容，复制 config.example.json 为本地 config.json。
2. 填写 workspace_root/default_workspace、ssh_argv、remote_prefix、remote_root/remote_home、remote_claude_binary、model/effort、port/timeout_seconds。路径和 SSH 身份来自用户授权；remote_prefix 按实际宿主/容器入口配置，不假定某个容器包装器存在。
3. 先运行 `py -X utf8 config.py` 做不连网的配置检查，再按运行时 README 运行 `py -X utf8 deploy.py`。部署三个 Python 文件（config.py、remote_runner.py、mcp_stdio.py）及仅含六个远端字段的 runtime-config.json，并核对 SHA-256；不覆盖服务器默认 Claude、认证或其他项目。
4. 用 `powershell -NoProfile -File .\start.ps1` 或双击 `Start Claude Direct.cmd` 启动。浏览器打开服务实际返回的回环 URL；后台助手隐藏启动，可交互窗口只在用户需要时显示。
5. 新环境完成小范围真实请求后报告实际证据层；参考实现的历史实测不替代目标环境的部署。

config.json 是机器私有配置，不提交。模型由此配置决定，页面没有模型下拉选择器。临时模型时窗是模式 A 的历史经验，公开直聊示例不沿用私人时窗，也不按额度重置时间擅自换型号。

发布配置的请求总预算为 1–900 秒，包含模型生成、工具调用与用户等待审批；并非命令单项超时。修改六个远端字段 remote_root、remote_home、remote_claude_binary、model、effort、timeout_seconds 后重新部署，部署后的下一请求读取新值；本机字段在重启后生效。改变远端根目录或 HOME 涉及会话迁移，不能自动当作新会话重发。当前兼容基线为 Claude Code 2.1.296，其他版本先核对应参数。

首次新会话与原请求恢复不同：第一次正常请求可创建独立 SID；原 session 已存在时只 resume 对应会话。不能用最近文件或 --continue 猜会话。

## 3. 四个工具与权限

| 工具 | 能做什么 | 当前实现边界 |
|---|---|---|
| read_file | 读取选定目录内一个 UTF-8 文件 | 单文件最多 200 KiB；拒绝越界、链接/重解析点和受保护路径/名称/已识别秘密内容 |
| write_file | 在选定范围内写 UTF-8 文件 | 同样有路径与大小限制；覆盖前保留旧字节到受保护备份，不删除历史备份 |
| list_files | 列出一层目录 | 非递归，最多 200 项；省略受保护条目 |
| run_command | 在本机运行指定 PowerShell 命令 | 展示完整命令、cwd 和 timeout_seconds，用户逐条允许一次或拒绝；默认 60 秒，可请求 1–300 秒；每路输出上限 32 KiB；审批最多等待 600 秒，仍受请求总预算限制 |

这些是随包实现的技术限额，不是来源项目的业务预算。变更限额时同步工具 schema、校验和测试，不仅改变模型提示词。

**命令不是操作系统沙箱。** 被允许的 PowerShell 命令按当前 Windows 用户权限执行，可能访问 cwd 外的资源。文件工具的目录限制、秘密过滤和备份不构成对任意 shell 命令的系统隔离。审批的是该次完整命令、目录及执行时限，不是对后续命令的全局许可；Claude 收到拒绝后不得换一种方式达到同一未批准动作。

文件写入在已选择并授权的目录内自动进行；不要再对每个普通文件额外添加审批。用户切换目录只在空闲状态允许，由后端判断路径是否合法。执行前通过文件工具读取可访问范围内适用的 AGENTS.md/CLAUDE.md；所选目录外的父级说明不可访问时明确说明，不绕过边界。

## 4. 状态、停止和原 ID 恢复

本机 API：GET /api/state；POST /api/send、/api/workspace、/api/approval、/api/stop、/api/recover。POST 接受动作不等于模型完成。前端约每秒读状态；POST 等待超时后保留草稿并要求恢复，不自动再次发送。

主要状态：

- idle/completed/failed/stopped/cancelled：终态或空闲，且没有未结束 turn 时可发新消息。
- connecting/running/awaiting_approval：本轮在途。审批卡的执行时限不是模型总预算，也不是审批等待时限。
- stopping：停止已发出，等待本轮进程和连接结束；不能在此状态再次发送。
- finishing：已收到 done，但旧连接/进程尚在收尾；此时继续禁止新发送，避免新旧请求混写。
- unknown：断线或缺确认终态；保留原 request_id，先恢复。
- recovering：只查询原请求回执/公开事件，不 start 新模型；禁止并行恢复。

停止设置本轮取消事件，唤醒等待审批并通知远端；断线也停止继续派发工具、取消当前可取消的本机命令。命令已完成的外部作用不能靠“停止”撤销。

原 ID 恢复读远端 request receipt/events，接回已有公开文本和终态；不重复已经执行的工具。恢复查不到确认终态时仍保留 unknown，不能因为用户刷新页面就自动重做原 prompt。原 session、request 和 hash 不匹配应停止该恢复动作。

### 首次失败与 resume_available

ready 不是会话已经可恢复的证明。远端通过实际 transcript 是否存在且非空返回 resume_available；本机 ready 缺字段时保留原值，done/recover 以明确布尔字段更新，兼容旧成功 done 为 true。明确请求 resume=true 却找不到历史时直接拒绝；不能静默用同一 SID 建立空会话。只有 resume=false、前次已确认终止且回执明确没有 transcript、此前未续聊的首轮失败，才允许下一条用户新请求初始化同一独立 SID。曾有历史的会话即使本机随后变为 resume=false，也不能借此例外重建。unknown 不获得这一例外。

### 登记前断线：维护恢复

SSH 在远端登记请求前失败时，本机已有 request 文件，远端可能返回 not_found，页面继续显示 unknown。本版没有自动释放入口，这是需维护者处理的已知限制；单次 not_found 不能证明没有执行，也可能连错根目录、HOME 或机器。

在用户要求修复该连接时，维护者按以下步骤处理，不要删除 state、重建 SID 或直接重发原消息：

1. 保留本机 `state.json`、该 ID 的 `.request.json`、events 以及原连接配置证据，记录当前 request_id、session_id、prompt hash。先停止本机服务自己的进程，核对完整命令行，不停止其他 Claude/主管进程。
2. 核对原 SSH 主机密钥/别名、容器、remote_root、remote_home、CLI binary 与请求开始时的绑定；无法确认原绑定就保留 unknown，不改状态。
3. 在**原远端环境**持有运行时 `SessionLock`，核对该 request 目录未登记、无该请求所属活跃进程、无公开事件/结果/工具副作用；如果原请求要续聊，还须确认原 SID 的 transcript 存在。不能靠 lock 文件存在或 PID 数字单独判断。
4. 证据足以确认“未登记、未执行”后，将证据时间、身份、原 ID/hash 和判断写入独立 `resolution-<REQUEST_ID>.json`。原 request、receipt（若有）、events 和 unknown 快照原样保留。
5. 只在确认本机状态仍指向该 ID、没有新请求时，将**本机当前视图**标成 failed，并写明“原请求确认未登记/未执行，等待用户决定是否另行发送”；清除遗留 approval，保留 messages、session_id、request_id 和既有 session_started。这是一项有证据的状态修复，不是删除原失败。
6. 重启本机服务并回读。需要重做时由用户明确发送新请求、新 request_id；沿用原会话，不伪装成原请求恢复。已登记或存在副作用的情况不适用这套处置，应继续收取/裁定原任务。

这些是维护步骤，不是已附带的一键命令；未经目标环境证据确认，不执行第 5 步。

### 生命周期修复

- turn_running 覆盖从请求开始到 finally 收尾；done 先进入 finishing。
- finally 只清自己的 proc，并核 request_id 后更新状态。
- 旧连接观察线程醒来时持锁核对 proc 和 request_id，防止取消新请求。
- 工具派发同时看 stopping/cancel_event；收到 SSH 缓冲里的旧 tool_call，也不在已断线后继续写文件。
- 审批布置、用户答复和停止信号按同一锁协调；审批仅匹配当前 call_id，失效 ID 或重复批准被拒绝。
- recover 不允许重叠调用，回写时核原 request_id。

这些竞态修复有离线测试，不应扩写成“任何断线都无损恢复”。

## 5. 原文与运行记录

本机独立 state 目录保存 state.json、events.jsonl 和每请求的 *.request.json；远端独立请求目录保存请求原件、公开 events 与终态 receipt。刷新和本机服务重启从这些记录恢复历史，不以重新发 prompt 补历史。

公开对话只保留用户原文、assistant text、已公开工具调用/结果、审批决定及必要状态；不导出 thinking、签名、认证内容或私有调试日志。原文可能包含用户主动输入的敏感材料，因此整个 state/、请求原件、截图、nonce、真实 session/request ID 都属于本机资料，不随源码发布。备份、数据和状态目录也要在发布排除清单中。

模式 A 的 dialogue_archive.py 白名单和 v2 同步缺口，不代表直聊状态已自动接入那套归档；两套状态不要按文件名合并。发布只提供空配置示例和源码。

## 6. CLI 与系统提示快照

专用 CLI 使用 stream-json、--verbose、--include-partial-messages；关闭内置 tools，只显式开放 laptop MCP，工具 schema 与本机白名单一致。--setting-sources "" 避免意外加载全局设置；不要把模式 A 的“禁一切 MCP”参数照搬过来。

现行实现还使用 --system-prompt-snapshot off，每轮追加本次工作目录、工具权限和项目说明读取要求。这是静态检查后采取的预防措施；已核该 CLI 接受参数并完成真实续聊，但未真实复现快照导致的错误，也没实测切换工作目录后的提示更新。其他 CLI 版本不支持时明确适配，不能删掉参数后假称语义相同。

## 7. 不依赖 Grokbot 专有接口

只读查看 Grokbot 0.70.2 本地包时发现账户、machine、approval 绑定的 GET SSE /local-exec/requests 及 responses/connection 的 POST 通道；未发现可直接复用的开放 stdio MCP/API，也未观察到本机 TCP listener。没有解封凭据或修改该应用。

这说明当时没有可直接使用的公开接口，不证明协议永不能复用。包内方案使用自己的标准 MCP stdio relay 和 SSH，不要求 Grokbot、不复制其账号协议；以后有官方文档及授权再单独评估。

## 8. 已实测层级与未覆盖部分

来源环境已完成同一独立 SID 的三轮真实联调：

1. Claude 读取测试文件、写脚本，经 UI 允许一次后在本机执行，结果符合预期且退出 0。
2. resume 从前轮上下文继续写文件，没有为该步骤重复读取原测试输入。
3. UI 拒绝命令后，工具返回未批准，Claude 未重试或换方式执行。

刷新、服务重启后历史保留；点击恢复状态返回原 ID 的 completed，ready 事件总数仍为三，未观察到第四次模型启动。来源运行版本另有 29 项离线检查：文件工具 7、远端协议 10、本机状态机 12；公开配置化副本增加配置绑定与历史丢失保护检查后，34 项离线检查通过。后者没有发送 SSH/模型请求，不能替代新部署的真实联调。

没有做真实突然断网、休眠、长期稳定或跨型号切换；首次失败 resume 的修复来自静态分析及模拟回归，真实首轮当时成功。公开配置化版本仍要在目标环境验证，不能把源码打包等同于新机器部署通过。
