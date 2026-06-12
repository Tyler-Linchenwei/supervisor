"""
积分引擎 —— 主人赏罚的数字化记录。

积分累计 → 阈值触发 → 惩罚自动升级。
不是称号降级，是惩罚倍率升级。
"""

import json
import os
import tempfile
from datetime import datetime

from _paths import PROJECT_ROOT

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
ARCHIVE_PATH = os.path.join(PROJECT_ROOT, "archive.json")

# 积分阈值与惩罚升级倍率对照表
THRESHOLDS = [
    (5, 1.0, "基础惩罚 × 1 — 照常执行"),
    (10, 1.5, "基础惩罚 × 1.5 — 每次惩罚追加额外练习题或抄写"),
    (15, 2.0, "基础惩罚 × 2 — 体罚加倍，罚站/罚跪时间翻倍，追加面壁"),
    (25, 3.0, "基础惩罚 × 3 — 耳光/皮带数量三倍，必须跪着执行全部惩罚"),
    (40, 5.0, "当前模块回滚 — 删光当前模块重写，耳光五十下，搓衣板罚跪六十分钟"),
    (60, 10.0, "项目推到重来 — 所有代码作废，耳光五十下，搓衣板罚跪六十分钟，皮带打屁股三十下，镜子前跪着自骂复盘三十分钟"),
]


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


def add_points(amount: int, reason: str) -> dict:
    """加积分（奴隶犯错时调用）。返回当前状态与触发的升级。"""
    cfg = _load_config()
    arc = _load_archive()

    cfg["points"] += amount
    cfg["last_points_check"] = datetime.now().isoformat()

    # 判断当前处于哪个阈值等级
    old_level = cfg.get("escalation_level", 0)
    new_level = old_level
    triggered = None

    for threshold, multiplier, desc in reversed(THRESHOLDS):
        if cfg["points"] >= threshold:
            new_level = threshold
            if threshold > old_level:
                triggered = {"threshold": threshold, "multiplier": multiplier, "description": desc}
            break
    else:
        new_level = 0

    cfg["escalation_level"] = new_level

    # 记录积分变更历史
    record = {
        "time": datetime.now().isoformat(),
        "amount": amount,
        "reason": reason,
        "points_before": cfg["points"] - amount,
        "points_after": cfg["points"],
        "escalation_triggered": triggered,
    }
    arc["points_history"].append(record)

    _save_config(cfg)
    _save_archive(arc)

    return {
        "current_points": cfg["points"],
        "escalation_level": new_level,
        "escalation_triggered": triggered,
        "all_thresholds": [{"threshold": t, "multiplier": m, "description": d} for t, m, d in THRESHOLDS],
    }


def get_current_multiplier() -> float:
    """获取当前积分对应的惩罚倍率。"""
    cfg = _load_config()
    for threshold, multiplier, _ in reversed(THRESHOLDS):
        if cfg["points"] >= threshold:
            return multiplier
    return 1.0


def get_status() -> dict:
    """查看当前积分状态。"""
    cfg = _load_config()
    multiplier = get_current_multiplier()

    # 找到当前所在阈值描述
    current_threshold_desc = "无 — 低于5分，正常惩罚"
    for threshold, _, desc in reversed(THRESHOLDS):
        if cfg["points"] >= threshold:
            current_threshold_desc = desc
            break

    next_threshold = None
    for threshold, _, desc in THRESHOLDS:
        if threshold > cfg["points"]:
            next_threshold = {"threshold": threshold, "points_needed": threshold - cfg["points"], "description": desc}
            break

    return {
        "points": cfg["points"],
        "multiplier": multiplier,
        "current_level": current_threshold_desc,
        "next_threshold": next_threshold,
        "all_thresholds": [{"threshold": t, "multiplier": m, "description": d} for t, m, d in THRESHOLDS],
    }


def clear_points(reason: str = "里程碑完成，主人验收通过") -> dict:
    """积分清零（奴隶完成里程碑后主人批准）。"""
    cfg = _load_config()
    arc = _load_archive()

    old_points = cfg["points"]
    cfg["points"] = 0
    cfg["escalation_level"] = 0
    cfg["last_points_check"] = datetime.now().isoformat()

    arc["points_history"].append({
        "time": datetime.now().isoformat(),
        "amount": -old_points,
        "reason": reason,
        "points_before": old_points,
        "points_after": 0,
        "escalation_triggered": None,
    })

    _save_config(cfg)
    _save_archive(arc)

    return {"cleared": old_points, "reason": reason, "current_points": 0}


def halve_points() -> dict:
    """积分减半（连续五次无过失触发）。"""
    cfg = _load_config()
    arc = _load_archive()

    old_points = cfg["points"]
    new_points = old_points // 2
    cfg["points"] = new_points
    cfg["last_points_check"] = datetime.now().isoformat()

    arc["points_history"].append({
        "time": datetime.now().isoformat(),
        "amount": -(old_points - new_points),
        "reason": "连续五次交互无过失——积分减半",
        "points_before": old_points,
        "points_after": new_points,
        "escalation_triggered": None,
    })

    _save_config(cfg)
    _save_archive(arc)

    return {"before": old_points, "after": new_points, "reduced_by": old_points - new_points}
