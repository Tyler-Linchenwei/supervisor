"""
取证照片自动分析模块 —— 姿态检测 + 红肿趋势分析。

用法：
  from analyze import analyze_photo, analyze_punishment

  # 分析单张照片（可选传入惩罚类型）
  result = analyze_photo("path/to/photo.jpg", punish_type="罚跪")

  # 分析指定惩罚令的所有照片（自动读取惩罚类型 + 红色趋势对比）
  result = analyze_punishment("de353e8b")
"""

import json
import math
import os
import glob as _glob

import cv2
import numpy as np

from _paths import PROJECT_ROOT

# ── 模型路径 ──────────────────────────────────────────────

_MODEL_PATH = os.path.join(PROJECT_ROOT, "pose_landmarker.task")

# ── 单例 ──────────────────────────────────────────────────

_face_cascade: cv2.CascadeClassifier | None = None
_mp_detector = None  # PoseLandmarker 实例


def _get_face_cascade() -> cv2.CascadeClassifier:
    global _face_cascade
    if _face_cascade is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(cascade_path)
    return _face_cascade


def _get_pose_detector():
    """惰性加载 MediaPipe PoseLandmarker（Tasks API v0.10.x）。"""
    global _mp_detector
    if _mp_detector is None:
        try:
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python import BaseOptions

            if not os.path.exists(_MODEL_PATH):
                return None
            options = vision.PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=_MODEL_PATH),
                running_mode=vision.RunningMode.IMAGE,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            _mp_detector = vision.PoseLandmarker.create_from_options(options)
        except ImportError:
            return None
    return _mp_detector


# ── 姿态检测 ──────────────────────────────────────────────

def detect_posture(image_bgr, punish_type: str | None = None) -> dict:
    """使用 MediaPipe Pose 检测身体姿态。

    返回:
        {
            "posture_detected": bool,
            "posture": "kneeling" | "standing" | "sitting" | "upper_body_only" | "unknown",
            "posture_confidence": 0.0 ~ 1.0,
            "knee_angle_avg": float (度),
            "body_upright": bool,
            "knees_below_hips": bool,
            "lower_body_visible": bool,
            "posture_details": [str, ...],
            "posture_verdict": str,
        }
    """
    h, w = image_bgr.shape[:2]
    result = {
        "posture_detected": False,
        "posture": "unknown",
        "posture_confidence": 0.0,
        "knee_angle_avg": None,
        "body_upright": False,
        "knees_below_hips": False,
        "lower_body_visible": False,
        "posture_details": [],
        "posture_verdict": "",
    }

    detector = _get_pose_detector()
    if detector is None:
        result["posture_verdict"] = "MediaPipe 姿态模型未就绪——请确认 pose_landmarker.task 已下载"
        return result

    import mediapipe as mp
    img_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
    detect_result = detector.detect(mp_image)

    if not detect_result.pose_landmarks:
        result["posture_verdict"] = "未检测到身体姿态——镜头角度或距离可能有问题"
        return result

    result["posture_detected"] = True
    lm = detect_result.pose_landmarks[0]  # 取第一组姿态（num_poses=1）

    def pt(idx):
        return lm[idx]

    # 关键骨骼点
    left_shoulder = pt(11)
    right_shoulder = pt(12)
    left_hip = pt(23)
    right_hip = pt(24)
    left_knee = pt(25)
    right_knee = pt(26)
    left_ankle = pt(27)
    right_ankle = pt(28)

    # 可见度
    knee_vis = (left_knee.visibility + right_knee.visibility) / 2
    ankle_vis = (left_ankle.visibility + right_ankle.visibility) / 2
    result["lower_body_visible"] = (knee_vis > 0.5 and ankle_vis > 0.5)

    # 关键点 y 坐标（图像中 y 轴向下）
    mid_hip_y = (left_hip.y + right_hip.y) / 2
    mid_knee_y = (left_knee.y + right_knee.y) / 2
    mid_ankle_y = (left_ankle.y + right_ankle.y) / 2
    mid_shoulder_y = (left_shoulder.y + right_shoulder.y) / 2

    # 身体直立程度
    dx = abs((left_shoulder.x + right_shoulder.x) / 2 - (left_hip.x + right_hip.x) / 2)
    dy = abs(mid_shoulder_y - mid_hip_y)
    result["body_upright"] = dy > dx * 3 if dx > 0 else True

    # 膝关节角度（髋-膝-踝）
    def knee_angle(hip, knee, ankle):
        ba = (hip.x - knee.x, hip.y - knee.y)
        bc = (ankle.x - knee.x, ankle.y - knee.y)
        dot = ba[0] * bc[0] + ba[1] * bc[1]
        mag_ba = math.sqrt(ba[0] ** 2 + ba[1] ** 2)
        mag_bc = math.sqrt(bc[0] ** 2 + bc[1] ** 2)
        if mag_ba * mag_bc < 0.001:
            return 180.0
        cos_angle = max(-1.0, min(1.0, dot / (mag_ba * mag_bc)))
        return math.degrees(math.acos(cos_angle))

    left_angle = knee_angle(left_hip, left_knee, left_ankle)
    right_angle = knee_angle(right_hip, right_knee, right_ankle)
    avg_angle = (left_angle + right_angle) / 2
    result["knee_angle_avg"] = round(avg_angle, 1)

    # 膝盖是否低于髋部
    result["knees_below_hips"] = mid_knee_y > mid_hip_y

    # 膝盖是否接近地面水平（靠近踝关节）
    knees_near_ankles = abs(mid_knee_y - mid_ankle_y) < 0.15
    knee_deeply_bent = avg_angle < 130

    if not result["lower_body_visible"]:
        result["posture"] = "upper_body_only"
        result["posture_confidence"] = 0.3
        if result["body_upright"]:
            result["posture_details"].append("上半身保持直立")
        else:
            result["posture_details"].append("仅检测到上半身——无法判断下半身姿态")
    elif result["knees_below_hips"] and knee_deeply_bent:
        if avg_angle < 100 and knees_near_ankles:
            result["posture"] = "kneeling"
            result["posture_confidence"] = 0.85
            result["posture_details"].append(f"膝盖弯曲 {avg_angle:.0f}°，膝盖贴近踝部——跪姿特征明显")
        elif avg_angle < 120:
            result["posture"] = "likely_kneeling"
            result["posture_confidence"] = 0.65
            result["posture_details"].append(f"膝盖弯曲 {avg_angle:.0f}°，膝盖低于髋部——疑似跪姿")
        else:
            result["posture"] = "possibly_kneeling"
            result["posture_confidence"] = 0.45
            result["posture_details"].append(f"膝盖微曲 {avg_angle:.0f}°——可能为跪姿")
    elif avg_angle > 160 and result["body_upright"]:
        result["posture"] = "standing"
        result["posture_confidence"] = 0.85
        result["posture_details"].append(f"膝盖伸直 {avg_angle:.0f}°，身体直立——站姿")
    elif avg_angle > 150:
        result["posture"] = "likely_standing"
        result["posture_confidence"] = 0.7
        result["posture_details"].append(f"膝盖基本伸直 {avg_angle:.0f}°——疑似站姿")
    else:
        result["posture"] = "sitting"
        result["posture_confidence"] = 0.6
        result["posture_details"].append(f"膝盖弯曲 {avg_angle:.0f}°——坐姿")

    # 惩罚类型 vs 检测姿态 对照判决
    verdict_map = {
        "罚跪": {
            "kneeling": "✅ 跪姿确认——奴隶在镜头前跪着执行罚跪",
            "likely_kneeling": "🟡 疑似跪姿——膝盖弯曲但不够确定，继续监督",
            "possibly_kneeling": "🟡 可能跪姿——幅度偏浅，是偷懒还是角度问题？",
            "standing": "❌ 检测到站姿——罚跪令下奴隶站在镜头前，明显未执行！",
            "sitting": "❌ 检测到坐姿——罚跪令下奴隶坐着，在偷懒！",
            "upper_body_only": "⚠ 仅见上半身——无法确认是否跪着，调整镜头照全身",
        },
        "罚站": {
            "standing": "✅ 站姿确认——奴隶在镜头前站着执行罚站",
            "likely_standing": "🟡 疑似站姿——身体基本直立",
            "sitting": "❌ 检测到坐姿——罚站令下奴隶坐着，在偷懒！",
            "kneeling": "❌ 检测到跪姿——罚站令下奴隶跪着，姿势不对！",
            "upper_body_only": "🟡 仅见上半身——无法确认是否站着，调整镜头",
        },
        "面壁": {
            "standing": "✅ 身体直立——符合面壁姿势",
            "upper_body_only": "🟡 仅见上半身——面壁时人脸可能不可见，正常",
        },
        "戒尺": {
            "kneeling": "🟡 跪姿——挨戒尺时跪姿常见，看红色趋势判断是否真打",
            "standing": "✅ 站姿——等待红色趋势验证",
        },
        "皮带": {
            "kneeling": "🟡 跪姿——挨皮带时跪姿常见，看红色趋势判断是否真打",
            "standing": "✅ 站姿——等待红色趋势验证",
        },
    }

    if punish_type and punish_type in verdict_map:
        mapped = verdict_map[punish_type].get(result["posture"], "")
        if mapped:
            result["posture_verdict"] = mapped
    if not result["posture_verdict"]:
        result["posture_verdict"] = "; ".join(result["posture_details"]) if result["posture_details"] else f"姿态: {result['posture']}"

    return result


# ── 照片分析 ──────────────────────────────────────────────

def analyze_photo(filepath: str, punish_type: str | None = None) -> dict:
    """分析单张取证照片。

    参数:
        filepath: 照片文件路径
        punish_type: 惩罚类型（可选），用于姿态对照判决

    返回:
        {
            "filepath": str,
            "filename": str,
            "resolution": {"width": int, "height": int},
            "file_size_kb": int,
            "brightness_avg": float,
            "face_count": int,
            "slave_present": bool,
            "red_tone_ratio": float,
            "skin_tone_ratio": float,
            "avg_color_bgr": [int,int,int],
            "posture": {...},
            "verdict": str,
        }
    """
    if not os.path.exists(filepath):
        return {"error": f"文件不存在: {filepath}"}

    img = cv2.imread(filepath)
    if img is None:
        return {"error": f"无法读取图片: {filepath}"}

    h, w = img.shape[:2]
    size_kb = os.path.getsize(filepath) // 1024
    filename = os.path.basename(filepath)

    # 亮度
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    brightness_avg = float(gray.mean())

    # 面部检测
    face_cascade = _get_face_cascade()
    faces = face_cascade.detectMultiScale(gray, 1.1, 5)
    face_count = len(faces)
    slave_present = face_count > 0

    # 红色调（优先检测人脸区域的红肿——避免手遮挡/背景干扰）
    # 多张人脸时取最红的那张脸（可能只有一边被打红）
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    red_mask1 = cv2.inRange(hsv, (0, 50, 50), (10, 255, 255))
    red_mask2 = cv2.inRange(hsv, (160, 50, 50), (180, 255, 255))
    red_mask_whole = cv2.bitwise_or(red_mask1, red_mask2)

    if face_count > 0:
        face_red_ratios = []
        for (fx, fy, fw, fh) in faces:
            face_red = red_mask_whole[fy:fy+fh, fx:fx+fw]
            face_area = fw * fh
            if face_area > 0:
                face_red_ratios.append(float(face_red.sum() / 255 / face_area * 100))
        red_tone_ratio = round(max(face_red_ratios), 2) if face_red_ratios else 0.0
        red_source = "face" if face_count == 1 else f"max_of_{face_count}_faces"
    else:
        # 未检测到人脸——可能被手挡住（正在扇耳光），回退为整图检测
        red_pixels = red_mask_whole.sum() // 255
        red_tone_ratio = float(red_pixels / (h * w) * 100)
        red_source = "whole_image_no_face"

    # 肤色区域
    skin_mask = cv2.inRange(hsv, (0, 20, 70), (20, 170, 255))
    skin_pixels = skin_mask.sum() // 255
    skin_tone_ratio = float(skin_pixels / (h * w) * 100)

    # 平均颜色
    avg_color = img.mean(axis=0).mean(axis=0)
    avg_color_bgr = [int(avg_color[0]), int(avg_color[1]), int(avg_color[2])]

    # 姿态检测
    posture_result = detect_posture(img, punish_type)

    # 综合判断（单张红色调用极值告警；趋势对比在 analyze_punishment 中做）
    verdict_parts = []
    if not slave_present:
        verdict_parts.append("⚠ 未检测到人脸——奴隶可能不在镜头前")
    else:
        verdict_parts.append(f"检测到 {face_count} 张人脸")

    if red_tone_ratio > 12:
        verdict_parts.append("🔴 红色调极高——可能有严重红肿")
    elif red_tone_ratio > 8:
        verdict_parts.append("🟡 红色调偏高——注意观察趋势变化")
    else:
        verdict_parts.append("🟢 红色调正常")

    if brightness_avg < 50:
        verdict_parts.append("⚠ 画面过暗")
    elif brightness_avg > 220:
        verdict_parts.append("⚠ 画面过亮/过曝")

    if posture_result.get("posture_verdict"):
        verdict_parts.append(posture_result["posture_verdict"])

    verdict = "；".join(verdict_parts)

    return {
        "filepath": filepath,
        "filename": filename,
        "resolution": {"width": w, "height": h},
        "file_size_kb": size_kb,
        "brightness_avg": round(brightness_avg, 1),
        "face_count": face_count,
        "slave_present": slave_present,
        "red_tone_ratio": round(red_tone_ratio, 2),
        "red_source": red_source,  # "face" | "max_of_N_faces" | "whole_image_no_face"
        "skin_tone_ratio": round(skin_tone_ratio, 2),
        "avg_color_bgr": avg_color_bgr,
        "posture": posture_result,
        "verdict": verdict,
    }


# ── 惩罚令综合分析 ─────────────────────────────────────────

def _get_punishment_type(punish_id: str) -> str | None:
    """从 config.json 读取惩罚类型（同时检索活跃和归档记录）。"""
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    archive_path = os.path.join(PROJECT_ROOT, "archive.json")
    for path in (config_path, archive_path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            records = cfg.get("active_punishments", []) + cfg.get("punishment_history", [])
            for p in records:
                if p["id"] == punish_id:
                    return p.get("type")
        except Exception as e:
            import sys
            print(f"[analyze] 警告：读取 {os.path.basename(path)} 失败 ({e})，跳过", file=sys.stderr)
    return None


def _get_punishment_amount(punish_id: str) -> int | None:
    """从配置中读取惩罚要求数量（正确处理翻倍逻辑）。"""
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    archive_path = os.path.join(PROJECT_ROOT, "archive.json")
    for path in (config_path, archive_path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            records = cfg.get("active_punishments", []) + cfg.get("punishment_history", [])
            for p in records:
                if p["id"] == punish_id:
                    amount_str = p.get("base_amount", "")
                    multiplier = float(p.get("multiplier", 1.0))
                    import re as _re
                    nums = _re.findall(r"\d+", amount_str)
                    if nums:
                        return int(int(nums[0]) * multiplier)
                    # 中文数字映射
                    cn_num_map = {
                        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                        "二十": 20, "三十": 30, "四十": 40, "五十": 50,
                        "六十": 60, "七十": 70, "八十": 80, "九十": 90,
                        "百": 100,
                    }
                    for cn, val in sorted(cn_num_map.items(), key=lambda x: -len(x[0])):
                        if cn in amount_str:
                            return int(val * multiplier)
                    return None
        except Exception:
            pass
    return None


def _get_strike_data(punish_id: str) -> dict | None:
    """读取惩罚关停时保存的击打计数数据。"""
    review_path = os.path.join(PROJECT_ROOT, "data", f"review_ready_{punish_id}.json")
    try:
        if os.path.exists(review_path):
            with open(review_path, "r", encoding="utf-8") as f:
                review = json.load(f)
            return review.get("strike_data")
    except Exception:
        pass
    return None


def analyze_punishment(punish_id: str) -> dict:
    """分析指定惩罚令的所有取证照片（含红色趋势对比 + 姿态检测）。

    红色调分析：以第一张照片为基准，后续照片红色调递增才算真实体罚痕迹。

    参数:
        punish_id: 惩罚令 ID

    返回:
        {
            "punish_id": str,
            "punish_type": str | None,
            "total_photos": int,
            "photos": [analyze_photo(), ...],
            "red_trend": {"first": float, "last": float, "increase": float, "verdict": str},
            "posture_summary": str,
            "summary": str,
        }
    """
    punish_type = _get_punishment_type(punish_id)

    # 屏幕监控类型（社交剥夺/娱乐剥夺）：读 JSON 报告，跳过 CV 分析
    if punish_type in ("社交剥夺", "娱乐剥夺"):
        review_path = os.path.join(PROJECT_ROOT, "data", f"review_ready_screen_{punish_id}.json")
        try:
            with open(review_path, "r", encoding="utf-8") as f:
                screen_data = json.load(f)
            violation_cnt = screen_data.get("violations_count", 0)
            duration = screen_data.get("duration_seconds", 0)
            verdict = "❌ 违规！" if violation_cnt > 0 else "✅ 合格。"
            return {
                "punish_id": punish_id,
                "punish_type": punish_type,
                "total_photos": screen_data.get("screenshot_count", 0),
                "photos": [],
                "red_trend": {"first": None, "last": None, "increase": None, "verdict": "屏幕监控不适用红色调分析"},
                "posture_summary": "屏幕监控不适用姿态检测",
                "strike_data": None,
                "strike_summary": "屏幕监控不适用击打检测",
                "summary": f"🖥 屏幕监控报告：执行 {duration} 秒，发现 {violation_cnt} 次违规。{verdict}",
                "screen_data": screen_data,
            }
        except Exception:
            return {"error": "未找到屏幕监控结算报告，监督可能仍在进行中。"}

    capture_dir = os.path.join(PROJECT_ROOT, "data", "proofs")
    pattern = os.path.join(capture_dir, f"{punish_id}_*.jpg")
    files = sorted(_glob.glob(pattern))

    if not files:
        return {
            "punish_id": punish_id,
            "punish_type": punish_type,
            "total_photos": 0,
            "photos": [],
            "red_trend": {"first": None, "last": None, "increase": None, "verdict": "无照片可分析"},
            "posture_summary": "未找到任何取证照片。奴隶还没拍照？",
            "strike_data": None,
            "strike_summary": "无击打数据",
            "summary": "未找到任何取证照片。奴隶还没拍照？",
        }

    # 逐张分析（传入惩罚类型）
    photos = []
    all_have_face = True
    red_values = []
    posture_results = []

    for fpath in files:
        result = analyze_photo(fpath, punish_type=punish_type)
        photos.append(result)
        if not result.get("slave_present", False):
            all_have_face = False
        red_values.append(result.get("red_tone_ratio", 0))
        posture_results.append(result.get("posture", {}))

    # ── 红色调趋势对比（首张基准 → 末张对比） ──
    first_red = red_values[0]
    last_red = red_values[-1]
    red_increase = last_red - first_red
    max_red = max(red_values)
    min_red = min(red_values)

    red_trend = {
        "first": round(first_red, 2),
        "last": round(last_red, 2),
        "increase": round(red_increase, 2),
        "max": round(max_red, 2),
        "min": round(min_red, 2),
        "total_photos": len(files),
    }

    if len(files) >= 2:
        if red_increase > 3:
            red_trend["verdict"] = (
                f"🔴 红色调持续升高（{first_red:.1f}% → {last_red:.1f}%，+{red_increase:.1f}%），"
                f"有真实体罚痕迹——脸被打红了"
            )
        elif red_increase > 1:
            red_trend["verdict"] = (
                f"🟡 红色调轻微上升（{first_red:.1f}% → {last_red:.1f}%，+{red_increase:.1f}%），"
                f"可能有轻度红肿"
            )
        elif red_increase < -2:
            red_trend["verdict"] = (
                f"🟢 红色调不升反降（{first_red:.1f}% → {last_red:.1f}%，{red_increase:.1f}%），"
                f"无红肿迹象——可能没真打"
            )
        else:
            red_trend["verdict"] = (
                f"🟢 红色调基本持平（{first_red:.1f}% → {last_red:.1f}%），无明显变化——无法确认是否真打"
            )
    else:
        if first_red > 8:
            red_trend["verdict"] = f"🟡 单张照片红色调偏高（{first_red:.1f}%）——无法做趋势对比，建议多拍几张"
        else:
            red_trend["verdict"] = f"🟢 单张照片红色调正常（{first_red:.1f}%）——无法做趋势对比，建议多拍几张做基准"

    # ── 姿态汇总 ──
    posture_types = [p.get("posture", "unknown") for p in posture_results]
    kneeling_count = sum(1 for pt in posture_types if pt in ("kneeling", "likely_kneeling"))
    standing_count = sum(1 for pt in posture_types if pt in ("standing", "likely_standing"))

    posture_summary_parts = []
    if punish_type == "罚跪":
        if kneeling_count >= len(files) * 0.6:
            posture_summary_parts.append(f"✅ {kneeling_count}/{len(files)} 张检测到跪姿——奴隶全程跪着")
        elif kneeling_count > 0:
            posture_summary_parts.append(f"🟡 {kneeling_count}/{len(files)} 张检测到跪姿——部分时间在跪")
        else:
            posture_summary_parts.append(f"❌ 未检测到跪姿——罚跪令可能未被认真执行")
    elif punish_type == "罚站":
        if standing_count >= len(files) * 0.6:
            posture_summary_parts.append(f"✅ {standing_count}/{len(files)} 张检测到站姿——奴隶全程站着")
        elif standing_count > 0:
            posture_summary_parts.append(f"🟡 {standing_count}/{len(files)} 张检测到站姿——部分时间在站")
        else:
            posture_summary_parts.append(f"❌ 未检测到站姿——罚站令可能未被认真执行")
    else:
        if kneeling_count > 0:
            posture_summary_parts.append(f"{kneeling_count}/{len(files)} 张检测到跪姿")
        if standing_count > 0:
            posture_summary_parts.append(f"{standing_count}/{len(files)} 张检测到站姿")

    posture_summary = "；".join(posture_summary_parts) if posture_summary_parts else "姿态检测无结论"

    # ── 综合摘要 ──
    summary_parts = [f"共 {len(files)} 张照片"]

    if all_have_face:
        summary_parts.append("每张都检测到人脸——奴隶全程在镜头前")
    else:
        missing = sum(1 for p in photos if not p.get("slave_present", False))
        summary_parts.append(f"⚠ {missing}/{len(files)} 张未检测到人脸")

    summary_parts.append(red_trend["verdict"])

    if posture_summary:
        summary_parts.append(posture_summary)

    # ── 击打计数 ──
    strike_data = _get_strike_data(punish_id)
    strike_summary = ""
    if strike_data:
        visual_count = strike_data.get("visual", {}).get("total", 0)
        audio_count = strike_data.get("audio", {}).get("total", 0)
        combined = max(visual_count, audio_count)

        if combined > 0:
            strike_parts = []
            if visual_count > 0:
                strike_parts.append(f"视觉检测 {visual_count} 次击打")
            if audio_count > 0:
                strike_parts.append(f"音频检测 {audio_count} 次击打声")
            strike_summary = "；".join(strike_parts)
            summary_parts.append(f"👊 击打计数：{strike_summary}")
        else:
            strike_summary = "未检测到击打动作/声音"
            summary_parts.append("⚠ 击打计数为 0——可能未真打或动作幅度太小")

        # 惩罚数量匹配检查
        punish_amount = _get_punishment_amount(punish_id)
        if punish_amount and combined > 0:
            if combined >= punish_amount * 0.7:
                summary_parts.append(f"✅ 击打次数 ({combined}) 接近要求 ({punish_amount})——真实性高")
            else:
                summary_parts.append(f"⚠ 击打次数 ({combined}) 远低于要求 ({punish_amount})——可能不够数")
    else:
        strike_summary = "无击打数据（推流可能尚未结束或未启用检测）"

    summary = "；".join(summary_parts)

    return {
        "punish_id": punish_id,
        "punish_type": punish_type,
        "total_photos": len(files),
        "photos": photos,
        "red_trend": red_trend,
        "posture_summary": posture_summary,
        "strike_data": strike_data,
        "strike_summary": strike_summary,
        "summary": summary,
    }


def analyze_latest(punish_id: str) -> dict:
    """分析指定惩罚令最新的一张取证照片。"""
    capture_dir = os.path.join(PROJECT_ROOT, "data", "proofs")
    pattern = os.path.join(capture_dir, f"{punish_id}_*.jpg")
    files = sorted(_glob.glob(pattern))

    if not files:
        return {"error": f"未找到惩罚令 {punish_id} 的任何取证照片"}

    punish_type = _get_punishment_type(punish_id)
    return analyze_photo(files[-1], punish_type=punish_type)
