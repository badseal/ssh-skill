# SSH Skill - 高性能 SSH 操作技能

**中文** | [English](README_EN.md)

> 为 Claude Code 打造的企业级 SSH 管理工具，让远程服务器操作像本地一样简单高效

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 📢 最近更新

### v3.3 - Windows 原生 SSH 适配 & Passphrase 密钥支持（2026-03-24）

- 🔑 **Passphrase 密钥完整支持**：通过 Windows SSH Agent 集成，passphrase 保护的密钥可以无感使用，无需每次交互输入密码
- 🪟 **Windows 原生 SSH 适配**：自动定位 `%SystemRoot%\System32\OpenSSH\ssh.exe`，解决 Git SSH 与 Windows 原生 SSH 的 PATH 优先级冲突
- 🔌 **SSH 隧道管理**：新增本地端口转发功能，支持守护进程模式、自动重连、心跳检测，可便捷访问远程数据库和内网服务
- 🛡️ **Windows SSH Agent 工具**：一键检测、启动和配置 Windows OpenSSH Authentication Agent 服务

## ✨ 核心特性

### 🚀 极致性能

**守护进程长连接模式** - 业界领先的性能优化

| 模式 | 单次命令 | 连续 10 条 | 连续 30 条 | 性能提升 |
|------|----------|-----------|-----------|---------|
| 传统直连 | ~0.45s | ~4.5s | ~13.5s | - |
| **守护进程** | **~0.12s** | **~1.2s** | **~3.6s** | **🔥 3.75x** |

- 首次连接自动启动守护进程
- 多个 Claude Code 实例共享连接
- 自动心跳检测和断线重连
- 空闲 30 分钟自动退出

### 📊 智能大文件传输

**自动切换传输模式** - 根据文件大小智能选择最优方案

```
文件大小 ≤ 80MB  →  原生 SCP（快速完成）
文件大小 > 80MB  →  Paramiko SFTP（实时进度）
```

**实时进度显示**：
```json
{
  "file": "large-file.iso",
  "total": 310984990,
  "transferred": 155492495,
  "percent": 50.0,
  "speed": "2.1 MB/s",
  "eta": 74.2
}
```

**智能超时计算**：
- 根据文件大小自动计算超时时间
- 公式：`文件大小(MB) ÷ 1MB/s + 60秒缓冲`
- 范围：60 秒 - 3600 秒（1小时）

**传输优化**：
- 块大小：128KB（4倍性能提升）
- 支持断点续传
- 无超时限制（大文件）
- 目录递归上传/下载

### 🌐 服务器间直接传输

**零本地带宽消耗** - 数据直接在服务器间传输

```bash
# 自动模式（推荐）- 智能选择最优方式
ssh_server_transfer.py source-server /data/backup.tar.gz target-server /backup/

# 直连模式 - 大文件推荐（数据不经过本地）
ssh_server_transfer.py source-server /data/large.iso target-server /data/ --mode direct

# 支持 rsync 增量同步
ssh_server_transfer.py source-server /data/ target-server /backup/ --use-rsync
```

**传输模式对比**：

| 模式 | 数据流向 | 适用场景 | 优势 |
|------|---------|---------|------|
| 直连 (direct) | 源 → 目标 | 大文件、服务器间网络通 | 速度快，不占本地带宽 |
| 流式 (stream) | 源 → 本地 → 目标 | 小文件、网络不通 | 无需服务器间配置 |
| 混合 (hybrid) | 先尝试直连，失败降级 | 不确定环境 | 自动适应 |
| 自动 (auto) | 智能判断 | 默认 | 最优选择 |

### 🎯 跳板机支持

**多级跳板机自动处理** - 使用标准 ProxyJump

```ssh-config
Host internal-server
    HostName 10.0.1.100
    User appuser
    ProxyJump bastion1,bastion2
```

AI 只需要知道 `internal-server` 别名，底层自动处理多级跳转。

### 🔧 统一配置管理

**基于标准 OpenSSH 配置** - 兼容所有 SSH 工具

```bash
# 列出所有服务器
ssh_config_manager_v3.py list-servers

# 查找服务器
ssh_config_manager_v3.py find "web"

# 创建配置
ssh_config_manager_v3.py create --alias prod-web-01 --host 192.168.1.100 --user root

# 更新配置
ssh_config_manager_v3.py update prod-web-01 --description "生产环境 Web 服务器"
```

**元数据支持**：
- 环境标签（production/development/staging）
- 位置信息
- 自定义标签
- 创建/更新时间

### ⚡ 批量并发操作

**对多台服务器并发执行命令**

```bash
# 对所有服务器执行
ssh_cluster.py "uptime" --parallel

# 按环境过滤
ssh_cluster.py "systemctl status nginx" --environment production --parallel

# 按标签过滤
ssh_cluster.py "df -h" --tags "web,nginx" --parallel --max-workers 10
```

## 📦 安装

### 依赖

```bash
pip install paramiko
```

### 配置

1. 将 `ssh-skill` 目录放到 `~/.claude/skills/` 下
2. 配置 SSH 密钥或密码认证
3. 开始使用！

## 🎬 快速开始

### 执行远程命令

```bash
python ~/.claude/skills/ssh-skill/scripts/ssh_execute.py prod-web-01 "systemctl status nginx"
```

### 上传文件

```bash
# 小文件（快速）
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./app.tar.gz /tmp/

# 大文件（自动显示进度）
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./large-file.iso /tmp/

# 断点续传
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./large-file.iso /tmp/ --resume

# 递归上传目录
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./dist/ /var/www/html/ --recursive
```

### 下载文件

```bash
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_download.py prod-web-01 /var/log/app.log ./app.log
```

### 服务器间传输

```bash
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_server_transfer.py source-server /data/backup.tar.gz target-server /backup/
```

## 🎯 使用场景

### 场景 1：日常运维

```bash
# 快速检查服务器状态
ssh_execute.py web-01 "uptime && free -m && df -h"

# 批量重启服务
ssh_cluster.py "systemctl restart nginx" --environment production --parallel
```

### 场景 2：大文件部署

```bash
# 上传 500MB 应用包（自动显示进度）
ssh_upload.py prod-web-01 ./app-v2.0.tar.gz /opt/apps/

# 输出示例：
# 上传进度: 45.2% (2.1 MB/s) ETA: 102.3s
```

### 场景 3：数据迁移

```bash
# 服务器间直接传输（不占用本地带宽）
ssh_server_transfer.py old-server /data/database.sql new-server /data/ --mode direct

# 使用 rsync 增量同步
ssh_server_transfer.py source /data/ target /backup/ --use-rsync
```

### 场景 4：跳板机访问

```bash
# 通过跳板机访问内网服务器（自动处理）
ssh_execute.py internal-server "docker ps"
```

## 📈 性能数据

### 真实测试数据

**测试环境**：
- 文件大小：297MB
- 网络速度：1.6-2.1 MB/s
- 服务器：test-001

**测试结果**：

| 指标 | 原生 SCP | Paramiko SFTP | 优势 |
|------|---------|--------------|------|
| 进度显示 | ❌ 无 | ✅ 实时 | 用户体验 |
| 超时问题 | ❌ 30秒固定 | ✅ 无限制 | 稳定性 |
| 断点续传 | ❌ 不支持 | ✅ 支持 | 可靠性 |
| 传输速度 | 快 | 稍慢 | 性能 |

**智能选择策略**：
- 文件 ≤ 80MB：使用原生 SCP（快速完成）
- 文件 > 80MB：使用 Paramiko SFTP（实时进度）

### 守护进程性能

**命令执行速度对比**：

```
传统模式：
  命令 1: 0.45s
  命令 2: 0.45s
  命令 3: 0.45s
  总计: 1.35s

守护进程模式：
  命令 1: 0.45s (首次启动守护进程)
  命令 2: 0.12s (复用连接)
  命令 3: 0.12s (复用连接)
  总计: 0.69s (提升 1.96x)
```

## 🔐 安全特性

- 支持密钥认证和密码认证
- 密码加密存储在 SSH 配置注释中
- 支持密钥密码保护
- 自动添加主机密钥（可配置）
- 支持 SSH agent forwarding

## 🛠️ 高级功能

### 断点续传

```bash
# 上传大文件，支持中断后继续
ssh_upload.py prod-web-01 ./large-file.iso /tmp/ --resume
```

### 目录递归传输

```bash
# 递归上传整个目录
ssh_upload.py prod-web-01 ./dist/ /var/www/html/ --recursive
```

### 自动错误恢复

- SSH 连接断开自动重连（最多 3 次）
- 每 60 秒心跳检测连接状态
- 传输失败自动重试

### 配置管理

```bash
# 按环境过滤
ssh_config_manager_v3.py list-servers --environment production

# 按标签过滤
ssh_config_manager_v3.py list-servers --tags web,nginx

# 更新服务器信息
ssh_config_manager_v3.py update prod-web-01 --description "新描述" --tags tag1,tag2
```

## 📚 配置示例

### 密钥认证

```ssh-config
# ===== prod-web-01 =====
# description: 生产环境 Web 服务器
# environment: production
# tags: web,nginx,production
# location: 阿里云-北京
Host prod-web-01
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/id_rsa
    Port 22
```

### 密码认证

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

### 跳板机配置

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

## 🎨 与 Claude Code 集成

在 Claude Code 中，AI 会自动使用 ssh-skill 处理所有 SSH 操作：

```
用户：在 prod-web-01 上检查 Nginx 状态
AI：[自动调用 ssh_execute.py]

用户：上传 app.tar.gz 到 prod-web-01 的 /tmp 目录
AI：[自动调用 ssh_upload.py]

用户：从 old-server 迁移数据到 new-server
AI：[自动调用 ssh_server_transfer.py]
```

## 🔄 版本历史

### v3.2 (2026-03-04)
- ✨ **大文件传输优化**：智能切换传输模式（80MB 阈值）
- ✨ **实时进度显示**：百分比、速度、ETA
- ✨ **智能超时计算**：根据文件大小自动计算
- ✨ **块大小优化**：32KB → 128KB（4倍提升）
- ✨ **无超时限制**：大文件传输不再超时

### v3.1
- 守护进程长连接模式
- 服务器间直接传输
- 批量并发操作
- 统一配置管理

### v3.0
- 基于 OpenSSH 配置
- 跳板机支持
- 元数据管理

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 👨‍💻 作者

Michael Zhang - [@badseal](https://github.com/badseal)

---

**让远程服务器操作像本地一样简单高效！** 🚀
