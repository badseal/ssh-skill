"""
Windows SSH Agent 自动配置工具

检测并启动 Windows 的 OpenSSH Authentication Agent 服务
"""

import subprocess
import os
import json
import re


def _run_sc(arguments, runner=subprocess.run):
    return runner(
        ['sc.exe', *arguments],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=10,
        shell=False,
    )


def _service_value(output, field):
    match = re.search(rf'\b{field}\s*:\s*(\d+)', output, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _requires_admin(result):
    message = f'{result.stdout}\n{result.stderr}'.lower()
    return (
        ('access' in message and 'denied' in message)
        or 'permissiondenied' in message
        or '拒绝访问' in message
        or 'error 5' in message
    )


def check_windows_ssh_agent(*, runner=subprocess.run):
    """检查 Windows SSH Agent 服务状态"""
    if os.name != 'nt':
        return {
            'available': False,
            'running': False,
            'message': '非 Windows 系统'
        }

    try:
        result = _run_sc(['query', 'ssh-agent'], runner)

        if result.returncode != 0:
            return {
                'available': False,
                'running': False,
                'message': 'OpenSSH Authentication Agent 服务不存在（需要安装 OpenSSH 客户端）'
            }

        config_result = _run_sc(['qc', 'ssh-agent'], runner)
        status = _service_value(result.stdout, 'STATE')
        start_type = (
            _service_value(config_result.stdout, 'START_TYPE')
            if config_result.returncode == 0 else None
        )

        # Status: 1=Stopped, 4=Running
        # StartType: 2=Automatic, 3=Manual, 4=Disabled
        is_running = (status == 4)
        is_auto = (start_type == 2)

        return {
            'available': True,
            'running': is_running,
            'auto_start': is_auto,
            'status': 'Running' if is_running else 'Stopped',
            'start_type': {2: 'Automatic', 3: 'Manual', 4: 'Disabled'}.get(start_type, 'Unknown')
        }

    except Exception as e:
        return {
            'available': False,
            'running': False,
            'message': f'检查失败: {str(e)}'
        }


def start_windows_ssh_agent(*, runner=subprocess.run):
    """启动 Windows SSH Agent 服务"""
    if os.name != 'nt':
        return {
            'success': False,
            'message': '非 Windows 系统'
        }

    try:
        result = _run_sc(['start', 'ssh-agent'], runner)

        already_running = '1056' in f'{result.stdout}\n{result.stderr}'
        if result.returncode == 0 or already_running:
            return {
                'success': True,
                'message': 'SSH Agent 服务已启动'
            }
        else:
            # 检查是否是权限问题
            if _requires_admin(result):
                return {
                    'success': False,
                    'message': '需要管理员权限启动服务',
                    'admin_required': True
                }
            else:
                return {
                    'success': False,
                    'message': f'启动失败: {result.stderr}'
                }

    except Exception as e:
        return {
            'success': False,
            'message': f'启动失败: {str(e)}'
        }


def enable_windows_ssh_agent_auto_start(*, runner=subprocess.run):
    """设置 Windows SSH Agent 服务为自动启动"""
    if os.name != 'nt':
        return {
            'success': False,
            'message': '非 Windows 系统'
        }

    try:
        result = _run_sc(
            ['config', 'ssh-agent', 'start=', 'auto'], runner
        )

        if result.returncode == 0:
            return {
                'success': True,
                'message': 'SSH Agent 已设置为自动启动'
            }
        else:
            # 检查是否是权限问题（移除换行符后检查）
            if _requires_admin(result):
                return {
                    'success': False,
                    'message': '需要管理员权限修改服务配置',
                    'admin_required': True
                }
            else:
                return {
                    'success': False,
                    'message': f'设置失败: {result.stderr}'
                }

    except Exception as e:
        return {
            'success': False,
            'message': f'设置失败: {str(e)}'
        }


def setup_windows_ssh_agent(auto_start=True, *, runner=subprocess.run):
    """
    一键配置 Windows SSH Agent

    Args:
        auto_start: 是否设置为自动启动

    Returns:
        配置结果字典
    """
    result = {
        'success': False,
        'steps': []
    }

    # 1. 检查服务状态
    status = check_windows_ssh_agent(runner=runner)
    result['steps'].append({
        'step': 'check_service',
        'result': status
    })

    if not status.get('available'):
        result['message'] = status.get('message', 'SSH Agent 服务不可用')
        return result

    # 2. 如果启动类型是 Disabled，先设置为 Automatic
    if status.get('start_type') == 'Disabled':
        auto_result = enable_windows_ssh_agent_auto_start(runner=runner)
        result['steps'].append({
            'step': 'enable_auto_start',
            'result': auto_result
        })

        if not auto_result.get('success'):
            result['message'] = auto_result.get('message')
            result['admin_required'] = auto_result.get('admin_required', False)
            return result

    # 3. 如果未运行，启动服务
    if not status.get('running'):
        start_result = start_windows_ssh_agent(runner=runner)
        result['steps'].append({
            'step': 'start_service',
            'result': start_result
        })

        if not start_result.get('success'):
            result['message'] = start_result.get('message')
            result['admin_required'] = start_result.get('admin_required', False)
            return result

    # 4. 如果需要且还未设置，确保自动启动
    if auto_start and not status.get('auto_start') and status.get('start_type') != 'Disabled':
        auto_result = enable_windows_ssh_agent_auto_start(runner=runner)
        result['steps'].append({
            'step': 'ensure_auto_start',
            'result': auto_result
        })

        if not auto_result.get('success'):
            # 自动启动失败不影响整体成功
            result['warning'] = auto_result.get('message')

    result['success'] = True
    result['message'] = 'Windows SSH Agent 配置成功'
    return result


def get_setup_instructions():
    """获取手动配置说明（需要管理员权限时）"""
    return """
Windows SSH Agent 手动配置步骤：

方法 1：使用管理员权限的 Windows 终端
1. 右键点击"开始"菜单，选择"终端管理员"
2. 运行以下命令：
   sc.exe config ssh-agent start= auto
   sc.exe start ssh-agent

方法 2：使用服务管理器
1. 按 Win+R，输入 services.msc
2. 找到"OpenSSH Authentication Agent"
3. 右键 → 属性 → 启动类型改为"自动"
4. 点击"启动"按钮

配置完成后，添加你的密钥：
ssh-add C:\\Users\\YourName\\.ssh\\id_ed25519
"""


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description='Windows SSH Agent 配置工具')
    parser.add_argument('action', choices=['check', 'start', 'setup', 'instructions'],
                        help='操作：check=检查状态, start=启动服务, setup=一键配置, instructions=显示手动配置说明')
    parser.add_argument('--no-auto-start', action='store_true',
                        help='不设置自动启动')

    args = parser.parse_args()

    if args.action == 'check':
        result = check_windows_ssh_agent()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == 'start':
        result = start_windows_ssh_agent()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == 'setup':
        result = setup_windows_ssh_agent(auto_start=not args.no_auto_start)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not result['success'] and result.get('admin_required'):
            print("\n" + get_setup_instructions())

    elif args.action == 'instructions':
        print(get_setup_instructions())


if __name__ == '__main__':
    main()
