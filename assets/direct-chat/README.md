# Claude 直聊运行程序

Windows 本机网页直接把用户消息发给 Linux 上的 Claude Code；Claude 的四个 MCP 工具通过同一条 SSH 连接回到笔记本。没有 GPT 转述步骤，也不依赖其他聊天产品的专有接口。

这是可配置的发布副本。首次部署应选择新的专用目录和会话，不能指向正在使用的主管运行目录。包内不含登录信息、SSH 私钥、历史聊天或远端状态。

## 环境与配置

- 本机：Windows、Python 3.10+（可用 `py.exe`）、Windows PowerShell、OpenSSH。无第三方 Python 包。
- 远端：Linux、Python 3.10+、已安装并已在指定 HOME 登录的 Claude Code。SSH 必须能无交互连接，并保持标准输入；容器入口也必须保留 stdin，例如 `docker exec -i`。登录和主机密钥确认由用户在部署前完成，程序不关闭主机密钥检查。
- 原运行版本为 Claude Code **2.1.296**。本程序需要该版本支持的 `--system-prompt-snapshot off`、`--include-partial-messages`、`--strict-mcp-config` 等参数。换用其他版本，先在目标环境检查它的 `--help`；发布副本未对所有版本做兼容验证。程序不下载 CLI，也不自动降级模型或删除不支持的参数。
- 使用默认的 `$HOME/.claude` 登录与会话目录；自定义 `CLAUDE_CONFIG_DIR` 尚不支持。`remote_home` 必须与运行 Claude 的 Linux 用户及现有登录对应。只复用该用户的登录，独立会话和工作目录不复用主管的 session ID。

在本目录运行：

```powershell
Copy-Item -LiteralPath .\config.example.json -Destination .\config.json
notepad.exe .\config.json
py -X utf8 .\config.py
```

`config.json` 已被 `.gitignore` 排除。示例中的主机别名、目录与模型占位符必须按自己的环境填写；配置校验不代表连接或模型已经可用。

| 字段 | 含义与实际用途 |
|---|---|
| `workspace_root` | Windows 文件工具允许的最大目录范围，填写绝对路径。不能据此认为终端命令被操作系统隔离。 |
| `default_workspace` | 该范围内的默认工作目录，启动时创建；不要放进本运行程序目录。页面在空闲时可切换到范围内的现有子目录。 |
| `ssh_argv` | 本机 SSH 参数数组，例如 `ssh.exe -T -o BatchMode=yes ... claude-host` 分成各个元素。可指定自己的 `-F` 配置文件；连接配置与密钥应放在工作范围外。不要使用断开 stdin 的 `-n`/`-f`。 |
| `remote_prefix` | SSH 后的远端命令前缀，**最后包含 Python 解释器**。主机直接运行用 `["python3"]`；容器示例为 `["docker", "exec", "-i", "my-container", "python3"]`。需要指定用户时在容器前缀中加入实际 `--user` 参数。不要分配 TTY，也不要使用输出欢迎文字的包装器。 |
| `remote_root` | Linux 内的专用安装/运行目录；容器模式填写容器内路径。部署三个 Python 文件和 `runtime-config.json`，并在其下保存 requests/sessions/workspace/sockets。路径过长会被校验拒绝，以免超出 UNIX socket 上限。 |
| `remote_home` | Claude 子进程的 `HOME`；恢复证据从其 `.claude/projects/<工作目录编码>/<session_id>.jsonl` 查找。 |
| `remote_claude_binary` | Linux/容器内 Claude Code 可执行文件的绝对路径，建议指向已验证版本。 |
| `model` | 自己账号/通道当前可用的**确切型号 ID**。本程序不替用户猜可用型号，没有固定 Fable 时窗、默认型号切换或模型下拉框。 |
| `effort` | 传入 CLI 的 `--effort`，允许 `low`/`medium`/`high`/`max`；需目标型号支持。 |
| `port` | 本机网页端口，范围 1024–65535；只监听 `127.0.0.1`。 |
| `timeout_seconds` | 一次请求的总预算，1–900 秒，包括模型生成、工具等待和用户命令许可等待。远端也按部署值限制上限。 |

## 部署与启动

```powershell
py -X utf8 .\deploy.py
powershell.exe -NoProfile -File .\start.ps1
```

也可以双击 `Start Claude Direct.cmd`。不自动打开浏览器时，在启动前设置 `$env:CLAUDE_DIRECT_NO_BROWSER = '1'`，按输出地址自行打开。希望在前台查看启动错误时使用 `py -X utf8 .\app.py`，关闭该终端进程即可结束本机服务；先在网页停止或收取当前请求。

部署只发送三个 Python 源文件及六个远端配置字段，不发送 SSH 配置、登录文件、本地项目文件或聊天历史。它核对四个文件的 SHA-256；已存在且变化的文件先保存到远端 `deployment-backups/`。部署不调用模型。不要在当前请求运行时更新远端程序。

修改 `model` / `effort` / `remote_home` / 二进制等远端设置后，重新运行 `deploy.py`；**部署后的下一次请求**读取新值，正在运行的请求不切换型号。其他本机配置在服务启动时读取，修改后需要重启。改变远端根目录、HOME 或本机状态目录会影响旧会话恢复，应视为迁移操作，不能把连接失败当作新会话自动重发。

## 操作与记录

- 四个工具为 `read_file`、`write_file`、`list_files`、`run_command`，MCP server 名为 `laptop`。内建工具禁用，仅放行 `mcp__laptop__*`；不启用 `--safe-mode`，因为它会阻止所需 MCP 通道。`--permission-mode dontAsk` 仅用于这一组远端工具，本机对每条终端命令仍显示原文、工作目录和超时，需用户单次许可。
- 文件工具限定相对路径，阻止越界、符号链接/重解析点、硬链接、敏感文件名与本程序目录；每文件最多 200 KiB，覆盖前保留原字节。终端在 Windows 用户权限下执行，cwd **不是沙箱**；批准命令可能访问工作目录外资源，不应把文件工具的限制误认为整个 PowerShell 的权限边界。
- 终端工具每条超时最多 300 秒，输出最多每路 32 KiB；许可等待最多 600 秒，仍受请求总预算限制。取消、超时或 SSH 断开会结束本次相关进程；不会终止其他主管或其他会话。
- 本机 `state/state.json` 保存聊天视图与 session ID；`state/events.jsonl` 保存用户原文、公开回答及工具公开结果，`state/*.request.json` 保存原始请求。备份在 `state/backups/`。远端 `requests/<request_id>/` 保存原始请求、公开事件和最终回执，原生 Claude 会话仍在所配置 HOME 下。桥接公开事件过滤 thinking；原生 CLI 的私有会话文件也应按敏感运行资料保存，不随发布包提交。
- 首轮尝试不等于已生成可恢复会话。程序以实际 transcript 决定 `resume_available`，保留首轮失败后可再次发送的修复；同时保留 `modelUsage=null`、完成后 EOF、旧连接取消新请求等修复。
- 断线后使用页面“恢复状态”，只查询原 request ID，绝不重新调用模型。相同 ID 相同参数只收取已记录状态，变参复用被拒绝。无最终回执的状态可能继续显示未知；不能把它当失败而静默重发，也不应手工删除锁或历史来绕过。
- 远端未登记请求就断线时，`not_found` 仍会保持 unknown，当前没有一键释放；需维护者按 [维护恢复步骤](../../references/direct-chat.md) 核对原绑定及未登记证据。原生历史丢失或 HOME 改错也会拒绝续聊，不会静默创建空历史。
- 回执的型号检查依据 CLI 报告的型号标签；它不构成对供应商实际底层模型的独立认证。

## 离线检查

```powershell
py -X utf8 -m unittest discover -p "test_*.py" -v
```

测试使用临时目录，SSH/Claude 调用被替换；其中 Windows 终端测试只执行输出、超时与取消用的无业务命令。它验证配置绑定、原文/恢复协议、工具边界、逐条许可和取消逻辑，不能替代新环境的登录、真实 Linux 进程组及模型/MCP 对话验证。新安装验收可先进行一次短对话，再续聊确认同一会话，并在专用试验目录检查文件写入和一条已许可的简单命令。
