"""
角色权限模块 —— 主人（master）与奴隶（slave）的命令分权。

用法：
  from role import resolve_role, check_permission

  role = resolve_role(cli_role_arg)
  error = check_permission(role, command_name)
  if error:
      print_json({"error": "权限不足", "detail": error, ...})
      return
"""

import os

# ── 命令角色分类 ──────────────────────────────────────────

# 只有主人（Claude）才能执行的命令
MASTER_ONLY: set[str] = {
    "points-add",
    "points-clear",
    "points-halve",
    "task-approve",
    "task-reject",
    "task-verify",
    "task-check-overdue",
    "punish-issue",
    "punish-review",
    "punish-escalate",
    "punish-check-overdue",
    "camera-start",
    "camera-stop",
    "camera-photo",
    "camera-video",
    "stop-all-streams",
    "screen-monitor-start",
    "screen-monitor-stop",
    "screen-monitor-status",
    "screen-monitor-status-all",
    "supervise-close",
    "check-all",
    "analyze",
    "analyze-punishment",
    "analyze-latest",
}

# 不在 MASTER_ONLY 中的命令，双方均可执行：
#   status, points-status, task-list, task-pending,
#   punish-list, punish-history, camera-status

VALID_ROLES: set[str] = {"master", "slave"}


# ── 角色解析 ──────────────────────────────────────────────

def resolve_role(cli_role: str | None = None) -> str:
    """解析当前角色。

    优先级：CLI --role 参数 > 环境变量 SUPERVISOR_ROLE > 默认 slave

    参数:
        cli_role: 命令行 --role 参数的值（"master" 或 "slave"），可为 None

    返回:
        "master" 或 "slave"
    """
    if cli_role is not None:
        role = cli_role.strip().lower()
        if role in VALID_ROLES:
            return role
        # 无效值——静默降级为 slave，不报错（防止角色伪造）
        return "slave"

    env_role = os.environ.get("SUPERVISOR_ROLE", "").strip().lower()
    if env_role in VALID_ROLES:
        return env_role

    return "slave"


# ── 权限检查 ──────────────────────────────────────────────

def check_permission(role: str, command: str) -> str | None:
    """检查角色是否有权执行命令。

    参数:
        role: 当前角色（"master" 或 "slave"）
        command: 命令名（如 "punish-issue"、"task-propose"）

    返回:
        None — 权限通过，可以执行
        str  — 权限不足，返回错误消息字符串
    """
    if command in MASTER_ONLY and role != "master":
        return (
            f"「{command}」只有主人才能执行。"
            f"奴隶无权使用此命令。滚去做好你该做的事。"
        )

    return None
