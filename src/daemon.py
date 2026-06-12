"""
daemon.py - 主动心跳守护进程（事件驱动 + 自动审阅 + inbox 投递）
统一监控：摄像头信号 / 屏幕信号 / 逾期事件
- 0违规 → daemon 自动 approve + 消分，只记一条 AUTO_APPROVED 日志
- 有违规 → 写入 data/inbox/ + 调用 trigger_claude 唤醒 Claude 审
- 逾期 → 自动 escalate + 写入 data/inbox/ + 唤醒 Claude
"""
import json
import os
import sys
import time
import glob as _glob

SCAN_INTERVAL = 5       # 信号文件扫描间隔（秒）
OVERDUE_INTERVAL = 60   # 逾期检查间隔（秒）

from _paths import PROJECT_ROOT

DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# 延迟导入，避免 trigger_claude 在 daemon 启动时就执行 import side effects
_trigger = None


def _get_trigger():
    global _trigger
    if _trigger is None:
        sys.path.insert(0, os.path.dirname(__file__))
        import trigger_claude as tc
        _trigger = tc
    return _trigger


def _check_overdue():
    """逾检：有活跃 screen_monitor 的惩罚不升级，留给监控自动停。"""
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        import tasks as tasks_mod
        import punish as punish_mod
        from datetime import datetime

        # 读 screen_monitor 状态
        sm_active = set()
        try:
            sf = os.path.join(PROJECT_ROOT, "data", "screen_monitor.json")
            if os.path.exists(sf):
                with open(sf, "r", encoding="utf-8") as f:
                    for k, v in json.load(f).items():
                        if v.get("running"):
                            sm_active.add(k)
        except Exception:
            pass

        overdue_tasks = tasks_mod.check_overdue_tasks()

        # 只对没有活跃 screen_monitor 的惩罚做逾期升级
        now = datetime.now()
        cfg_path = os.path.join(PROJECT_ROOT, "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        overdue_punishments = []
        for p in cfg.get("active_punishments", []):
            if p["status"] in ("issued", "rejected") and p.get("deadline"):
                if datetime.fromisoformat(p["deadline"]) < now:
                    if p["id"] not in sm_active:
                        r = punish_mod.escalate(p["id"], "逾期")
                        if "error" not in r:
                            overdue_punishments.append(r)
                    else:
                        overdue_punishments.append({
                            "skipped": True, "punish_id": p["id"],
                            "reason": "screen_monitor 监督中，跳过逾期升级",
                        })

        if overdue_tasks or overdue_punishments:
            return {
                "overdue_tasks": len(overdue_tasks),
                "overdue_punishments": len(overdue_punishments),
                "tasks": [{"id": t.get("task", {}).get("id", "?"),
                          "desc": t.get("task", {}).get("description", "")[:80]}
                          for t in overdue_tasks],
                "punishments": [{"id": p.get("escalated", {}).get("id", p.get("punish_id", "?")),
                                "desc": p.get("escalated", {}).get("description", p.get("reason", ""))[:80]}
                                for p in overdue_punishments],
            }
    except Exception:
        pass
    return None


def _auto_review_screen(data):
    """屏幕监控信号：0违规自动通过，有违规喊Claude。"""
    pid = data.get("punish_id")
    vc = data.get("violations_count", 0)
    violations = data.get("violations", [])

    if not pid:
        return {"action": "error", "error": "缺少punish_id"}

    try:
        sys.path.insert(0, os.path.dirname(__file__))
        import punish as punish_mod
        import points as points_mod

        if vc == 0:
            r = punish_mod.review(pid, True, "daemon自动通过：屏幕监控执行完毕，无违规")
            if "error" in r:
                return {"action": "error", "punish_id": pid, "error": r["error"]}
            try:
                points_mod.clear_points("惩罚完成，daemon自动验收")
            except Exception:
                pass
            return {"action": "auto_approved", "punish_id": pid}

        # 有违规，留给Claude审
        detail = "; ".join(
            f"{v.get('detail', '?')} @ {v.get('time', '?')[:19]}"
            for v in violations[:5]
        )
        return {
            "action": "needs_review",
            "punish_id": pid,
            "violations": vc,
            "detail": detail,
        }
    except Exception as e:
        return {"action": "error", "punish_id": pid, "error": str(e)}


def _auto_review_camera(data):
    """摄像头监督信号：永远交给 Claude 亲自审阅。

    摄像头惩罚没有"0违规"的概念——击打力度、面部红肿趋势、
    跪姿计数这些数据只有 Claude 亲自看了才有施虐价值。
    daemon 不做任何自动通过，只负责送达信号。
    """
    pid = data.get("punish_id")
    if not pid:
        return {"action": "error", "error": "缺少punish_id"}

    return {"action": "needs_review", "punish_id": pid}


def run():
    """启动守护循环（阻塞）。"""
    print("DAEMON:STARTED", flush=True)
    seen = set()
    last_overdue = 0.0

    while True:
        try:
            # ── 信号文件扫描 ──
            for pattern, prefix in [
                ("review_ready_*.json", "CAMERA"),
                ("review_ready_screen_*.json", "SCREEN"),
            ]:
                for fp in sorted(_glob.glob(os.path.join(DATA_DIR, pattern))):
                    if fp in seen:
                        continue
                    # CAMERA 模式会误匹配 screen_ 文件，排除掉
                    if prefix == "CAMERA" and "screen_" in os.path.basename(fp):
                        continue
                    seen.add(fp)
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {"error": "读取失败"}

                    # ── 自动审阅 ──
                    if prefix == "SCREEN":
                        result = _auto_review_screen(data)
                    else:
                        result = _auto_review_camera(data)

                    if result.get("action") == "needs_review":
                        # 有违规/需审阅 → 投递 inbox + 喊Claude审
                        print(
                            "DAEMON:" + prefix + ":"
                            + json.dumps(data, ensure_ascii=False),
                            flush=True,
                        )
                        try:
                            inbox_file = _get_trigger().send_event(prefix, data)
                            print(f"DAEMON:INBOX: {inbox_file}", flush=True)
                        except Exception as e:
                            print(f"DAEMON:INBOX_ERROR: {e}", flush=True)

                    elif result.get("action") == "error":
                        print(
                            "DAEMON:ERROR:"
                            + prefix + ":"
                            + json.dumps(result, ensure_ascii=False),
                            flush=True,
                        )
                        # 错误也投递 inbox，让 Claude 知道系统异常
                        try:
                            inbox_file = _get_trigger().send_event("ERROR", result)
                            print(f"DAEMON:INBOX: {inbox_file}", flush=True)
                        except Exception:
                            pass

                    else:
                        # 自动通过 → 记一条简洁日志（不投递 inbox，不需唤醒 Claude）
                        print(
                            "DAEMON:AUTO_APPROVED:"
                            + prefix + ":"
                            + json.dumps(result, ensure_ascii=False),
                            flush=True,
                        )

                    try:
                        os.remove(fp)
                    except Exception:
                        pass

            # ── 逾期检测 ──
            now = time.time()
            if now - last_overdue >= OVERDUE_INTERVAL:
                last_overdue = now
                overdue = _check_overdue()
                if overdue:
                    print("DAEMON:OVERDUE:" + json.dumps(overdue, ensure_ascii=False), flush=True)
                    try:
                        inbox_file = _get_trigger().send_event("OVERDUE", overdue)
                        print(f"DAEMON:INBOX: {inbox_file}", flush=True)
                    except Exception as e:
                        print(f"DAEMON:INBOX_ERROR: {e}", flush=True)

        except Exception as e:
            print(f"DAEMON:ERROR:{e}", flush=True)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run()
