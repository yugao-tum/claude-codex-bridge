# 可移植绑定与既有通道接续

此页记录应绑定的字段、历史入口形态和复用限制，不是某个人的安装快照。源码、配置和回执要对应当前部署版本。技能加载不会连接服务器、初始化会话或执行旧任务。

## 占位符与两套配置

| 示例 | 需要替换成什么 |
|---|---|
| `<PROJECT>` | 用户选择并授权的本机项目根目录 |
| `<BRIDGE_DIR>` | 模式 A 已有桥接目录，例如项目内独立的 `dist/bridge` |
| `<REMOTE_HOME>` | 已授权远端执行账号的 home；不能从本机用户名猜 |
| `<REMOTE_BRIDGE>` | 模式 A 远端持久请求、回执、会话及专用 CLI 的根目录 |
| `<DIRECT_DIR>` | 模式 B 的独立部署副本，不等于技能安装目录或旧主管目录 |
| `bridge-host` | 已获授权、已固定主机密钥的 SSH config 别名示例 |
| `<container-profile>` | 获授权的容器入口参数；没有容器时不要人为添加 |
| `<SESSION_ID>` / `<REQUEST_ID>` | 当前绑定中的真实 ID；分别表示会话和单次调用 |

[assets/binding.example.json](../assets/binding.example.json) 是交接记录，**不会被现有程序自动读取**。模式 B 真正读取的配置格式见 [运行时 README](../assets/direct-chat/README.md) 和同目录 `config.example.json`；填成本机 `config.json`，不提交私人路径、账号、SSH 配置或认证内容。

## 模式 A：复用现有桥接

文档中的下列历史脚本名是接口参考，不代表发布包附带了它们。先在用户指定项目定位已部署版本，不为“补齐文件名”复制旧项目。

| 用途 | 历史入口 / 状态 |
|---|---|
| 主管连接 | `<BRIDGE_DIR>/connect.py`：check / chat / status |
| 远端主管桥 | `<REMOTE_BRIDGE>/bridge.py`；本地同名副本可能落后 |
| 请求锚点 | connection-pending.local.json、connection-last.local.json |
| 归档与显示 | dialogue_archive.py、foreground_view.py、live_public_watch.py |
| 独立 worker | implementation_bridge.py、model-probes-55/acceptance_runner.py |
| 新公开流式 worker | worker_public_stream_v2.py、worker_public_stream_v2_transport.py |
| 按原 ID 收件 | resume_dispatch.py、receive_fable_60c_workers.py；受原任务清单限制 |
| 额度诊断 | claude_usage_pty_readonly.py；独立 PTY，不 resume 主管 |

实际链路可以是 OpenSSH → 已授权宿主账号 → 已配置容器入口 → 专用 Claude CLI。历史专用主管版本 2.1.280、worker 2.1.296 只是来源记录，不是必须安装版本或“最新版本”。应保存 Claude binary、HOME、workspace、session marker、transcript、持久挂载及部署 SHA 的对应关系。

原主管会话只有一个写入者。有 pending 就先收原结果。下面均为替换占位符后的调用形态：

```powershell
py -X utf8 "<BRIDGE_DIR>/connect.py" check
py -X utf8 "<BRIDGE_DIR>/connect.py" status
py -X utf8 "<BRIDGE_DIR>/connect.py" chat --prompt-file "<BRIDGE_DIR>/request.md"
py -X utf8 "<BRIDGE_DIR>/dialogue_archive.py"
```

chat 会调用模型；status 收原请求；归档命令只读取远端资料但更新本机档案。先读当前入口的实际用法，历史 connect.py 没有 --model、--effort、--timeout、--request-id，不要由模板推导不存在的 CLI 参数。

历史 connect.py 另有 `resolve-terminated`，只允许实现中列明的两条故障请求。新 UUID 不能借旧 ID 清锁；须满足 [确认终止证据](connection-recovery.md)，在已有修复授权内做针对性扩展，保留原 unknown 与独立 resolution。`resolve_77f4_not_registered.py` 属于未登记请求证据处置，与确认终止不同，见 [请求恢复](request-lifecycle.md)。

## 模式 B：部署独立直聊

从 `assets/direct-chat/` 复制或使用独立运行目录，按 README 填真实配置。配置项包括 workspace_root、default_workspace、ssh_argv、remote_prefix、remote_root、remote_home、remote_claude_binary、model、effort、port、timeout_seconds。

- ssh_argv 使用已授权 SSH 入口；remote_prefix 指远端 Python 前的命令前缀，容器入口按实际环境设置，不假设 `wb` 普遍存在。
- 远端 root、会话和本机 state 均与模式 A 分开；不复制主管 session，不抢旧 pending。
- 模型在配置中指定。页面展示请求/响应标签，未独立认证服务商底层模型；没有 UI 型号选择器，也没有沿用历史临时模型时窗。
- 具体部署、启动、MCP 和命令审批见 [direct-chat.md](direct-chat.md)，配置字段的可执行格式以运行时 README 为准。

## 不直接重跑的历史文件

以下历史名字可帮助识别旧件，但不授予重新运行权限：

- implementation_bridge.py submit：曾硬编码首批 worker 和文件清单；新任务按当前主管分工建记录。
- probe-conversation.py、connect.deploy-app.bak.py、bridge.deploy-app.bak.py：可能指向旧容器路由。
- opus_session.py：历史 Pi planning 通道，不是 Claude Code 会话的断线替代。
- activate_fable_supervisor_window.py：一次性模型时窗部署；过期或有备份会拒绝，不删除备份绕过。
- repair_055f.py、resolve_77f4_not_registered.py：绑定单次修复证据，不是通用重部署/清锁命令。
- dispatch_fable_*.py、run_fable_receiver_detached.ps1：绑定当时清单、目录、预算和授权。
- worker_v2_canary_once.py：已有历史 task 时只查原状态，不删除 task 以再次提交。

项目业务目标、资金或账户、材料外发、暂停范围及持续运行授权以本次用户指示为准，不继承来源项目政策。加载或公开发布技能，不代表已获权限接管任何旧会话。
