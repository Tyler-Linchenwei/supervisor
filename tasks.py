"""
日常任务管理 —— 奴隶报任务、主人审批、逾期转惩罚。

流程：
1. 奴隶在对话中报今日任务 → "主人，今天我要完成XXX"
2. 主人审批 → 通过 / 调整 / 驳回
3. 任务录入系统，设截止时间
4. 截止时间到 → 检查完成状态
5. 逾期未完成 → 自动转换为惩罚令
"""

import json
import os
import tempfile
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "archive.json")


def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(cfg):
    dir_name = os.path.dirname(CONFIG_PATH)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


def _load_archive():
    with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_archive(arc):
    dir_name = os.path.dirname(ARCHIVE_PATH)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(arc, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, ARCHIVE_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


def approve(task_id: str, deadline: str, comment: str = None) -> dict:
    """
    主人审批通过任务，设定截止时间。
    deadline: "22:00" 或 "2026-06-11T22:00:00" 格式。
    """
    cfg = _load_config()

    # 解析 deadline（兼容 "HH:MM" 和 "HH:MM:SS"）
    if "T" in deadline:
        deadline_dt = datetime.fromisoformat(deadline)
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        if deadline.count(":") == 1:
            deadline_dt = datetime.fromisoformat(f"{today}T{deadline}:00")
        else:
            deadline_dt = datetime.fromisoformat(f"{today}T{deadline}")

    for t in cfg["active_tasks"]:
        if t["id"] == task_id:
            if t["status"] != "proposed":
                return {"error": f"任务状态为 '{t['status']}'，不能审批。"}
            t["status"] = "approved"
            t["approved_by_master"] = True
            t["deadline"] = deadline_dt.isoformat()
            t["master_comment"] = comment
            _save_config(cfg)

            return {
                "message": f"任务已审批。截止时间：{deadline_dt.strftime('%H:%M')}。完不成你知道后果。",
                "task": t,
            }

    return {"error": f"未找到任务 ID={task_id}。"}


def reject(task_id: str, reason: str) -> dict:
    """主人驳回任务提议。"""
    cfg = _load_config()
    arc = _load_archive()

    for t in cfg["active_tasks"]:
        if t["id"] == task_id:
            t["status"] = "rejected"
            t["master_comment"] = reason
            cfg["active_tasks"].remove(t)
            arc["task_history"].append(t)
            _save_config(cfg)
            _save_archive(arc)

            return {
                "message": f"任务驳回：{reason}。重新想，想好了再来报。",
                "task": t,
            }

    return {"error": f"未找到任务 ID={task_id}。"}


def verify_done(task_id: str, approved: bool, comment: str = None) -> dict:
    """主人验收任务——真正完成或打回。"""
    cfg = _load_config()
    arc = _load_archive()

    for t in cfg["active_tasks"]:
        if t["id"] == task_id:
            if t["status"] != "done":
                return {"error": f"任务状态为 '{t['status']}'，主人不能验收。"}

            if approved:
                t["status"] = "done"
                t["master_comment"] = comment or "验收通过"
                cfg["active_tasks"].remove(t)
                arc["task_history"].append(t)
                _save_config(cfg)
                _save_archive(arc)
                return {
                    "message": "验收通过。算你这次没让我失望。",
                    "task": t,
                }
            else:
                t["status"] = "approved"  # 打回重新做
                t["master_comment"] = comment or "不合格，重做。"
                t["completed_at"] = None
                _save_config(cfg)
                return {
                    "message": f"不合格！{t['master_comment']} 重做。",
                    "task": t,
                }

    return {"error": f"未找到任务 ID={task_id}。"}


def convert_to_punishment(task_id: str) -> dict:
    """任务逾期处理：标记任务为未完成并归档，触发报警要求主人（Claude）主动下发定制惩罚。"""
    cfg = _load_config()
    arc = _load_archive()

    for t in cfg["active_tasks"]:
        if t["id"] == task_id:
            if t["status"] not in ("approved", "done"):
                return {"error": f"任务状态为 '{t['status']}'，不能处理逾期。"}

            # 1. 标记任务为已逾期/失败
            t["status"] = "missed"

            # 2. 从活跃列表移除并追加到归档
            cfg["active_tasks"] = [x for x in cfg["active_tasks"] if x["id"] != task_id]
            arc["task_history"].append(t)

            # 3. 安全存盘
            _save_config(cfg)
            _save_archive(arc)

            # 4. 返回系统报警，触发主人（Claude）的定制惩罚裁决
            return {
                "message": (
                    f"【系统严重警告】任务已逾期作废！\n"
                    f"任务内容：{t['description']}。\n"
                    f"请主人（Claude）立刻发挥创意，调用 `punish-issue` 工具为奴隶下发定制的随机惩罚令！"
                ),
                "task": t,
                "_llm_directive": "MANDATORY_TOOL_CALL: punish-issue",
            }

    return {"error": f"未找到任务 ID={task_id}。"}


def check_overdue_tasks() -> list:
    """检查所有逾期任务，自动转惩罚。返回转换列表。"""
    cfg = _load_config()
    now = datetime.now()
    converted_list = []

    overdue_ids = [
        t["id"] for t in cfg["active_tasks"]
        if t["status"] in ("approved", "done")
        and t.get("deadline")
        and datetime.fromisoformat(t["deadline"]) < now
    ]

    for tid in overdue_ids:
        result = convert_to_punishment(tid)
        if "error" not in result:
            converted_list.append(result)

    return converted_list


def list_active() -> list:
    """列出所有活跃任务。"""
    cfg = _load_config()
    return cfg["active_tasks"]


def list_pending_approval() -> list:
    """列出待主人审批的任务提议。"""
    cfg = _load_config()
    return [t for t in cfg["active_tasks"] if t["status"] == "proposed"]


def get_task(task_id: str) -> dict:
    """查看单个任务详情。"""
    cfg = _load_config()
    for t in cfg["active_tasks"]:
        if t["id"] == task_id:
            return t
    arc = _load_archive()
    for t in arc["task_history"]:
        if t["id"] == task_id:
            return t
    return {"error": f"未找到任务 ID={task_id}"}


def history(limit: int = 20) -> list:
    """查看任务历史档案。"""
    arc = _load_archive()
    return arc["task_history"][-limit:]
