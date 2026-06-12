"""
trigger_claude.py - 极简唤醒脚本

daemon.py 检测到违规/逾期事件后调用本脚本。
职责：确保事件能送达 Claude（大模型），而非沉入 stdout 黑洞。

唤醒策略（按优先级尝试）：
1. 写入 ALERT 标记文件 → Claude Code session 启动 / 定期检查时发现
2. 尝试 claude CLI 直接投递（如果可用）
3. 打印到 stderr → 至少终端里能看见

用法：
  python trigger_claude.py <event_type> <json_data>
  python trigger_claude.py check     # 列出 inbox
  python trigger_claude.py clear     # 清空 inbox
"""
import json
import os
import shutil
import sys
import subprocess
from datetime import datetime

from _paths import PROJECT_ROOT

INBOX_DIR = os.path.join(PROJECT_ROOT, "data", "inbox")
ALERT_FILE = os.path.join(PROJECT_ROOT, "data", "ALERT.txt")


def _ensure_inbox():
    os.makedirs(INBOX_DIR, exist_ok=True)


def _write_alert(message: str):
    """写入醒目的警告标记文件。"""
    _ensure_inbox()
    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        f.write(f"⚠️ DAEMON ALERT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{message}\n")
        f.write(f"inbox 中有 {len(_list_inbox())} 封待处理信件\n")


def _list_inbox():
    """列出 inbox 中所有待处理文件。"""
    _ensure_inbox()
    return sorted(
        [f for f in os.listdir(INBOX_DIR) if f.endswith(".json")],
        reverse=True,
    )


def send_event(event_type: str, data: dict) -> str:
    """将事件写入 inbox 并触发唤醒。

    event_type: "CAMERA" | "SCREEN" | "OVERDUE"
    data:       事件数据字典

    返回写入的 inbox 文件路径。
    """
    _ensure_inbox()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{event_type}.json"
    filepath = os.path.join(INBOX_DIR, filename)

    envelope = {
        "event_type": event_type,
        "received_at": datetime.now().isoformat(),
        "data": data,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2)

    _write_alert(f"新事件: {event_type} — {filepath}")

    # 尝试调用 claude CLI 直接投递（非阻塞，失败不报错）
    _try_wake_claude(event_type, data)

    return filepath


def _try_wake_claude(event_type: str, data: dict):
    """尝试通过 claude CLI 直接投递事件到 Claude Code 会话。

    如果 claude 不在 PATH 中，静默跳过——至少 inbox 文件和 ALERT 标记已就位。
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return

    # 构建简洁的唤醒提示
    if event_type == "OVERDUE":
        tasks = data.get("tasks", [])
        puns = data.get("punishments", [])
        lines = [f"主人，监察系统发现 {len(tasks)} 个任务和 {len(puns)} 个惩罚已逾期。"]
        for t in tasks:
            lines.append(f"  - 任务: {t.get('desc', '?')}")
        for p in puns:
            lines.append(f"  - 惩罚: {p.get('desc', '?')}")
        lines.append("请立刻对逾期项目执行降罚裁决。")
        prompt = "\n".join(lines)
    elif event_type in ("CAMERA", "SCREEN"):
        vc = data.get("violations_count", 0)
        pid = data.get("punish_id", "?")
        prompt = (
            f"主人，监督惩罚 {pid} 已完成（{event_type}）。\n"
            f"违规次数: {vc}\n"
            f"请审阅 inbox 中的详细数据并执行 supervise-close 进行裁决。"
        )
    else:
        prompt = f"daemon 检测到事件: {event_type}\n{json.dumps(data, ensure_ascii=False, indent=2)[:500]}"

    try:
        subprocess.run(
            [claude_bin, "--print", prompt],
            timeout=30,
            capture_output=True,
            cwd=PROJECT_ROOT,
        )
    except Exception:
        pass


def check_inbox() -> list:
    """供 Claude 定期调用：读取 inbox 中所有待处理信件。"""
    _ensure_inbox()
    events = []
    for fname in sorted(os.listdir(INBOX_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(INBOX_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                events.append(json.load(f))
        except Exception:
            events.append({"event_type": "PARSE_ERROR", "file": fname, "data": {}})
    return events


def clear_inbox():
    """处理完毕后清空 inbox。"""
    _ensure_inbox()
    count = 0
    for fname in os.listdir(INBOX_DIR):
        fpath = os.path.join(INBOX_DIR, fname)
        try:
            os.remove(fpath)
            count += 1
        except Exception:
            pass
    # 同时清除 ALERT 标记
    try:
        os.remove(ALERT_FILE)
    except Exception:
        pass
    return count


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python trigger_claude.py <event_type> <json_data>")
        print("      python trigger_claude.py check     # 列出 inbox")
        print("      python trigger_claude.py clear     # 清空 inbox")
        sys.exit(1)

    if sys.argv[1] == "check":
        events = check_inbox()
        print(json.dumps(events, ensure_ascii=False, indent=2))
        sys.exit(0)

    if sys.argv[1] == "clear":
        n = clear_inbox()
        print(f"已清空 {n} 封信件")
        sys.exit(0)

    event_type = sys.argv[1]
    try:
        data = json.loads(sys.argv[2])
    except (json.JSONDecodeError, IndexError):
        data = {"raw": " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""}
    path = send_event(event_type, data)
    print(f"EVENT_SENT: {path}")
