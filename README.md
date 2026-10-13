# Claude Bridge & Direct

一个完整的 Codex skill：连接云端 Claude Code，支持主管协作，也支持用户直接与 Claude 聊天并操作 Windows 笔记本。

技能名保持 `claude-codex-bridge`，便于继续使用已有调用方式。

| 模式 | 谁与用户对话 | 本机任务怎样执行 |
|---|---|---|
| 主管与执行者 | Codex 完整传递用户意图与 Claude 原文 | Claude 决定方向，Codex 承接本机执行与回报 |
| Claude 直聊 | 用户在本机网页直接与远端 Claude 对话 | Claude 通过 MCP 请求读写文件、列目录和运行命令；普通程序传输，不调用 GPT |

## 安装和调用

将本仓库安装为一个技能目录，例如 `~/.codex/skills/claude-codex-bridge`。已有同名技能时，先保留旧版，再更新代码与文档；运行中的连接配置和聊天数据应保留在各自项目目录。

首次安装也可在 Codex 中要求：

> 使用 skill-installer，从 https://github.com/yugao-tum/claude-codex-bridge 安装根目录技能。

这是私有仓库，需要当前 GitHub 账号具有访问权限。

示例调用：

> 使用 $claude-codex-bridge，检查当前项目已授权的 Claude 连接，沿用主管会话，接回结果并执行。

> 使用 $claude-codex-bridge，建立我与云端 Claude 的独立直聊入口，让它通过工具在本机写代码和执行任务，不经过 GPT 转述。

> 使用 $claude-codex-bridge，恢复上次断开的请求，先按原 request_id 收取状态，不重新提交。

## 内容

- [SKILL.md](SKILL.md)：使用边界、模式选择、工作闭环与资料入口。
- [references/](references/)：SSH/容器连接、会话与请求恢复、并行收件、模型与额度、存档、实际故障经验。
- [assets/message-templates.md](assets/message-templates.md)：首次交接、增量反馈、任务和恢复消息。
- [assets/binding.example.json](assets/binding.example.json)：协作绑定字段示例。
- [assets/direct-chat/](assets/direct-chat/)：可配置的 Windows 本机服务、网页、Linux Claude runner、MCP、部署与启动程序及测试。

## 直聊程序

配置、部署和启动以 [直聊运行说明](assets/direct-chat/README.md) 为准。需要 Windows、Python、OpenSSH、已获授权的 Linux SSH 通道，以及远端已配置认证的 Claude Code。没有附带模型账号、API key、SSH 私钥或 Grokbot 认证。

文件工具限定在用户选定目录；运行命令需要逐条许可，命令使用当前 Windows 账号权限，工作目录不是系统沙箱。

已发送消息、Claude 可见回复和工具结果自动落盘。刷新页面保留聊天；关闭页面不等于停止执行；断线后按原请求恢复，不自动重发。记录存在不意味着每个模型始终把全部历史装入上下文。

模型由运行配置确定，使用远端实际支持的精确型号和档位。当前没有网页模型下拉框；更改配置与部署后由下一次请求生效，并保留会话记录。旧项目中的限时 Fable/Opus 切换仅作为历史经验，不作为新安装默认。

Grokbot 的专有执行通道没有被当作通用 API 使用。本实现复用 SSH 和 Claude MCP；没有修改 Grokbot，也没有接入鼠标控屏。

## 验证范围

原始部署实际完成了三项测试：云端 Claude 读本机输入、写 Python 程序并在本机运行；同一会话记住前轮结果继续写文件；用户拒绝命令后准确回传且不执行。刷新、服务重启和只读状态恢复也已检验，原始运行版本有 29 项针对性测试通过。

发布包的具体检查结果见 [VALIDATION.md](VALIDATION.md)。可移植配置版本的离线检查与原始部署的真实联调分别记录，不能互相代替。

## 发布内容边界

本仓库包含技能、运行源码、示例配置和测试；不包含个人连接配置、聊天原文、运行状态、部署回执、截图、真实项目文件或认证材料。绑定示例是字段说明，直聊 `config.example.json` 才对应附带程序的配置格式。
