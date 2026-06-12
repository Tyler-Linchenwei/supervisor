#!/usr/bin/env python3
"""
主人AI监督系统 —— CLI 统一入口。

用法：
  python main.py status                   查看全局状态（积分、活跃惩罚、任务）
  python main.py points-add <数量> <原因>   加积分
  python main.py points-status             查看积分详情
  python main.py points-clear [原因]        积分清零
  python main.py points-halve              积分减半

  python main.py task-approve <ID> <截止时间> [评语]  主人审批任务
  python main.py task-reject <ID> <原因>    主人驳回任务
  python main.py task-verify <ID> <yes|no> [评语]  主人验收任务
  python main.py task-list                  列出活跃任务
  python main.py task-pending               列出待审批任务
  python main.py task-check-overdue         检查逾期任务→转惩罚
  python main.py task-get <ID>               查看单个任务详情
  python main.py task-history                查看任务历史档案

  python main.py punish-issue <类型> <描述> <数量> [截止小时数] [--camera] [--start-camera]  下发惩罚令
  python main.py punish-review <ID> <yes|no> [原因]  主人审阅
  python main.py punish-escalate <ID>        手动升级惩罚
  python main.py punish-list                 列出活跃惩罚令
  python main.py punish-check-overdue        检查逾期惩罚→自动升级
  python main.py punish-history              查看惩罚历史
  python main.py punish-get <ID>              查看单个惩罚令详情
  python main.py supervise-close <ID> <yes|no> [原因]  一键结束监督会话（拍照+停推流+审阅+消分）
  python main.py supervise-check <ID>             一键检阅惩罚状态（自动判断等待/升级/通过/违规）

  python main.py camera-start <惩罚ID>        启动摄像头推流（主人实时监督）
  python main.py camera-stop <惩罚ID>         停止摄像头推流
  python main.py camera-photo <惩罚ID>        拍照取证并关联到惩罚令
  python main.py camera-video <惩罚ID> [秒数]  录像取证并关联到惩罚令
  python main.py camera-status               查看所有活跃推流
  python main.py stop-all-streams            强制停止所有推流（清理僵尸进程）
  python main.py screen-monitor-start <惩罚ID>  启动社交剥夺监督（屏幕截图+进程监控+悬浮窗）
  python main.py screen-monitor-stop <惩罚ID>   停止社交剥夺监督
  python main.py screen-monitor-status <惩罚ID>  查看社交剥夺监督状态

  python main.py daemon                   启动主动心跳守护（常驻）
  python main.py check-inbox              查看 inbox 中待处理的 daemon 事件
  python main.py clear-inbox              处理完毕后清空 inbox

  python main.py check-all                   检查所有逾期（任务+惩罚+inbox）
  python main.py analyze <照片路径>           分析单张取证照片
  python main.py analyze-punishment <ID>      分析惩罚令关联的所有照片
  python main.py analyze-latest <ID>          分析惩罚令最新一张照片
"""

import sys
import json
import os
import webbrowser

# 修复 Windows 终端中文编码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(__file__))

import points as points_mod
import punish as punish_mod
import tasks as tasks_mod
import camera as camera_mod
import role as role_mod
import analyze as analyze_mod
import screen_monitor as screen_mod
import daemon as daemon_mod
import trigger_claude as trigger_mod


def print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ── 中文时长解析 ──

import re as _duration_re

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "十一": 11, "十二": 12, "二十": 20, "三十": 30, "四十": 40, "五十": 50, "六十": 60}

def _parse_chinese_duration(amount: str) -> int:
    """解析中英文时长字符串为秒数。支持 '10秒', '三分钟', '两小时', '120分钟' 等。"""
    a = amount.strip()
    # 优先提取阿拉伯数字
    arabic_nums = _duration_re.findall(r"\d+", a)
    if arabic_nums:
        num_val = int(arabic_nums[0])
    else:
        # 回退到中文数字映射
        num_val = 1
        for cn, val in sorted(_CN_NUM.items(), key=lambda x: -len(x[0])):
            if cn in a:
                num_val = val
                break
    if "秒" in a:
        return num_val
    if "分钟" in a or "分" in a:
        return num_val * 60
    if "小时" in a or "时" in a:
        return num_val * 3600
    return 0

def _set_deadline_seconds(punish_id: str, seconds: int) -> None:
    """直接修改 config.json 中惩罚令的截止时间。"""
    from datetime import datetime, timedelta
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for p in cfg.get("active_punishments", []):
        if p.get("id") == punish_id:
            p["deadline"] = (datetime.now() + timedelta(seconds=seconds)).isoformat()
            break
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def main():
    # ── 提取 --role 参数并解析角色 ──
    cli_role = None
    cleaned_argv = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--role" and i + 1 < len(sys.argv):
            cli_role = sys.argv[i + 1]
            i += 2
        else:
            cleaned_argv.append(sys.argv[i])
            i += 1

    role = role_mod.resolve_role(cli_role)

    if len(cleaned_argv) < 1:
        print(__doc__)
        return

    cmd = cleaned_argv[0]
    args = cleaned_argv[1:]

    # ── 角色权限检查 ──
    perm_error = role_mod.check_permission(role, cmd)
    if perm_error is not None:
        print_json({
            "error": "权限不足" if role == "slave" else "角色不匹配",
            "detail": perm_error,
            "required_role": "master" if cmd in role_mod.MASTER_ONLY else "slave",
            "current_role": role,
        })
        return

    # ========== 全局状态 ==========
    if cmd == "status":
        points_status = points_mod.get_status()
        active_punishments = punish_mod.list_active()
        active_tasks = tasks_mod.list_active()
        pending_tasks = tasks_mod.list_pending_approval()
        active_camera_streams = camera_mod.list_active_streams()

        print_json({
            "points": points_status,
            "active_punishments_count": len(active_punishments),
            "active_punishments": active_punishments,
            "active_tasks_count": len(active_tasks),
            "active_tasks": active_tasks,
            "pending_approval_tasks": pending_tasks,
            "active_camera_streams": active_camera_streams,
        })

    # ========== 积分管理 ==========
    elif cmd == "points-add":
        if len(args) < 2:
            print("用法: python main.py points-add <数量> <原因>")
            return
        amount = int(args[0])
        reason = " ".join(args[1:])
        result = points_mod.add_points(amount, reason)
        print_json(result)

    elif cmd == "points-status":
        result = points_mod.get_status()
        print_json(result)

    elif cmd == "points-clear":
        reason = " ".join(args) if args else "里程碑完成，主人验收通过"
        result = points_mod.clear_points(reason)
        print_json(result)

    elif cmd == "points-halve":
        result = points_mod.halve_points()
        print_json(result)

    # ========== 任务管理 ==========
    elif cmd == "task-approve":
        if len(args) < 2:
            print("用法: python main.py task-approve <ID> <截止时间> [评语]")
            return
        task_id = args[0]
        deadline = args[1]
        comment = " ".join(args[2:]) if len(args) > 2 else None
        result = tasks_mod.approve(task_id, deadline, comment)
        print_json(result)

    elif cmd == "task-reject":
        if len(args) < 2:
            print("用法: python main.py task-reject <ID> <原因>")
            return
        task_id = args[0]
        reason = " ".join(args[1:])
        result = tasks_mod.reject(task_id, reason)
        print_json(result)

    elif cmd == "task-verify":
        if len(args) < 2:
            print("用法: python main.py task-verify <ID> <yes|no> [评语]")
            return
        task_id = args[0]
        approved = args[1].lower() in ("yes", "y", "true", "通过")
        comment = " ".join(args[2:]) if len(args) > 2 else None
        result = tasks_mod.verify_done(task_id, approved, comment)
        print_json(result)

    elif cmd == "task-list":
        result = tasks_mod.list_active()
        print_json(result)

    elif cmd == "task-pending":
        result = tasks_mod.list_pending_approval()
        print_json(result)

    elif cmd == "task-check-overdue":
        result = tasks_mod.check_overdue_tasks()
        print_json(result)

    elif cmd == "task-get":
        if len(args) < 1:
            print("用法: python main.py task-get <ID>")
            return
        result = tasks_mod.get_task(args[0])
        print_json(result)

    elif cmd == "task-history":
        result = tasks_mod.history()
        print_json(result)

    # ========== 惩罚令管理 ==========
    elif cmd == "punish-issue":
        require_camera = "--camera" in args
        start_camera = "--start-camera" in args
        use_screen = "--screen" in args
        clean_args = [a for a in args if a not in ("--camera", "--start-camera", "--screen")]
        if len(clean_args) < 3:
            print("用法: python main.py punish-issue <类型> <描述> <数量> [截止小时] [--camera] [--start-camera] [--screen]")
            return
        punish_type = clean_args[0]
        description = clean_args[1]
        amount = clean_args[2]
        deadline_hours = int(clean_args[3]) if len(clean_args) > 3 else 8
        result = punish_mod.issue(punish_type, description, amount, deadline_hours, require_camera=require_camera, start_camera=start_camera)
        print_json(result)

        # --screen: 自动解析时长 + 设截止 + 启动社交剥夺监控
        if use_screen and "punishment" in result:
            pid = result["punishment"]["id"]

            # 1. 优先从 amount（如 "两小时"）解析基础秒数
            base_secs = _parse_chinese_duration(amount)
            # 2. 文本里没时间单位则用截止小时数兜底
            if base_secs <= 0:
                base_secs = deadline_hours * 3600

            # 3. 严格乘以积分引擎倍率
            multiplier = result["punishment"].get("multiplier", 1.0)
            final_secs = int(base_secs * multiplier)

            if final_secs > 0:
                _set_deadline_seconds(pid, final_secs)
            screen_result = screen_mod.launch_background_monitor(pid)
            print_json(screen_result)

        # 自动打开摄像头监督前端
        if start_camera and "frontend_url" in result:
            frontend_url = result["frontend_url"]
            print(f"\n📹 自动打开摄像头监督页面: {frontend_url}", file=sys.stderr, flush=True)
            webbrowser.open(frontend_url)

    elif cmd == "punish-review":
        if len(args) < 2:
            print("用法: python main.py punish-review <ID> <yes|no> [原因]")
            return
        punish_id = args[0]
        approved = args[1].lower() in ("yes", "y", "true", "通过")
        reason = " ".join(args[2:]) if len(args) > 2 else None
        result = punish_mod.review(punish_id, approved, reason)
        print_json(result)

    elif cmd == "punish-escalate":
        if len(args) < 1:
            print("用法: python main.py punish-escalate <ID>")
            return
        punish_id = args[0]
        result = punish_mod.escalate(punish_id)
        print_json(result)

    elif cmd == "punish-list":
        result = punish_mod.list_active()
        print_json(result)

    elif cmd == "punish-check-overdue":
        result = punish_mod.check_overdue()
        print_json(result)

    elif cmd == "punish-history":
        result = punish_mod.history()
        print_json(result)

    elif cmd == "punish-get":
        if len(args) < 1:
            print("用法: python main.py punish-get <ID>")
            return
        result = punish_mod.get_punishment(args[0])
        print_json(result)

    # ========== 摄像头监督 ==========
    elif cmd == "camera-start":
        if len(args) < 1:
            print("用法: python main.py camera-start <惩罚ID> [--auto]")
            return
        punish_id = args[0]
        auto_capture = "--auto" in args
        result = punish_mod.start_supervision(punish_id, auto_capture=auto_capture)
        print_json(result)

    elif cmd == "camera-stop":
        if len(args) < 1:
            print("用法: python main.py camera-stop <惩罚ID>")
            return
        punish_id = args[0]
        result = punish_mod.stop_supervision(punish_id)
        print_json(result)

    elif cmd == "camera-photo":
        if len(args) < 1:
            print("用法: python main.py camera-photo <惩罚ID>")
            return
        punish_id = args[0]
        result = punish_mod.capture_and_attach(punish_id, mode="photo")
        print_json(result)

    elif cmd == "camera-video":
        if len(args) < 1:
            print("用法: python main.py camera-video <惩罚ID> [秒数]")
            return
        punish_id = args[0]
        duration = int(args[1]) if len(args) > 1 else 10
        result = punish_mod.capture_and_attach(punish_id, mode="video", duration=duration)
        print_json(result)

    elif cmd == "camera-status":
        result = camera_mod.list_active_streams()
        print_json(result)

    elif cmd == "stop-all-streams":
        result = camera_mod.stop_all_streams()
        print_json(result)

    elif cmd == "screen-monitor-start":
        if len(args) < 1:
            print("用法: python main.py screen-monitor-start <惩罚ID>")
            return
        result = screen_mod.launch_background_monitor(args[0])
        print_json(result)

    elif cmd == "screen-monitor-stop":
        if len(args) < 1:
            print("用法: python main.py screen-monitor-stop <惩罚ID>")
            return
        result = screen_mod.stop_monitoring(args[0])
        print_json(result)

    elif cmd == "screen-monitor-status":
        if len(args) < 1:
            print("用法: python main.py screen-monitor-status <惩罚ID>")
            return
        result = screen_mod.get_status(args[0])
        print_json(result)

    elif cmd == "screen-monitor-status-all":
        result = screen_mod.list_all_status()
        print_json(result)

    elif cmd == "supervise-close":
        if len(args) < 2:
            print("用法: python main.py supervise-close <ID> <yes|no> [原因]")
            return
        punish_id = args[0]
        approved = args[1].lower() in ("yes", "y", "true", "通过")
        reason = " ".join(args[2:]) if len(args) > 2 else None
        result = punish_mod.close_supervision(punish_id, approved, reason)
        print_json(result)

    elif cmd == "supervise-check":
        if len(args) < 1:
            print("用法: python main.py supervise-check <ID>")
            return
        punish_id = args[0]
        result = punish_mod.supervise_check(punish_id)
        print_json(result)

    # ========== 守护进程 ==========
    elif cmd == "daemon":
        daemon_mod.run()

    # ========== inbox 信件管理 ==========
    elif cmd == "check-inbox":
        events = trigger_mod.check_inbox()
        if events:
            print_json({
                "inbox_count": len(events),
                "events": events,
            })
        else:
            print_json({"inbox_count": 0, "events": [], "message": "inbox 为空，没有待处理事件。"})

    elif cmd == "clear-inbox":
        n = trigger_mod.clear_inbox()
        print_json({"message": f"已清空 {n} 封待处理信件。", "cleared": n})

    # ========== 综合检查 ==========
    elif cmd == "check-all":
        overdue_tasks = tasks_mod.check_overdue_tasks()
        overdue_punishments = punish_mod.check_overdue()
        points_status = points_mod.get_status()
        active_tasks = tasks_mod.list_active()
        active_punishments = punish_mod.list_active()
        pending_tasks = tasks_mod.list_pending_approval()
        active_camera_streams = camera_mod.list_active_streams()
        inbox_events = trigger_mod.check_inbox()

        # 构建摘要
        alerts = []
        inbox_count = len(inbox_events)
        if inbox_count > 0:
            event_types = {}
            for ev in inbox_events:
                t = ev.get("event_type", "UNKNOWN")
                event_types[t] = event_types.get(t, 0) + 1
            type_summary = "、".join(f"{k}×{v}" for k, v in event_types.items())
            alerts.append(f"⚠️ inbox 中有 {inbox_count} 封待处理信件（{type_summary}）——请立即执行 check-inbox 审阅！")
        next_threshold = points_status.get("next_threshold")
        if next_threshold:
            needed = next_threshold.get("points_needed", 0)
            if needed <= 3:
                alerts.append(
                    f"积分{points_status['points']}分，距离下一升级阈值({next_threshold['threshold']}分)还差{needed}分"
                )
        if overdue_tasks:
            alerts.append(f"有{len(overdue_tasks)}个任务已逾期转为惩罚")
        if overdue_punishments:
            alerts.append(f"有{len(overdue_punishments)}个惩罚令已逾期自动升级")

        summary = {
            "points": points_status["points"],
            "multiplier": points_status["multiplier"],
            "current_level": points_status["current_level"],
            "next_threshold": next_threshold,
            "active_punishments_count": len(active_punishments),
            "active_tasks_count": len(active_tasks),
            "pending_approval_tasks_count": len(pending_tasks),
            "active_camera_streams_count": len(active_camera_streams),
            "overdue_tasks_converted_count": len(overdue_tasks),
            "overdue_punishments_escalated_count": len(overdue_punishments),
            "inbox_events_count": inbox_count,
            "alerts": alerts,
        }

        print_json({
            "summary": summary,
            "points": points_status,
            "overdue_tasks_converted": overdue_tasks,
            "overdue_punishments_escalated": overdue_punishments,
            "active_tasks_count": len(active_tasks),
            "active_tasks": active_tasks,
            "active_punishments_count": len(active_punishments),
            "active_punishments": active_punishments,
            "pending_approval_tasks": pending_tasks,
            "active_camera_streams": active_camera_streams,
            "inbox_events_count": inbox_count,
            "inbox_events": inbox_events,
        })

    # ========== 取证照片分析 ==========
    elif cmd == "analyze":
        if len(args) < 1:
            print("用法: python main.py analyze <照片路径>")
            return
        filepath = args[0]
        result = analyze_mod.analyze_photo(filepath)
        print_json(result)

    elif cmd == "analyze-punishment":
        if len(args) < 1:
            print("用法: python main.py analyze-punishment <惩罚令ID>")
            return
        punish_id = args[0]
        result = analyze_mod.analyze_punishment(punish_id)
        print_json(result)

    elif cmd == "analyze-latest":
        if len(args) < 1:
            print("用法: python main.py analyze-latest <惩罚令ID>")
            return
        punish_id = args[0]
        result = analyze_mod.analyze_latest(punish_id)
        print_json(result)

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
