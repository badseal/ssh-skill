---
name: ssh-skill
description: "CRITICAL: Use this skill for ALL SSH/server operations. NEVER run raw ssh/scp directly. Triggers: SSH, remote server, server IP/hostname/user@host, connect/login, run command on server, check server/status, deploy, upload/download, file transfer, bastion/jump host, server-to-server transfer, migrate, tunnel, port forward, database/internal service access, and Chinese terms: 服务器, 远程, 连接, 登录, 上传, 下载, 部署, 跳板机, 服务器间传输, 迁移, 隧道, 端口转发, 数据库连接, 内网访问. Provides persistent connections, pooling, jump hosts, SFTP, tunneling, and recovery. DO NOT use for local commands, localhost, or current-directory work."
compatibility: "Requires uv and OpenSSH. Resolve bundled scripts relative to this SKILL.md file."
allowed-tools: Bash, Read, Write, Glob
---

# SSH Skill v3.3.1

高性能 SSH 操作技能，支持守护进程长连接、自动连接复用、跳板机、批量并发、服务器间直接传输、自动错误恢复。

## 快捷命令

当用户显式调用本 skill（例如平台支持的 `/ssh-skill <参数>`）时，根据参数执行对应操作：

### `/ssh-skill list`

列出所有已配置的服务器。执行以下步骤：

1. 先将下方 `<skill-root>` 替换为当前 `SKILL.md` 所在目录，再运行命令获取数据：
```bash
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" list-servers
```
2. 解析返回的 JSON 数据
3. 以 **Markdown 表格** 格式展示，列：序号、别名、备注(description)、标签(tags)、位置(location)、认证方式(auth)、用户名(user)
4. 在表格末尾显示服务器总数

表格示例格式：
```
| # | 别名 | 备注 | 标签 | 位置 | 认证 | 用户名 |
|---|------|------|------|------|------|--------|
| 1 | mgmt-01 | 管理服务器 | 管理,Warpgate | 丰台机房 | 密钥 | root |
```

### `/ssh-skill find <关键词>`

查找匹配的服务器，格式同 list。

### `/ssh-skill help`

展示 SSH Skill 的帮助文档。以 Markdown 格式输出以下内容：

**SSH Skill v3.3.1 - 高性能 SSH 操作技能**

**核心特点：**
- 守护进程长连接：首次连接后自动启动守护进程，后续命令响应时间从 ~0.45s 降至 ~0.12s
- 自动连接复用：多个 Agent 会话可共享同一守护进程
- SFTP 高级传输：支持断点续传、进度显示、目录递归上传/下载
- 服务器间直接传输：支持服务器到服务器的文件直接传输，无需本地中转
- SSH 隧道：支持本地端口转发，访问远程内网服务（数据库、Web 服务等）
- 跳板机支持：通过 ProxyJump 自动处理多级跳板机
- 批量并发操作：支持对多台服务器并发执行命令
- 自动错误恢复：SSH 连接断开自动重连（最多 3 次）

**快捷命令：**
- `/ssh-skill list` - 列出所有已配置的服务器
- `/ssh-skill find <关键词>` - 查找匹配的服务器
- `/ssh-skill transfer <源> <源路径> <目标> <目标路径>` - 服务器间文件传输
- `/ssh-skill tunnel <别名> <端口>` - 启动 SSH 隧道
- `/ssh-skill help` - 显示此帮助信息

**常用操作：**

1. 执行远程命令：
   ```
   在 <别名> 上执行 <命令>
   ```

2. 上传文件：
   ```
   上传 <本地路径> 到 <别名> 的 <远程路径>
   ```

3. 下载文件：
   ```
   从 <别名> 下载 <远程路径> 到 <本地路径>
   ```

4. 服务器间传输：
   ```
   从 <源别名> 传输 <路径> 到 <目标别名> 的 <路径>
   将 <别名A> 的文件迁移到 <别名B>
   ```

5. SSH 隧道：
   ```
   建立到 <别名> 的 MySQL 隧道
   连接 <别名> 的数据库
   访问 <别名> 的内部服务
   ```

6. 批量操作：
   ```
   在所有服务器上执行 <命令>
   在生产环境服务器上执行 <命令>
   ```

**配置管理：**
- 配置文件位置：`~/.ssh/config`
- 使用标准 OpenSSH 格式 + 注释元数据
- 支持密钥认证和密码认证
- 支持 ProxyJump 跳板机配置

**性能对比：**
- 直连模式：单次命令 ~0.45s，连续 10 条 ~4.5s
- 守护进程模式：单次命令 ~0.12s，连续 10 条 ~1.2s

更多详细信息请参考 SKILL.md 文档。

### 其他参数

将参数作为用户意图理解，按照下方调用规则执行对应的 SSH 操作。

## CRITICAL: 调用规则

### 路径说明

**Skill 根目录**：`<skill-root>` 表示当前 `SKILL.md` 所在目录。

- Agent 必须从已加载的 `SKILL.md` 路径解析 `<skill-root>`，不要假设当前工作目录。
- 不要扫描或硬编码 `.claude`、`.codex`、`.agents` 等平台目录。
- Claude Code 可将 `<skill-root>` 对应为 `${CLAUDE_SKILL_DIR}`；其他 Agent 使用各自提供的 skill 来源路径。
- 脚本内部使用 `__file__` 定位同目录模块，因此只需正确解析入口脚本路径。

### 调用格式（唯一正确方式）

**MUST**: 使用 `uv run "<skill-root>/scripts/脚本名.py"` 格式，并在执行前替换 `<skill-root>`。使用别名（alias）标识服务器。

**NEVER**: 不要使用 `cd` 到脚本目录再执行，不要使用反斜杠 `\`，不要直接写 `ssh` 或 `scp` 命令。

### 执行远程命令

```bash
uv run "<skill-root>/scripts/ssh_execute.py" <别名> "<命令>"
```

可选参数：`--timeout <秒>` `--no-daemon`

ssh_execute.py 会自动检测守护进程：有则走长连接（~0.12s），无则自动启动守护进程。

### 上传文件

```bash
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_upload.py" <别名> "<本地路径>" "<远程路径>"
```

可选参数：`--resume`（断点续传） `--recursive`（目录递归上传） `--no-progress`（禁用进度输出）

### 下载文件

```bash
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_download.py" <别名> "<远程路径>" "<本地路径>"
```

可选参数：`--resume`（断点续传） `--recursive`（目录递归下载） `--no-progress`（禁用进度输出）

**CRITICAL**: 上传/下载命令**必须**加 `MSYS_NO_PATHCONV=1` 前缀，防止 Windows MSYS bash 将远程路径（如 `/tmp/file`）转换为 Windows 路径。

### 服务器间传输

```bash
# 自动模式（推荐）- 根据文件大小和网络环境自动选择最优方式
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>"

# 强制直连模式（大文件推荐，数据直接在服务器间传输）
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --mode direct

# 强制流式转发（小文件或服务器间网络不通时）
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --mode stream

# 混合模式（先尝试直连，失败后自动降级到流式）
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --mode hybrid

# 使用 rsync（仅直连模式，支持增量同步）
MSYS_NO_PATHCONV=1 uv run "<skill-root>/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --use-rsync
```

可选参数：`--mode <auto|direct|stream|hybrid>`（传输模式） `--use-rsync`（使用 rsync） `--no-progress`（禁用进度） `--size-threshold <MB>`（大小阈值，默认 10） `--timeout <秒>`（超时，默认 300）

**传输模式说明：**

| 模式 | 适用场景 | 数据流向 | 优点 |
|------|----------|----------|------|
| 直连 (direct) | 大文件、服务器间网络通 | 源服务器 → 目标服务器 | 速度快，不占本地带宽 |
| 流式 (stream) | 小文件、网络不通 | 源 → 本地 → 目标（流式） | 无需服务器间配置 |
| 混合 (hybrid) | 不确定环境 | 先尝试直连，失败降级 | 自动适应 |
| 自动 (auto) | 默认 | 智能判断 | 最优选择 |

**CRITICAL**: 服务器间传输命令也**必须**加 `MSYS_NO_PATHCONV=1` 前缀。

### 批量操作

```bash
# 对所有服务器执行
uv run "<skill-root>/scripts/ssh_cluster.py" "<命令>" --parallel

# 对指定别名列表执行
uv run "<skill-root>/scripts/ssh_cluster.py" "<命令>" --hosts "DEV-002,DEV-003" --parallel

# 按环境过滤
uv run "<skill-root>/scripts/ssh_cluster.py" "<命令>" --environment production --parallel

# 按标签过滤
uv run "<skill-root>/scripts/ssh_cluster.py" "<命令>" --tags "web,nginx" --parallel
```

可选参数：`--timeout <秒>` `--health-check` `--max-workers <数量>`

### 配置管理

```bash
# 列出所有服务器
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" list-servers

# 按环境过滤
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" list-servers --environment production

# 查找服务器（支持别名和描述模糊查找）
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" find "<关键词>"

# 创建配置
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" create --alias <别名> --host <IP> --user <用户名> --key <密钥文件> --environment <环境>

# 更新配置（只更新提供的字段，其他字段保持不变）
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" update <别名> --description "新描述" --tags tag1 tag2 tag3
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" update <别名> --environment production --location "新位置"
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" update <别名> --host <新IP> --port <新端口>

# 删除配置
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" delete <别名>
```

### SSH Tunnel（端口转发）

**本地端口转发** - 通过 SSH 隧道访问远程服务

```bash
# 启动 tunnel（自动分配本地端口）
uv run "<skill-root>/scripts/ssh_tunnel.py" start <别名> --remote-port <端口>

# 指定本地端口
uv run "<skill-root>/scripts/ssh_tunnel.py" start <别名> --local-port <本地端口> --remote-port <远程端口>

# 转发到远程的其他主机
uv run "<skill-root>/scripts/ssh_tunnel.py" start <别名> --remote-host <远程主机> --remote-port <端口>

# 列出所有活动的 tunnel
uv run "<skill-root>/scripts/ssh_tunnel.py" list

# 查看 tunnel 状态
uv run "<skill-root>/scripts/ssh_tunnel.py" status <tunnel-id>

# 停止 tunnel
uv run "<skill-root>/scripts/ssh_tunnel.py" stop <tunnel-id>

# 停止服务器的所有 tunnel
uv run "<skill-root>/scripts/ssh_tunnel.py" stop-all <别名>
```

**使用场景**：
- 连接远程数据库（MySQL、PostgreSQL、Redis）
- 访问内部 Web 服务（管理后台、监控面板）
- 调试内部 API 接口
- 访问其他内部服务（Elasticsearch、RabbitMQ）

**特性**：
- 守护进程模式运行
- 自动重连和心跳检测
- 空闲 30 分钟自动退出
- 支持跳板机（ProxyJump）
- 只监听 localhost（安全）

**示例**：
```bash
# 连接远程 MySQL
uv run "<skill-root>/scripts/ssh_tunnel.py" start prod-db-01 --remote-port 3306
# 返回：本地端口 10001

# 使用 tunnel 连接数据库
mysql -h 127.0.0.1 -P 10001 -u root -p

# 访问内部 Web 服务
uv run "<skill-root>/scripts/ssh_tunnel.py" start prod-web-01 --remote-port 8080
# 然后在浏览器访问 http://127.0.0.1:10002
```

## 配置文件

### 存储位置

`~/.ssh/config`（标准 OpenSSH 配置文件）

### 配置格式

每个服务器由 Host 块和注释元数据组成：

```ssh-config
# ===== prod-web-01 =====
# description: 生产环境 Web 服务器
# environment: production
# tags: web,nginx,production
# location: 阿里云-北京
# password:
# created_at: 2026-03-01 12:00:00
# updated_at: 2026-03-01 12:00:00
Host prod-web-01
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/id_rsa
    Port 22
```

### 密码认证配置

密码存储在注释中（SSH config 不原生支持密码字段）：

```ssh-config
# ===== dev-server =====
# description: 开发服务器
# environment: development
# password: your-password
Host dev-server
    HostName 192.168.1.200
    User root
    Port 22
```

注意：密码认证性能较低，建议升级为密钥认证。

### 跳板机配置

使用标准 ProxyJump：

```ssh-config
Host bastion
    HostName bastion.example.com
    User jumpuser
    IdentityFile ~/.ssh/jump_key

Host internal-server
    HostName 10.0.1.100
    User appuser
    IdentityFile ~/.ssh/id_rsa
    ProxyJump bastion
```

AI 只需要知道 `internal-server` 这个别名，底层自动处理跳转。

## 守护进程（长连接模式）

### 工作原理

守护进程在本地维护到远程服务器的 Paramiko 长连接，通过本地 TCP 接受命令请求。
ssh_execute.py 自动检测守护进程：有则复用长连接，无则自动启动。

### 自动模式（推荐）

ssh_execute.py 首次调用时会自动启动守护进程，无需手动操作。
守护进程空闲 30 分钟后自动退出。

### 手动管理

```bash
# 启动守护进程（通常不需要手动启动）
uv run "<skill-root>/scripts/ssh_daemon.py" start <别名>

# 查看守护进程状态
uv run "<skill-root>/scripts/ssh_daemon.py" status <别名>

# 停止守护进程
uv run "<skill-root>/scripts/ssh_daemon.py" stop <别名>
```

可选参数：`--idle-timeout <秒>`（默认 1800，即 30 分钟）

### 守护进程特性

- 每台服务器独立守护进程，按别名隔离
- 多个对话（多个 Agent 会话）可共享同一守护进程
- SSH 连接断开自动重连（最多 3 次）
- 每 60 秒心跳检测连接状态
- 空闲超时自动退出，无需手动清理

### 性能对比

| 模式 | 单次命令 | 连续 10 条 | 连续 30 条 |
|------|----------|-----------|-----------|
| 直连 | ~0.45s | ~4.5s | ~13.5s |
| 守护进程 | ~0.12s | ~1.2s | ~3.6s |

## 性能优化建议

### 命令合并

对同一服务器的多个独立查询，优先合并为一次调用：

```bash
# 好：一次调用获取多个信息
uv run "<skill-root>/scripts/ssh_execute.py" DEV-002 "hostname && uptime && df -h && free -m"

# 差：多次调用分别获取
uv run "<skill-root>/scripts/ssh_execute.py" DEV-002 "hostname"
uv run "<skill-root>/scripts/ssh_execute.py" DEV-002 "uptime"
uv run "<skill-root>/scripts/ssh_execute.py" DEV-002 "df -h"
uv run "<skill-root>/scripts/ssh_execute.py" DEV-002 "free -m"
```

### 何时合并，何时分开

- 合并：多个只读查询、状态检查、信息收集
- 分开：命令之间有依赖关系、需要根据前一个结果决定下一步、需要独立的错误处理

## 输出格式

所有脚本输出 JSON 格式：

```json
{
  "success": true,
  "exit_code": 0,
  "stdout": "命令输出",
  "stderr": ""
}
```

## 故障排查

### 连接超时

检查：网络连接、服务器是否在线、防火墙规则、跳板机是否可达。

长命令可加 `--timeout 300` 延长超时。

### 守护进程问题

如果守护进程异常，可手动停止后重试：

```bash
uv run "<skill-root>/scripts/ssh_daemon.py" stop <别名>
```

或使用 `--no-daemon` 参数跳过守护进程直连：

```bash
uv run "<skill-root>/scripts/ssh_execute.py" <别名> "<命令>" --no-daemon
```

### 别名不存在

如果提示别名不存在，可通过配置管理工具查找：

```bash
uv run "<skill-root>/scripts/ssh_config_manager_v3.py" find "<关键词>"
```

## 强制规则

- 所有 SSH 操作必须通过本 skill 的 Python 脚本
- 禁止直接写 `ssh` 或 `scp` 命令（首次配置公钥除外）
- 路径必须使用正斜杠 `/`，不要使用反斜杠 `\`
- 不要用 `cd` 切换到脚本目录，直接用完整路径调用
- 使用别名（alias）标识服务器，不再使用 JSON 配置文件路径
- 对同一服务器的多个只读查询，优先合并为一次调用

## 依赖

- uv
- Python 3.8+
- paramiko（SSH 连接和文件传输）

入口脚本使用 PEP 723 声明 Python 依赖。始终通过 `uv run` 执行，无需也不要向系统 Python 环境安装 paramiko。
