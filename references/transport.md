# 连接、工具与配置参数

阅读范围：第一次接入、迁移执行环境或需要定位连接障碍时。已经能用的通道优先复用，不为每次发消息重新跑探测。

## 1. 模式 A 链路与执行能力

```text
用户 → 本机 Codex
         ├─ 本机代码、数据、程序、任务成果
         └─ Windows OpenSSH → 已授权服务器账号
                              → 获授权的容器入口
                              → Python 持久请求桥
                              → Claude Code 固定主管会话
```

本页描述模式 A 的 SSH 通道。用户若指定 SSH/Claude Code，不擅自改成 HTTP SDK、Pi 或其他供应商；这不是禁止其他项目使用 API 的全局规则。Tailscale 可作为网络层，但不改变模型接口；来源环境曾未连通，最终继续使用 SSH，这不代表所有环境都应采用公网。模式 B 的双向 MCP 直聊链路见 [direct-chat.md](direct-chat.md)。

分清两种模式：

- **模式 A 的纯桥接执行**：Claude 禁用工具，只读 prompt 中的材料，产出方向、文件内容和命令；Codex 本机执行。不能说远端已经查看 GitHub 或修改仓库。
- **远端读代码模式（需已有相应授权和实际配置）**：远端有单仓库只读权限与工具，能够读已同步版本。每次正式代码任务注明 commit；本机未提交修改不会自动过去。不要为讨论每一句话都 push/pull。

把目标账号没有 `claude` 命令与“服务器没有 Claude”区别开。先确认用户给出的容器入口，例如历史 `docker exec`、当前专用 `wb`；不得借用管理员凭据或改他人服务来解决路径问题。

## 2. 最小连接绑定

模式 A 新环境填写 [binding.example.json](../assets/binding.example.json) 作为交接记录；历史桥参数写在源码中，不自动读取该 JSON。模式 B 已提供独立运行时配置，不受这一历史限制，见 [运行时 README](../assets/direct-chat/README.md)。

| 字段 | 含义与来源 |
|---|---|
| 本机 SSH 路径、config、别名 | 从现有入口或已授权配置取得 |
| 身份文件/代理引用、known_hosts | 使用本机已有凭据；只保存引用，不复制内容 |
| 容器命令 argv | 宿主机到实际运行账号的获授权入口 |
| Claude 绝对路径、版本 | 避免 PATH 指向过旧或另一套客户端 |
| 远端持久根目录、工作目录 | 请求、回执、会话和二进制实际所在地 |
| session_id、marker、transcript | 原固定会话三项必须对应 |
| model、effort、tools | 用户/主管选择，和该渠道实际支持情况 |
| 唯一写入者 | 具体线程或代理，避免两个接续者争用 |
| 本地 pending、last、archive | 中断后从这些文件恢复 |

SSH 配置可采用下列形态；尖括号值需从真实配置替换，不能直接运行：

```sshconfig
Host bridge-host
    HostName <authorized-host>
    User <authorized-user>
    Port <authorized-port>
    IdentityFile <existing-private-key-path>
    IdentitiesOnly yes
    BatchMode yes
    StrictHostKeyChecking yes
    UserKnownHostsFile <verified-known-hosts-path>
    ConnectTimeout 12
    ServerAliveInterval 15
    ServerAliveCountMax 3
    TCPKeepAlive yes
    RequestTTY no
```

以上 12/15/3 是源部署参数，不是普遍 SLA。若现有配置使用 `HostKeyAlias`，保留它和已固定 known_hosts 的对应关系。主机指纹变化时用管理员或已信任记录确认；不要用 `StrictHostKeyChecking=no` 或删除旧记录掩盖变化。私钥、ssh_config 中的私人地址不打包进技能，不提交 Git。

## 3. Claude CLI 参数

下面是源部署主管的基础参数，不是远端当前代码的完整副本。2026-10-13 已有项目内限时模型覆盖和 resolution 补丁，而本地 `bridge.py` 仍是旧版；修改或重新部署前比较实际部署文件及哈希，不能用旧副本覆盖修复。模型时窗见 [model-and-capacity.md](model-and-capacity.md)。未来迁移时核对目标版本，不据此强制升级全局 Claude。

源主管使用独立 `bin/claude-2.1.280`、`HOME=<REMOTE_HOME>`、`DISABLE_AUTOUPDATER=1`；worker 使用独立 2.1.296，还设 `DISABLE_UPDATES=1`。这两个版本不是本机默认客户端，也不是“最新版本”的永久断言。运行时从现有入口取得绑定，不擅自统一版本。

| 参数 | 源部署值 | 用途/注意点 |
|---|---|---|
| `-p` | 启用 | 非交互处理一轮 |
| `--output-format` | `json` | 外层结构化回执；正文另在 `result` |
| `--model` | 基础 `claude-opus-5-5`，可有用户授权时窗覆盖 | 以实际路由和本次回执为准，不能用含糊别名冒充固定型号 |
| `--effort` | `max` | 源主管指定；不是 Codex 的 `ultra` |
| `--resume` | 原 session UUID | 主控接续，不能用 `--continue` 猜最近会话 |
| `--safe-mode` | 启用 | 隔离自定义 hooks/skills 等；关键规则需入 prompt |
| `--tools` | 空字符串 | 禁内置工具；Python argv 中必须保留空参数 |
| `--disallowedTools` | `mcp__*` | 额外禁 MCP；`--tools ""` 本身不能替代它 |
| `--strict-mcp-config` | 启用 | 只使用显式 MCP 配置 |
| `--mcp-config` | `{"mcpServers":{}}` | 空 MCP 集合 |
| `--permission-mode` | `dontAsk` | 不弹交互请求；并不授予原本被禁的能力 |
| `--debug-file` | 单请求受限文件 | 只用于本机/服务器诊断，不进入公开对话和外发包 |

官方 CLI 的 `--resume`、`--session-id`、工具边界及参数解释见 [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)。模型支持的 effort 需按实际环境核实；源渠道曾回显 low/medium/high/xhigh/max，不证明每个模型或供应商都支持全部档位，也不证明真实后端身份。

逻辑命令示意（实际应使用现有 Python argv 实现）：

```text
timeout -k 5s 900s <absolute-claude-binary>
  --safe-mode -p --output-format json
  --model <confirmed-controller-model> --effort <confirmed-effort>
  --tools "" --disallowedTools "mcp__*"
  --strict-mcp-config --mcp-config '{"mcpServers":{}}'
  --permission-mode dontAsk --resume <fixed-session-uuid>
```

prompt 用 UTF-8 stdin 传入，不嵌入 shell 命令。主管命令不可加 `--no-session-persistence`；一次性 worker 可以加，见 [parallel-execution.md](parallel-execution.md)。已有固定会话缺失时不能靠 `--session-id` 自动新建一个冒充恢复。新项目首次创建才在明确初始化时用新 UUID。

## 4. 网络等待与任务时限

| 层级 | 源部署参数 | 实际含义 |
|---|---|---|
| SSH 建连 | 12 秒 | 连接尝试时限 |
| SSH 保活 | 15 秒 × 3 | 检测失联，不延长模型期限 |
| 本地 submit/status 子进程 | 55 秒 | 只等保存/查询回执 |
| 主管基础任务期限 | 默认 900 秒；基础实现只接受 300 或 900 | 从远端接收任务起算，包括启动等待；不是所有 worker 上限 |
| 终止宽限 | `timeout -k 5s` | TERM 后仍不退出则 KILL |
| 历史同步 `call` 等待 | 930 秒 | 内部接口保留，不是推荐聊天方式 |
| 一次性 worker 外围等待 | task timeout +15 秒 | 外围 subprocess 防止失控等待 |

模式 A 默认采用 submit → status，不把一次 SSH 连接挂到模型结束。远端 `Popen(start_new_session=True, close_fds=True)` 使 worker 脱离本次 SSH；请求和回执先持久保存。

worker 时限按其任务记录和主管授权：源项目后来有 1200/1500/1800/2400 秒，不自动套用主管 900 秒；也不因超时自动增加。v2 的首公开文本 300 秒与总预算独立，见 [parallel-execution.md](parallel-execution.md)。`124` 贴近预算和 `-9` 贴近预算+5–7 秒的真实案例支持预算终止；提前 `-9`、空 stdout、网络超时不能据此归因。

状态轮询建议一次 20–60 秒间隔并随任务调整，这是操作建议，不是现有程序自动轮询参数。使用工具等待时保持及时沟通。先执行能并行完成的承接任务，不用密集 status 占满上下文。

## 5. Windows 与跨 shell 技巧

- `py -X utf8` 启动 Python，prompt 文件保存 UTF-8；现有 connect.py 用 `utf-8-sig` 读取，可接受 BOM。
- 传输载荷用 `json.dumps` → stdin，远端 `json.load(sys.stdin)`。不要把中文 prompt、引号、反引号、`$()` 拼进 PowerShell 或 Bash 命令。
- 本机 `subprocess.run` 用 argv 列表；远端只将受控 argv 经 `shlex.join` 拼成命令。JSON 转义不等于 shell 转义。
- Python argv 中 `""` 是真正空字符串；不要让 PowerShell 转发时丢掉 `--tools` 的空值。复用已工作的脚本优于手工重写长 CLI。
- 长回执保存文件后按字段提取。context-mode 能处理项目内 JSON；若它拒绝读取工作区外文件，使用已获读取权限的原生工具做定向提取，不改权限来绕过限制。
- 新版本 CLI 优先放专用目录、固定绝对路径；不覆盖朋友的默认二进制、认证、全局设置或容器挂载。

## 6. 能保证与不能保证的恢复

模式 A 已脱离 SSH 的后台任务可在连接断开后继续；本机可重连取原请求结果。持久目录保证的是文件位置，不自动保证容器重建、进程被杀、整机重启后模型计算能继续。遇这些情况仍按原 request_id 查状态，不宣布“永久不会丢”。源模式 A 做过一次真实断线恢复试验，属于历史证据，不是当前 SLA。模式 B 持续工具回传依赖活跃双向连接，断线按其原请求状态恢复；不能据模式 A 的后台能力承诺模式 B 离线继续执行本机工具。
