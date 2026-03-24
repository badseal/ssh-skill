# SSH Skill - High-Performance SSH Operations Tool

[中文](README.md) | **English**

> Enterprise-grade SSH management tool for Claude Code, making remote server operations as simple and efficient as local ones

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 📢 Recent Updates

### v3.3 - Windows Native SSH Adaptation & Passphrase Key Support (2026-03-24)

- 🔑 **Full Passphrase Key Support**: Seamless passphrase-protected key usage through Windows SSH Agent integration — no interactive password input needed
- 🪟 **Windows Native SSH Adaptation**: Auto-locates `%SystemRoot%\System32\OpenSSH\ssh.exe`, resolving PATH priority conflicts between Git SSH and Windows native SSH
- 🔌 **SSH Tunnel Management**: Local port forwarding with daemon mode, auto-reconnect, heartbeat detection — easy access to remote databases and internal services
- 🛡️ **Windows SSH Agent Tool**: One-click detection, startup, and configuration of the Windows OpenSSH Authentication Agent service

## ✨ Core Features

### 🚀 Ultimate Performance

**Daemon Long-Connection Mode** - Industry-leading performance optimization

| Mode | Single Command | 10 Commands | 30 Commands | Performance Gain |
|------|---------------|-------------|-------------|-----------------|
| Traditional Direct | ~0.45s | ~4.5s | ~13.5s | - |
| **Daemon Mode** | **~0.12s** | **~1.2s** | **~3.6s** | **🔥 3.75x** |

- Auto-start daemon on first connection
- Multiple Claude Code instances share connections
- Automatic heartbeat detection and reconnection
- Auto-exit after 30 minutes idle

### 📊 Smart Large File Transfer

**Automatic Transfer Mode Switching** - Intelligently selects optimal solution based on file size

```
File size ≤ 80MB  →  Native SCP (fast completion)
File size > 80MB  →  Paramiko SFTP (real-time progress)
```

**Real-time Progress Display**:
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

**Smart Timeout Calculation**:
- Auto-calculate timeout based on file size
- Formula: `File size(MB) ÷ 1MB/s + 60s buffer`
- Range: 60 seconds - 3600 seconds (1 hour)

**Transfer Optimization**:
- Block size: 128KB (4x performance boost)
- Resume support
- No timeout limit (large files)
- Recursive directory upload/download

### 🌐 Server-to-Server Direct Transfer

**Zero Local Bandwidth Consumption** - Data transfers directly between servers

```bash
# Auto mode (recommended) - intelligently selects optimal method
ssh_server_transfer.py source-server /data/backup.tar.gz target-server /backup/

# Direct mode - recommended for large files (data doesn't go through local)
ssh_server_transfer.py source-server /data/large.iso target-server /data/ --mode direct

# Support rsync incremental sync
ssh_server_transfer.py source-server /data/ target-server /backup/ --use-rsync
```

**Transfer Mode Comparison**:

| Mode | Data Flow | Use Case | Advantages |
|------|-----------|----------|-----------|
| Direct | Source → Target | Large files, servers connected | Fast, no local bandwidth |
| Stream | Source → Local → Target | Small files, network issues | No server-to-server config needed |
| Hybrid | Try direct first, fallback to stream | Uncertain environment | Auto-adaptive |
| Auto | Smart decision | Default | Optimal choice |

### 🎯 Jump Host Support

**Multi-level Jump Host Auto-handling** - Using standard ProxyJump

```ssh-config
Host internal-server
    HostName 10.0.1.100
    User appuser
    ProxyJump bastion1,bastion2
```

AI only needs to know the `internal-server` alias, multi-level jumping is handled automatically.

### 🔧 Unified Configuration Management

**Based on Standard OpenSSH Config** - Compatible with all SSH tools

```bash
# List all servers
ssh_config_manager_v3.py list-servers

# Find servers
ssh_config_manager_v3.py find "web"

# Create configuration
ssh_config_manager_v3.py create --alias prod-web-01 --host 192.168.1.100 --user root

# Update configuration
ssh_config_manager_v3.py update prod-web-01 --description "Production Web Server"
```

**Metadata Support**:
- Environment tags (production/development/staging)
- Location information
- Custom tags
- Creation/update timestamps

### ⚡ Batch Concurrent Operations

**Execute commands on multiple servers concurrently**

```bash
# Execute on all servers
ssh_cluster.py "uptime" --parallel

# Filter by environment
ssh_cluster.py "systemctl status nginx" --environment production --parallel

# Filter by tags
ssh_cluster.py "df -h" --tags "web,nginx" --parallel --max-workers 10
```

## 📦 Installation

### Dependencies

```bash
pip install paramiko
```

### Configuration

1. Place the `ssh-skill` directory under `~/.claude/skills/`
2. Configure SSH key or password authentication
3. Start using!

## 🎬 Quick Start

### Execute Remote Commands

```bash
python ~/.claude/skills/ssh-skill/scripts/ssh_execute.py prod-web-01 "systemctl status nginx"
```

### Upload Files

```bash
# Small files (fast)
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./app.tar.gz /tmp/

# Large files (auto progress display)
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./large-file.iso /tmp/

# Resume support
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./large-file.iso /tmp/ --resume

# Recursive directory upload
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py prod-web-01 ./dist/ /var/www/html/ --recursive
```

### Download Files

```bash
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_download.py prod-web-01 /var/log/app.log ./app.log
```

### Server-to-Server Transfer

```bash
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_server_transfer.py source-server /data/backup.tar.gz target-server /backup/
```

## 🎯 Use Cases

### Scenario 1: Daily Operations

```bash
# Quick server status check
ssh_execute.py web-01 "uptime && free -m && df -h"

# Batch service restart
ssh_cluster.py "systemctl restart nginx" --environment production --parallel
```

### Scenario 2: Large File Deployment

```bash
# Upload 500MB application package (auto progress display)
ssh_upload.py prod-web-01 ./app-v2.0.tar.gz /opt/apps/

# Output example:
# Upload progress: 45.2% (2.1 MB/s) ETA: 102.3s
```

### Scenario 3: Data Migration

```bash
# Server-to-server direct transfer (no local bandwidth)
ssh_server_transfer.py old-server /data/database.sql new-server /data/ --mode direct

# Use rsync for incremental sync
ssh_server_transfer.py source /data/ target /backup/ --use-rsync
```

### Scenario 4: Jump Host Access

```bash
# Access internal server through jump host (auto-handled)
ssh_execute.py internal-server "docker ps"
```

## 📈 Performance Data

### Real Test Data

**Test Environment**:
- File size: 297MB
- Network speed: 1.6-2.1 MB/s
- Server: test-001

**Test Results**:

| Metric | Native SCP | Paramiko SFTP | Advantage |
|--------|-----------|---------------|-----------|
| Progress Display | ❌ None | ✅ Real-time | User Experience |
| Timeout Issue | ❌ 30s fixed | ✅ Unlimited | Stability |
| Resume Support | ❌ Not supported | ✅ Supported | Reliability |
| Transfer Speed | Fast | Slightly slower | Performance |

**Smart Selection Strategy**:
- File ≤ 80MB: Use native SCP (fast completion)
- File > 80MB: Use Paramiko SFTP (real-time progress)

### Daemon Performance

**Command Execution Speed Comparison**:

```
Traditional Mode:
  Command 1: 0.45s
  Command 2: 0.45s
  Command 3: 0.45s
  Total: 1.35s

Daemon Mode:
  Command 1: 0.45s (first start daemon)
  Command 2: 0.12s (reuse connection)
  Command 3: 0.12s (reuse connection)
  Total: 0.69s (1.96x improvement)
```

## 🔐 Security Features

- Support key and password authentication
- Password encrypted storage in SSH config comments
- Support key password protection
- Auto-add host keys (configurable)
- Support SSH agent forwarding

## 🛠️ Advanced Features

### Resume Support

```bash
# Upload large file, support resume after interruption
ssh_upload.py prod-web-01 ./large-file.iso /tmp/ --resume
```

### Recursive Directory Transfer

```bash
# Recursively upload entire directory
ssh_upload.py prod-web-01 ./dist/ /var/www/html/ --recursive
```

### Auto Error Recovery

- SSH connection auto-reconnect on disconnect (max 3 times)
- Heartbeat detection every 60 seconds
- Auto-retry on transfer failure

### Configuration Management

```bash
# Filter by environment
ssh_config_manager_v3.py list-servers --environment production

# Filter by tags
ssh_config_manager_v3.py list-servers --tags web,nginx

# Update server info
ssh_config_manager_v3.py update prod-web-01 --description "New description" --tags tag1,tag2
```

## 📚 Configuration Examples

### Key Authentication

```ssh-config
# ===== prod-web-01 =====
# description: Production Web Server
# environment: production
# tags: web,nginx,production
# location: Alibaba Cloud - Beijing
Host prod-web-01
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/id_rsa
    Port 22
```

### Password Authentication

```ssh-config
# ===== dev-server =====
# description: Development Server
# environment: development
# password: your-password
Host dev-server
    HostName 192.168.1.200
    User root
    Port 22
```

### Jump Host Configuration

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

## 🎨 Integration with Claude Code

In Claude Code, AI automatically uses ssh-skill for all SSH operations:

```
User: Check Nginx status on prod-web-01
AI: [Auto-calls ssh_execute.py]

User: Upload app.tar.gz to /tmp on prod-web-01
AI: [Auto-calls ssh_upload.py]

User: Migrate data from old-server to new-server
AI: [Auto-calls ssh_server_transfer.py]
```

## 🔄 Version History

### v3.2 (2026-03-04)
- ✨ **Large file transfer optimization**: Smart mode switching (80MB threshold)
- ✨ **Real-time progress display**: Percentage, speed, ETA
- ✨ **Smart timeout calculation**: Auto-calculate based on file size
- ✨ **Block size optimization**: 32KB → 128KB (4x improvement)
- ✨ **No timeout limit**: Large file transfers no longer timeout

### v3.1
- Daemon long-connection mode
- Server-to-server direct transfer
- Batch concurrent operations
- Unified configuration management

### v3.0
- Based on OpenSSH config
- Jump host support
- Metadata management

## 🤝 Contributing

Issues and Pull Requests are welcome!

## 📄 License

MIT License

## 👨‍💻 Author

Michael Zhang - [@badseal](https://github.com/badseal)

---

**Making remote server operations as simple and efficient as local ones!** 🚀
