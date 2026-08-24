# SSH Skill v4.0.0

**中文** | [English](README_EN.md)

`ssh-skill` 是面向 Codex 和 Claude Code 的 SSH 工作流 Skill。它以统一的
Python CLI 封装远程命令、文件传输、服务器间传输、批量操作、配置管理、
隧道和连接守护进程，并为 Windows、macOS、Linux 提供一致的结果协议。

**当前稳定主版本：v4.0.0。** 新的 AI 调用应使用统一入口和 v4 结果协议；
旧脚本仅作为迁移兼容入口保留。

## v4 重点

- 所有新调用统一进入 `scripts/ssh_skill.py`。
- stdout 只输出一个 `schema_version=1.0` 的 JSON 文档。
- 远程命令按单独参数传递，不经过本地 PowerShell/Bash/Zsh 二次解析。
- OpenSSH、Paramiko 和集群执行在请求发出后超时时统一返回
  `outcome_unknown`、`retryable=false`，防止 AI 自动重放副作用。
- 统一入口可在 stderr 实时转发有界 JSONL 进度；事件使用 ASCII-safe JSON，
  可被 Windows 控制台和三个桌面平台稳定解析。
- 顶层 JSON 结果限制为 256 KiB；递归传输明细最多保留 100 条首尾样本，
  同时保留真实总数，避免大目录占满 AI 上下文。
- 主机密钥默认采用 `accept-new`，已知密钥冲突会被拒绝。
- 集群默认只预览；执行需要 `--apply`，生产目标还需要
  `--confirm-production`。
- 新配置拒绝明文密码；旧密码仅兼容读取并始终脱敏。
- agent forwarding 默认关闭。
- 保留旧脚本文件名作为迁移入口，但默认采用 v4 安全语义。

## 兼容范围

| AI 工具 | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Codex | 支持 | 支持 | 支持 |
| Claude Code | 支持 | 支持 | 支持 |

发布门槛覆盖 Python 3.10、3.11、3.12、3.13。运行时需要 Python、OpenSSH
客户端和 Paramiko。Python 3.8/3.9 仅尽力兼容，不属于正式测试矩阵。
v4.0.0 发布基线为 115 项离线测试，并由 GitHub Actions 在 Windows、macOS、
Ubuntu 及 Python 3.10-3.13 上验证。

## Skill 路径

以当前 AI 实际加载的 [SKILL.md](SKILL.md) 所在目录为
`<SSH_SKILL_ROOT>`。不要把任意一个目录写死为唯一安装位置。

常见候选位置包括：

- Codex 用户级：`$CODEX_HOME/skills/ssh-skill/` 或 `.agents/skills/ssh-skill/`
- Codex 项目级：`.codex/skills/ssh-skill/` 或 `.agents/skills/ssh-skill/`
- Claude Code 用户级或项目级：`.claude/skills/ssh-skill/`

`doctor` 会列出当前副本和候选副本的版本、内容哈希与漂移状态，但不会自动
复制、覆盖或选择另一个副本。同一个任务中解析一次路径即可，不要每次操作前
重复运行 doctor。

## 平台调用

Windows PowerShell：

```powershell
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" doctor --json
python "<SSH_SKILL_ROOT>\scripts\ssh_skill.py" exec example-host "hostname"
```

macOS / Linux：

```bash
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" doctor --json
python3 "<SSH_SKILL_ROOT>/scripts/ssh_skill.py" exec example-host "hostname"
```

远程路径在三个系统上都保持 POSIX 格式。不要增加本地 Shell 包装层。

## 常用命令

以下示例假设当前目录就是源码根目录：

```text
python scripts/ssh_skill.py doctor --json
python scripts/ssh_skill.py config list-servers
python scripts/ssh_skill.py exec example-host "uname -a"
python scripts/ssh_skill.py upload example-host ./app.tar.gz /tmp/app.tar.gz
python scripts/ssh_skill.py download example-host /var/log/app.log ./app.log
python scripts/ssh_skill.py transfer source-host /data/file destination-host /backup/file
python scripts/ssh_skill.py tunnel start example-host --remote-port 5432
```

完整参数见 [references/commands.md](references/commands.md)。

## 集群确认门

先预览，不建立 SSH 连接：

```text
python scripts/ssh_skill.py cluster "uptime" --environment production
```

确认目标后执行：

```text
python scripts/ssh_skill.py cluster "uptime" --environment production --apply --confirm-production
```

不要从筛选条件推测实际范围；执行前检查结果中的 `targets`、`target_count` 和
`production_targets`。

## 结果协议

成功与失败都返回同一结构：

```json
{
  "schema_version": "1.0",
  "success": true,
  "operation": "exec",
  "data": {},
  "error": null,
  "meta": {
    "request_id": null,
    "platform": "windows",
    "transport": "openssh",
    "elapsed_ms": 120,
    "warnings": []
  }
}
```

当 `error.code=outcome_unknown` 时，命令可能已在远端执行。必须保留 request ID、
停止自动重试，并通过单独的只读检查确认远端状态。

stdout 顶层结果不会超过 256 KiB。递归传输结果只保留最多 100 条首尾样本，
`total_files` 等总数字段仍反映真实规模。显式启用的实时进度以独立、
ASCII-safe 的 JSONL 写入 stderr，不得与 stdout 结果合并。

## 安全边界

- 不直接拼装 `ssh`、`scp`、`sftp` 或 `rsync` 命令。
- 不在日常路径中关闭主机密钥校验或丢弃 `known_hosts`。
- 不把密码、私钥内容、token 或 askpass 数据写入输出。
- 不在未预览的情况下批量连接服务器。
- 不在未确认的情况下执行生产集群操作或配置删除。
- 不因为输出截断而重复执行变更命令。

详细规则见 [references/safety.md](references/safety.md)。

## 旧入口迁移

`ssh_execute.py`、`ssh_upload.py`、`ssh_download.py`、
`ssh_server_transfer.py`、`ssh_config_manager_v3.py`、`ssh_tunnel.py` 和
`ssh_daemon.py` 继续保留。默认输出已经切换到 v4 envelope。

只有确定存在旧调用方时才显式使用 `--legacy-json`。该参数只转换结果字段，
不会恢复不安全的主机密钥、重试、无限输出或集群行为。

## 本地无网络验证

这些帮助命令不连接服务器，并由测试自动执行：

```text
python scripts/ssh_skill.py --help
python scripts/ssh_skill.py exec --help
python scripts/ssh_skill.py upload --help
python scripts/ssh_skill.py download --help
python scripts/ssh_skill.py transfer --help
python scripts/ssh_skill.py cluster --help
python scripts/ssh_skill.py config --help
python scripts/ssh_skill.py tunnel --help
python scripts/ssh_skill.py daemon --help
python scripts/ssh_skill.py doctor --help
```

运行全部测试：

```text
python -m unittest discover -s tests -v
```

v4.0.0 当前发布基线：`115 tests passed`。

自动测试不会连接真实服务器。真实 SSH 冒烟测试、安装同步、推送、标签和发布是
独立批准步骤。

## 文档索引

- AI 核心契约：[SKILL.md](SKILL.md)
- 命令参考：[references/commands.md](references/commands.md)
- Windows：[references/platforms-windows.md](references/platforms-windows.md)
- macOS：[references/platforms-macos.md](references/platforms-macos.md)
- Linux：[references/platforms-linux.md](references/platforms-linux.md)
- 安全规则：[references/safety.md](references/safety.md)

## 许可证

MIT License
