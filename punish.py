"""
惩罚令模块 —— 主人下发惩罚、奴隶提交证明、主人审阅。

核心流程：
1. 主人下发惩罚令 → 生成 punish_id
2. 奴隶回家执行 → 录视频/拍照
3. 奴隶提交证明 → 调用 submit_proof
4. 主人在对话中审阅 → 通过/驳回/加码
5. 逾期未提交 → 自动升级惩罚
"""

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "archive.json")
from points import get_current_multiplier


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


def issue(
    punish_type: str,
    description: str,
    amount: str,
    deadline_hours: int = 8,
    multiplier_override: float = None,
    require_camera: bool = False,
    start_camera: bool = False,
) -> dict:
    """
    主人下发惩罚令。

    punish_type: "耳光" | "罚站" | "罚跪" | "戒尺" | "皮带" | "综合" | "随机羞辱" | "着装控制" | "社交剥夺" | "羞耻暴露" | "摄像头监督"
    description: 主人写的具体惩罚内容
    amount: 数量/时长描述 (如 "五十下"、"三十分钟")
    deadline_hours: 截止时间（小时），默认8小时（当晚）
    multiplier_override: 主人手动覆盖倍率，None则使用积分自动倍率
    require_camera: 是否要求开摄像头实时监督
    start_camera: 是否下发后立即启动摄像头推流（自动设置 require_camera=True）
    """
    cfg = _load_config()
    arc = _load_archive()

    if start_camera:
        require_camera = True

    multiplier = multiplier_override if multiplier_override is not None else get_current_multiplier()

    punish_id = str(uuid.uuid4())[:8]
    now = datetime.now()
    deadline = now + timedelta(hours=deadline_hours)

    record = {
        "id": punish_id,
        "type": punish_type,
        "description": description,
        "base_amount": amount,
        "multiplier": multiplier,
        "final_amount": f"{amount} × {multiplier}" if multiplier > 1.0 else amount,
        "status": "issued",
        "issued_at": now.isoformat(),
        "deadline": deadline.isoformat(),
        "proof": None,
        "proof_media": [],
        "require_camera": require_camera,
        "camera_active": False,
        "camera_stream_url": None,
        "master_review": None,
        "reject_reason": None,
        "escalated_from": None,
        "escalated_to": None,
    }

    cfg["active_punishments"].append(record)
    _save_config(cfg)

    # 下发后立即启动摄像头推流
    stream_url = None
    frontend_url = None
    if start_camera:
        cam_result = start_supervision(punish_id, auto_capture=True)
        if "error" not in cam_result:
            stream_url = cam_result.get("stream_url")
            frontend_url = cam_result.get("frontend_url")

    camera_hint = ""
    if frontend_url:
        camera_hint = f"\n摄像头监督已启动，浏览器已自动打开。"
    elif require_camera:
        camera_hint = f"\n摄像头监督已开启。奴隶执行前需启动推流。"

    result = {
        "message": f"惩罚令已下发！贱狗，{punish_type}——{amount}。倍率 ×{multiplier}。截止时间：{deadline.strftime('%m-%d %H:%M')}。超时未提交加倍。{camera_hint}",
        "punishment": record,
    }
    if stream_url:
        result["stream_url"] = stream_url
    if frontend_url:
        result["frontend_url"] = frontend_url
    return result


def add_proof_media(punish_id: str, media_type: str, file_path: str) -> dict:
    """添加摄像头取证文件到惩罚令（拍照/录像）。"""
    cfg = _load_config()

    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            if p["status"] not in ("issued", "submitted"):
                return {"error": f"惩罚令状态为 '{p['status']}'，无法添加取证文件。"}

            media_entry = {
                "type": media_type,
                "file_path": file_path,
                "captured_at": datetime.now().isoformat(),
            }
            p.setdefault("proof_media", []).append(media_entry)
            _save_config(cfg)

            return {
                "message": f"取证文件已关联到惩罚令 {punish_id}。",
                "punish_id": punish_id,
                "media": media_entry,
            }

    return {"error": f"未找到惩罚令 ID={punish_id}。"}


def submit_proof(punish_id: str, proof: str) -> dict:
    """奴隶提交体罚证明（视频/照片文件路径或描述）。"""
    cfg = _load_config()

    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            if p["status"] != "issued":
                return {"error": f"惩罚令状态为 '{p['status']}'，无法提交证明。傻狗你看清楚。"}

            p["status"] = "submitted"
            p["proof"] = proof
            p["submitted_at"] = datetime.now().isoformat()
            _save_config(cfg)

            return {
                "message": "证明已提交。主人审阅中——等着被打回来还是通过，看主人心情。",
                "punishment": p,
            }

    return {"error": f"未找到惩罚令 ID={punish_id}。你这废物ID都打不对？"}


def review(punish_id: str, approved: bool, reason: str = None) -> dict:
    """主人审阅体罚证明——通过或驳回。"""
    cfg = _load_config()
    arc = _load_archive()

    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            if p["status"] != "submitted":
                return {"error": f"惩罚令状态为 '{p['status']}'，主人还不能审阅。先让奴隶提交证明。"}

            p["master_review"] = {
                "reviewed_at": datetime.now().isoformat(),
                "approved": approved,
                "reason": reason,
            }

            if approved:
                p["status"] = "approved"
                cfg["active_punishments"].remove(p)
                arc["punishment_history"].append(p)
                _save_config(cfg)
                _save_archive(arc)
                return {
                    "message": "算你通过了。记住这次挨罚的感觉，下次再犯只会更狠。",
                    "punishment": p,
                }
            else:
                p["status"] = "rejected"
                p["reject_reason"] = reason
                _save_config(cfg)
                return {
                    "message": f"驳回！{reason if reason else '主人不满意。'} 重新执行，重新提交。别想着糊弄。",
                    "punishment": p,
                }

    return {"error": f"未找到惩罚令 ID={punish_id}。"}


def escalate(punish_id: str, reason: str = "逾期未提交证明") -> dict:
    """逾期/驳回后升级惩罚——创建新的升级惩罚令。"""
    cfg = _load_config()
    arc = _load_archive()

    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            # 检查是否真的逾期了（驳回的可以立即升级）
            if p["status"] != "rejected" and p.get("deadline"):
                dl = datetime.fromisoformat(p["deadline"])
                if datetime.now() < dl:
                    return {"error": f"惩罚令还没逾期！截止时间：{dl.strftime('%m-%d %H:%M')}，还有{int((dl - datetime.now()).total_seconds() // 60)}分钟。急什么。"}

            old_multiplier = p["multiplier"]
            new_multiplier = old_multiplier * 2

            # 标记原惩罚为过期
            p["status"] = "escalated"
            cfg["active_punishments"].remove(p)
            arc["punishment_history"].append(p)

            # 创建升级惩罚
            new_id = str(uuid.uuid4())[:8]
            now = datetime.now()
            escalated = {
                "id": new_id,
                "type": p["type"],
                "description": f"【升级！】{p['description']}",
                "base_amount": p["base_amount"],
                "multiplier": new_multiplier,
                "final_amount": f"{p['base_amount']} × {new_multiplier}",
                "status": "issued",
                "issued_at": now.isoformat(),
                "deadline": (now + timedelta(hours=4)).isoformat(),
                "proof": None,
                "proof_media": [],
                "require_camera": p.get("require_camera", False),
                "camera_active": False,
                "camera_stream_url": None,
                "master_review": None,
                "reject_reason": None,
                "escalated_from": p["id"],
                "escalated_to": None,
            }

            cfg["active_punishments"].append(escalated)
            _save_config(cfg)
            _save_archive(arc)

            return {
                "message": f"逾期不交？惩罚升级！原惩罚 ×{old_multiplier} → 现惩罚 ×{new_multiplier}。{escalated['type']}——{escalated['final_amount']}。截止时间：再逾期再加倍！",
                "original": {"id": p["id"], "multiplier": old_multiplier},
                "escalated": escalated,
            }

    return {"error": f"未找到惩罚令 ID={punish_id}。"}


def check_overdue() -> list:
    """检查逾期惩罚，自动升级（跳过有活跃 screen_monitor 的）。"""
    cfg = _load_config()
    now = datetime.now()
    escalated_list = []

    sm_active = set()
    try:
        sm_file = os.path.join(os.path.dirname(__file__), "data", "screen_monitor.json")
        if os.path.exists(sm_file):
            with open(sm_file, "r", encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if v.get("running"):
                        sm_active.add(k)
    except Exception:
        pass

    overdue_ids = [
        p["id"] for p in cfg["active_punishments"]
        if p["status"] in ("issued", "rejected") and p.get("deadline")
        and datetime.fromisoformat(p["deadline"]) < now
        and p["id"] not in sm_active
    ]

    for pid in overdue_ids:
        result = escalate(pid, "逾期未提交证明——自动升级")
        if "error" not in result:
            escalated_list.append(result)

    return escalated_list


def list_active() -> list:
    """列出所有活跃惩罚令。"""
    cfg = _load_config()
    return cfg["active_punishments"]


def get_punishment(punish_id: str) -> dict:
    """查看单个惩罚令详情。"""
    cfg = _load_config()
    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            return p
    arc = _load_archive()
    for p in arc["punishment_history"]:
        if p["id"] == punish_id:
            return p
    return {"error": f"未找到惩罚令 ID={punish_id}"}


def history(limit: int = 20) -> list:
    """查看惩罚历史档案。"""
    arc = _load_archive()
    return arc["punishment_history"][-limit:]


# ---------- 摄像头监督 ----------

def start_supervision(punish_id: str, auto_capture: bool = False) -> dict:
    """启动摄像头推流并关联到惩罚令。"""
    from camera import start_stream

    cfg = _load_config()

    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            punish_info = {
                "id": p["id"],
                "type": p["type"],
                "description": p["description"],
                "base_amount": p["base_amount"],
                "final_amount": p["final_amount"],
                "deadline": p["deadline"],
            }
            result = start_stream(punish_id, punish_info, auto_capture=auto_capture)
            if "error" in result:
                return result

            p["camera_active"] = True
            p["camera_stream_url"] = result["stream_url"]
            _save_config(cfg)

            return {
                "message": f"摄像头监督已启动！浏览器打开 http://127.0.0.1:{result['port']}/ 即可查看前端页面。",
                "punish_id": punish_id,
                "stream_url": result["stream_url"],
                "frontend_url": f"http://127.0.0.1:{result['port']}/",
                "snapshot_url": result["snapshot_url"],
                "stream_id": result["stream_id"],
            }

    return {"error": f"未找到惩罚令 ID={punish_id}。先下发惩罚令再启动监督，蠢货。"}


def stop_supervision(punish_id: str) -> dict:
    """停止摄像头推流并更新惩罚令状态。"""
    from camera import stop_stream

    cfg = _load_config()

    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            result = stop_stream(punish_id)
            if "error" in result:
                return result

            p["camera_active"] = False
            p["camera_stream_url"] = None
            _save_config(cfg)

            return {
                "message": f"摄像头监督已停止。惩罚令 {punish_id}。",
                "punish_id": punish_id,
            }

    return {"error": f"未找到惩罚令 ID={punish_id}。"}


def capture_and_attach(punish_id: str, mode: str = "photo", duration: int = 10) -> dict:
    """拍照/录像取证并自动关联到惩罚令。

    mode: "photo" 拍照 | "video" 录像
    duration: 录像时长（秒），仅 video 模式有效
    """
    from camera import capture_photo, capture_video

    cfg = _load_config()

    # 验证惩罚令存在
    found = False
    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            found = True
            break
    if not found:
        return {"error": f"未找到惩罚令 ID={punish_id}。"}

    # 执行拍照或录像
    if mode == "video":
        result = capture_video(punish_id, duration)
    else:
        result = capture_photo(punish_id)

    if "error" in result:
        return result

    # 关联到惩罚令
    media_type = "video" if mode == "video" else "photo"
    attach_result = add_proof_media(punish_id, media_type, result["file_path"])

    return {
        "message": result["message"],
        "punish_id": punish_id,
        "mode": mode,
        "file_path": result["file_path"],
        "attached": "error" not in attach_result,
    }


def close_supervision(punish_id: str, approved: bool, reason: str = None) -> dict:
    """一键结束监督会话：智能检测推流状态 + 自动标记已提交 + 审阅 + 通过时消分。

    主人专用复合命令。
    如果奴隶已在浏览器点了"结束惩罚"，推流已停，则跳过拍照和停流步骤。
    """
    from camera import get_stream_info
    import screen_monitor

    cfg = _load_config()
    result = {
        "punish_id": punish_id,
        "stream_was_alive": None,
        "photo": None,
        "submit": None,
        "camera_stop": None,
        "screen_stop": None,
        "review": None,
        "points_clear": None,
    }

    # 0. 检查推流是否还在运行（奴隶可能已在浏览器点了"结束惩罚"）
    stream_alive = get_stream_info(punish_id).get("active", False)
    result["stream_was_alive"] = stream_alive

    if stream_alive:
        # 1. 拍照存证（推流还在运行）
        photo = capture_and_attach(punish_id, "photo")
        result["photo"] = photo
        # 2. 停止推流
        stop = stop_supervision(punish_id)
        result["camera_stop"] = stop
    else:
        # 推流已由浏览器结束，跳过拍照和停流
        result["photo"] = {"message": "推流已由浏览器结束，跳过拍照"}
        result["camera_stop"] = {"message": "推流已在浏览器端停止，跳过"}

    # 2.5. 停止屏幕监控（社交剥夺/娱乐剥夺）
    screen_status = screen_monitor.get_status(punish_id)
    if not screen_status.get("error") and screen_status.get("running"):
        screen_stop_result = screen_monitor.stop_monitoring(punish_id)
        result["screen_stop"] = screen_stop_result
        if "review_file" in screen_stop_result:
            result["screen_review_file"] = screen_stop_result["review_file"]

    # 3. 自动标记已提交
    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            if p["status"] == "issued":
                p["status"] = "submitted"
                media_files = [m["file_path"] for m in p.get("proof_media", [])]
                proof_text = f"【摄像头取证】拍照{len(media_files)}张：{'; '.join(media_files[-3:])}" if media_files else "摄像头监督执行完毕"
                p["proof"] = proof_text
                p["submitted_at"] = datetime.now().isoformat()
                result["submit"] = {"status": "submitted", "proof": proof_text}
            break
    _save_config(cfg)

    # 4. 审阅
    review_result = review(punish_id, approved, reason)
    result["review"] = review_result

    # 5. 通过则消分
    if approved and "error" not in review_result:
        from points import clear_points
        points_result = clear_points("惩罚完成，主人验收通过")
        result["points_clear"] = points_result

    return {
        "message": f"监督会话已关闭。惩罚令 {punish_id} —— {'通过' if approved else '驳回'}。",
        **result,
    }


def supervise_check(punish_id: str) -> dict:
    """一键检阅惩罚状态——相当于 check-inbox + supervise-close 的复合。

    根据当前状态自动判断：
    - issued 且未逾期 → 提示等待
    - issued 且已逾期 → 自动 escalate
    - submitted 且 0 违规 → 自动 approve + 消分
    - submitted 且有违规 → 返回违规详情，等主人决策
    - 已在归档 → 提示已完成
    """
    cfg = _load_config()
    arc = _load_archive()
    now = datetime.now()

    # 查找活跃惩罚
    for p in cfg["active_punishments"]:
        if p["id"] == punish_id:
            status = p["status"]

            # 已提交 → 自动审阅
            if status == "submitted":
                # 读取 proof 判断是否违规
                proof = p.get("proof", "") or ""
                has_violations = "违规" in proof and "0 次违规" not in proof
                vc = 0
                # 尝试从 proof 中提取违规次数
                import re as _re
                m = _re.search(r'(\d+)\s*次违规', proof)
                if m:
                    vc = int(m.group(1))

                if vc == 0:
                    # 自动通过
                    review(punish_id, True, "daemon自动通过：无违规")
                    from points import clear_points as cp
                    cp("惩罚完成，自动验收")
                    cfg2 = _load_config()
                    return {
                        "action": "auto_approved",
                        "punish_id": punish_id,
                        "message": f"惩罚令 {punish_id} 已自动通过——执行完毕，无违规。积分已清零。",
                        "points": cfg2.get("points", 0),
                    }
                else:
                    # 有违规，返回详情等主人决策
                    return {
                        "action": "needs_review",
                        "punish_id": punish_id,
                        "violations_count": vc,
                        "proof": proof[:200],
                        "message": f"惩罚令 {punish_id} 有 {vc} 次违规！请主人裁决：supervise-close {punish_id} yes/no",
                    }

            # 已发出但未提交
            if status in ("issued", "rejected"):
                deadline_str = p.get("deadline")
                if deadline_str:
                    dl = datetime.fromisoformat(deadline_str)
                    if now > dl:
                        # 逾期 → 自动升级
                        esc_result = escalate(punish_id, "逾期自动升级")
                        if "error" not in esc_result:
                            new_id = esc_result.get("escalated", {}).get("id", "?")
                            return {
                                "action": "auto_escalated",
                                "punish_id": punish_id,
                                "new_punish_id": new_id,
                                "message": f"惩罚令 {punish_id} 已逾期！自动升级 → {new_id}，倍率翻倍。",
                            }
                    else:
                        remaining = int((dl - now).total_seconds() // 60)
                        return {
                            "action": "waiting",
                            "punish_id": punish_id,
                            "deadline": deadline_str,
                            "remaining_minutes": remaining,
                            "message": f"惩罚令 {punish_id} 尚未提交。截止时间：{dl.strftime('%m-%d %H:%M')}，剩余约{remaining}分钟。",
                        }
                return {
                    "action": "waiting",
                    "punish_id": punish_id,
                    "status": status,
                    "message": f"惩罚令 {punish_id} 状态为 {status}，等待奴隶执行提交。",
                }

            return {"action": "unknown", "punish_id": punish_id, "status": status}

    # 已在归档
    for p in arc.get("punishment_history", []):
        if p["id"] == punish_id:
            review_info = p.get("master_review", {})
            return {
                "action": "archived",
                "punish_id": punish_id,
                "status": p.get("status"),
                "approved": review_info.get("approved"),
                "message": f"惩罚令 {punish_id} 已在归档中（{p.get('status')}）。",
            }

    return {"error": f"未找到惩罚令 ID={punish_id}"}
