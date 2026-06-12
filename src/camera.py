"""
摄像头监督模块 —— 主人实时观看奴隶执行惩罚。

功能：
- 启动 MJPEG 推流（主人浏览器打开 http://127.0.0.1:端口 即可观看）
- 拍照取证（单帧截取存为 JPEG）
- 录像取证（录制指定时长存为 MP4）
- 停止推流

架构：
  CameraManager（单例） → 管理多路推流
  CameraStream → 一路摄像头推流（后台线程采集 + HTTP MJPEG 服务）
"""

import json
import os
import tempfile
import re as _re
import threading
import time
import uuid
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional

import cv2
import numpy as np

# 音频检测可选依赖
try:
    import sounddevice as _sd
    _HAS_AUDIO = True
except ImportError:
    _sd = None
    _HAS_AUDIO = False

import sys as _sys
from _paths import PROJECT_ROOT

# ---------- 配置 ----------

CAPTURE_DIR = os.path.join(PROJECT_ROOT, "data", "proofs")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "camera_config.json")
STREAMS_FILE = os.path.join(PROJECT_ROOT, "data", "streams.json")

DEFAULT_CONFIG = {
    "camera_index": 0,
    "capture_dir": CAPTURE_DIR,
    "stream_port_start": 8900,
    "stream_quality": 30,          # JPEG 质量 0-100
    "stream_fps": 15,              # 推流帧率
    "capture_width": 640,
    "capture_height": 480,
}

_stream_lock = threading.Lock()
_active_streams: dict[str, "CameraStream"] = {}   # punish_id → CameraStream


def _load_config() -> dict:
    """加载摄像头配置，不存在则创建默认。"""
    if not os.path.exists(CONFIG_PATH):
        _save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 合并不在文件中的默认键
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def _save_config(cfg: dict) -> None:
    dir_name = os.path.dirname(CONFIG_PATH)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


def _ensure_capture_dir() -> str:
    """确保证据目录存在，返回路径。"""
    cfg = _load_config()
    d = cfg["capture_dir"]
    os.makedirs(d, exist_ok=True)
    return d


# ---------- 跨进程推流状态持久化 ----------

def _load_stream_state() -> dict[str, dict]:
    """从文件加载推流状态（跨进程共享）。"""
    if not os.path.exists(STREAMS_FILE):
        return {}
    with open(STREAMS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_stream_state(state: dict[str, dict]) -> None:
    """保存推流状态到文件（原子写入防并发损坏）。"""
    dir_name = os.path.dirname(STREAMS_FILE)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STREAMS_FILE)
    except Exception:
        os.unlink(tmp_path)
        raise


def _add_stream_to_state(punish_id: str, port: int, stream_id: str, stream_url: str, snapshot_url: str) -> None:
    """注册推流到持久化状态文件。"""
    state = _load_stream_state()
    state[punish_id] = {
        "punish_id": punish_id,
        "port": port,
        "stream_id": stream_id,
        "stream_url": stream_url,
        "snapshot_url": snapshot_url,
        "started_at": datetime.now().isoformat(),
    }
    _save_stream_state(state)


def _remove_stream_from_state(punish_id: str) -> None:
    """从持久化状态文件中移除推流。"""
    state = _load_stream_state()
    state.pop(punish_id, None)
    _save_stream_state(state)


# ---------- 前端 HTML 模板 ----------

def _build_frontend_html(
    punish_id: str = "",
    punish_type: str = "",
    description: str = "",
    amount: str = "",
    deadline: str = "",
) -> str:
    """生成监督前端 HTML 页面（自包含，无外部依赖）。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>主人AI监督系统 — 惩罚执行中</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    background: #0a0a0a;
    color: #ccc;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
}}
.header {{
    width: 100%;
    max-width: 960px;
    padding: 24px 20px 16px;
    text-align: center;
    border-bottom: 1px solid #2a1010;
    margin-bottom: 16px;
}}
.header h1 {{
    font-size: 22px;
    color: #e03030;
    letter-spacing: 2px;
    margin-bottom: 12px;
}}
.header .meta {{
    display: flex;
    justify-content: center;
    gap: 32px;
    flex-wrap: wrap;
    font-size: 14px;
    color: #999;
}}
.header .meta span {{ color: #e03030; font-weight: bold; }}
.header .desc {{
    margin-top: 12px;
    font-size: 15px;
    color: #ddd;
    background: #1a0a0a;
    border: 1px solid #3a1515;
    border-radius: 6px;
    padding: 10px 16px;
}}
.main-layout {{
    width: 100%;
    max-width: 1200px;
    display: flex;
    gap: 16px;
    padding: 0 16px;
    flex-wrap: wrap;
}}
.stream-col {{
    flex: 1 1 640px;
    min-width: 320px;
}}
.log-col {{
    flex: 0 0 260px;
    background: #0d0d0d;
    border: 1px solid #2a1010;
    border-radius: 8px;
    padding: 12px;
    max-height: 500px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
}}
.log-col h3 {{
    font-size: 14px;
    color: #e03030;
    margin-bottom: 12px;
    letter-spacing: 1px;
    text-align: center;
}}
.log-col .log-entry {{
    font-size: 12px;
    color: #888;
    padding: 6px 8px;
    border-bottom: 1px solid #1a1a1a;
    display: flex;
    align-items: center;
    gap: 8px;
    animation: fadeIn 0.5s;
}}
@keyframes fadeIn {{
    from {{ opacity: 0; transform: translateY(-8px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}
.log-col .log-entry .dot {{
    width: 8px;
    height: 8px;
    background: #e03030;
    border-radius: 50%;
    flex-shrink: 0;
}}
.log-col .log-empty {{
    font-size: 12px;
    color: #555;
    text-align: center;
    padding: 20px 0;
}}
.stream-container {{
    width: 100%;
    background: #000;
    border: 2px solid #3a1515;
    border-radius: 8px;
    overflow: hidden;
    position: relative;
}}
.stream-container img {{
    width: 100%;
    display: block;
}}
.stream-container .status-badge {{
    position: absolute;
    top: 12px;
    right: 12px;
    background: #e03030;
    color: #fff;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 4px;
    animation: pulse 2s infinite;
}}
@keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
}}
.controls {{
    width: 100%;
    max-width: 960px;
    display: flex;
    gap: 16px;
    padding: 20px;
    justify-content: center;
}}
.btn {{
    border: none;
    border-radius: 8px;
    padding: 14px 40px;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
    letter-spacing: 1px;
    transition: all 0.2s;
}}
.btn:active {{ transform: scale(0.96); }}
.btn-photo {{
    background: #1a1a2e;
    color: #e03030;
    border: 1px solid #e03030;
}}
.btn-photo:hover {{ background: #2a1a1a; }}
.btn-stop {{
    background: #c02020;
    color: #fff;
    border: 1px solid #e03030;
}}
.btn-stop:hover {{ background: #e03030; }}
.btn:disabled {{
    opacity: 0.4;
    cursor: not-allowed;
    pointer-events: none;
}}
.strike-counter {{
    width: 100%;
    max-width: 960px;
    display: flex;
    gap: 12px;
    padding: 12px 20px;
    justify-content: center;
    align-items: center;
    font-size: 15px;
    color: #aaa;
}}
.strike-counter .counter-icon {{ font-size: 20px; }}
.strike-counter .counter-label {{ color: #888; }}
.strike-counter .counter-value {{
    color: #ff4444;
    font-weight: bold;
    font-size: 22px;
    min-width: 28px;
    text-align: center;
    transition: all 0.3s;
}}
.strike-counter .counter-value.pulse {{
    transform: scale(1.5);
    color: #fff;
}}
.strike-counter .counter-divider {{ color: #333; font-size: 18px; }}
.strike-counter .counter-icon-small {{ font-size: 14px; opacity: 0.7; }}
.toast {{
    position: fixed;
    bottom: 40px;
    left: 50%;
    transform: translateX(-50%);
    background: #1a1a1a;
    border: 1px solid #3a1515;
    color: #e03030;
    padding: 12px 24px;
    border-radius: 8px;
    font-size: 14px;
    opacity: 0;
    transition: opacity 0.3s;
    pointer-events: none;
    z-index: 100;
}}
.toast.show {{ opacity: 1; }}
.done-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.85);
    z-index: 200;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}}
.done-overlay.show {{ display: flex; }}
.done-overlay h2 {{
    font-size: 28px;
    color: #e03030;
    margin-bottom: 8px;
}}
.done-overlay p {{
    font-size: 16px;
    color: #999;
}}
</style>
</head>
<body>

<div class="header">
    <h1>⚡ 主人AI监督系统 — 惩罚执行中</h1>
    <div class="meta">
        <div>惩罚令: <span>{punish_id}</span></div>
        <div>类型: <span>{punish_type}</span></div>
        <div>数量: <span>{amount}</span></div>
        <div>截止: <span>{deadline[:16] if deadline else "无"}</span></div>
    </div>
    <div class="desc">{description}</div>
</div>

<div class="main-layout">
    <div class="stream-col">
        <div class="stream-container" id="streamBox">
            <img src="/stream" alt="实时摄像头" id="streamImg">
            <div class="status-badge" id="liveBadge">● LIVE</div>
        </div>
    </div>
    <div class="log-col" id="logCol">
        <h3>📸 主人随机取证记录</h3>
        <div class="log-empty" id="logEmpty">等待主人取证…</div>
        <div id="logEntries"></div>
    </div>
</div>

<div class="controls">
    <button class="btn btn-photo" onclick="takePhoto(this)">📷 拍照取证</button>
    <button class="btn btn-stop" onclick="endPunishment(this)">⏹ 结束惩罚</button>
</div>

<div class="strike-counter" id="strikeCounter">
    <span class="counter-icon">🫱</span>
    <span class="counter-label">击打检测：</span>
    <span class="counter-value" id="visualCount">0</span>
    <span class="counter-divider">|</span>
    <span class="counter-icon-small">🎤</span>
    <span class="counter-value" id="audioCount">0</span>
</div>

<div class="toast" id="toast"></div>

<div class="done-overlay" id="doneOverlay">
    <h2>惩罚执行完毕</h2>
    <p id="doneDetail">证明已存档，等待主人审阅。</p>
</div>

<script>
var knownPhotos = 0;
var pollTimer = null;
var autoCaptureTimer = null;
var strikeTimer = null;
var streamStartTime = Date.now();
var MIN_EXECUTION_SECONDS = 60;  // 最少执行60秒才能结束

function toast(msg) {{
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(function() {{ t.classList.remove("show"); }}, 2500);
}}

function formatTime(seconds) {{
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return m + "分" + s + "秒";
}}

function takePhoto(btn) {{
    btn.disabled = true;
    btn.textContent = "拍照中…";
    fetch("/api/photo")
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{
            if (d.file_path) {{
                toast("📸 拍照取证成功！");
                pollPhotos();  // 立即刷新列表
            }} else {{
                toast("❌ 拍照失败");
            }}
            btn.disabled = false;
            btn.textContent = "📷 拍照取证";
        }})
        .catch(function() {{
            toast("❌ 拍照失败");
            btn.disabled = false;
            btn.textContent = "📷 拍照取证";
        }});
}}

function pollPhotos() {{
    fetch("/api/photos")
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
            if (data.photos && data.photos.length > knownPhotos) {{
                var empty = document.getElementById("logEmpty");
                if (empty) empty.style.display = "none";
                var entries = document.getElementById("logEntries");
                for (var i = knownPhotos; i < data.photos.length; i++) {{
                    var p = data.photos[i];
                    var div = document.createElement("div");
                    div.className = "log-entry";
                    var time = p.captured_at ? p.captured_at.slice(11, 19) : "";
                    div.innerHTML = '<div class="dot"></div>' + time + ' — ' + p.filename;
                    entries.appendChild(div);
                    if (i === data.photos.length - 1) {{
                        toast("⚡ 取证记录已更新！共" + data.photos.length + "张");
                    }}
                }}
                knownPhotos = data.photos.length;
                document.getElementById("logCol").scrollTop = document.getElementById("logCol").scrollHeight;
            }}
        }});
}}

function endPunishment(btn) {{
    var elapsed = Math.floor((Date.now() - streamStartTime) / 1000);
    if (elapsed < MIN_EXECUTION_SECONDS) {{
        var remaining = MIN_EXECUTION_SECONDS - elapsed;
        toast("⛔ 执行时间不足！已执行" + formatTime(elapsed) + "，至少需要" + formatTime(MIN_EXECUTION_SECONDS) + "，还剩" + formatTime(remaining) + "。主人正在监督，别想糊弄。");
        return;
    }}
    if (!confirm("确定结束惩罚？\\n\\n已执行 " + formatTime(elapsed) + "。\\n系统将自动拍照、分析、提交，然后通知主人审阅。")) return;

    btn.disabled = true;
    btn.textContent = "拍照分析中…";
    if (pollTimer) clearInterval(pollTimer);
    if (autoCaptureTimer) clearInterval(autoCaptureTimer);
    if (strikeTimer) clearInterval(strikeTimer);

    fetch("/api/stop")
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{
            if (d.done) {{
                document.getElementById("streamBox").style.display = "none";
                document.getElementById("liveBadge").style.display = "none";

                var overlay = document.getElementById("doneOverlay");
                var detail = document.getElementById("doneDetail");
                var html = "✅ 分析已完成，证明已自动提交。<br><br>";
                html += "⏱ 执行时长：" + formatTime(elapsed) + "<br><br>";
                if (d.analysis && d.analysis.summary) {{
                    html += "📊 " + d.analysis.summary + "<br><br>";
                }}
                if (d.min_time_violated) {{
                    html += "⚠ <b>警告：执行时长不足，已标记。</b><br><br>";
                }}
                html += "⚡ <b>返回对话窗口，主人正在等你。</b>";
                detail.innerHTML = html;
                overlay.classList.add("show");
                document.querySelector(".controls").style.display = "none";
                toast("✅ 分析完成，返回对话等待主人审阅");
            }} else if (d.error) {{
                toast("⛔ " + d.error);
                btn.disabled = false;
                btn.textContent = "⏹ 结束惩罚";
                pollTimer = setInterval(pollPhotos, 2000);
                autoCaptureTimer = setInterval(autoCapture, 30000);
                strikeTimer = setInterval(pollStrikes, 2000);
            }} else {{
                toast("❌ 操作失败");
                btn.disabled = false;
                btn.textContent = "⏹ 结束惩罚";
                pollTimer = setInterval(pollPhotos, 2000);
                autoCaptureTimer = setInterval(autoCapture, 30000);
                strikeTimer = setInterval(pollStrikes, 2000);
            }}
        }})
        .catch(function() {{
            toast("❌ 操作失败");
            btn.disabled = false;
            btn.textContent = "⏹ 结束惩罚";
            pollTimer = setInterval(pollPhotos, 2000);
            autoCaptureTimer = setInterval(autoCapture, 30000);
            strikeTimer = setInterval(pollStrikes, 2000);
        }});
}}

// 每30秒自动拍照取证
function autoCapture() {{
    fetch("/api/photo")
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{
            if (d.file_path) {{
                pollPhotos();
            }}
        }})
        .catch(function() {{ }});  // 静默失败，不影响体验
}}

// 每2秒轮询击打计数
var lastVisual = 0, lastAudio = 0;
function pollStrikes() {{
    fetch("/api/strikes")
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{
            var vc = d.visual_count || 0;
            var ac = d.audio_count || 0;
            var vel = document.getElementById("visualCount");
            var ael = document.getElementById("audioCount");
            if (vc !== lastVisual) {{
                vel.textContent = vc;
                vel.classList.add("pulse");
                setTimeout(function() {{ vel.classList.remove("pulse"); }}, 300);
                lastVisual = vc;
            }}
            if (ac !== lastAudio) {{
                ael.textContent = ac;
                ael.classList.add("pulse");
                setTimeout(function() {{ ael.classList.remove("pulse"); }}, 300);
                lastAudio = ac;
            }}
        }})
        .catch(function() {{ }});  // 静默失败
}}

// 启动取证轮询 + 自动拍照 + 击打轮询
pollPhotos();
pollTimer = setInterval(pollPhotos, 2000);
autoCaptureTimer = setInterval(autoCapture, 30000);  // 每30秒自动拍一张
strikeTimer = setInterval(pollStrikes, 2000);  // 每2秒更新击打计数
pollStrikes();
</script>
</body>
</html>"""


# ---------- MJPEG HTTP 服务 ----------

class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器 —— MJPEG 长连接不阻塞其他请求。"""
    daemon_threads = True


class _MJPEGHandler(BaseHTTPRequestHandler):
    """MJPEG 推流请求处理器 —— 每路推流一个实例。"""

    stream: Optional["CameraStream"] = None  # 由工厂注入

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self._serve_frontend()
        elif self.path == "/stream":
            self._serve_mjpeg()
        elif self.path == "/snapshot":
            self._serve_snapshot()
        elif self.path == "/shutdown":
            self._serve_shutdown()
        elif self.path == "/api/info":
            self._serve_api_info()
        elif self.path == "/api/photo":
            self._serve_api_photo()
        elif self.path == "/api/photos":
            self._serve_api_photos()
        elif self.path == "/api/stop":
            self._serve_api_stop()
        elif self.path == "/api/strikes":
            self._serve_api_strikes()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write("404".encode("utf-8"))

    def _serve_shutdown(self) -> None:
        """远程停止推流——跨进程关闭入口。完整清理摄像头资源。"""
        stream = self.stream
        if stream:
            stream.running = False
            if stream._cap:
                stream._cap.release()
                stream._cap = None
            with stream.frame_lock:
                stream.current_frame = None
            with _stream_lock:
                _active_streams.pop(stream.punish_id, None)
            _remove_stream_from_state(stream.punish_id)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("推流已停止".encode("utf-8"))
        # 在新线程中 shutdown 避免阻塞当前请求
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    # ── 前端页面 ──

    def _serve_frontend(self) -> None:
        """返回监督前端 HTML 页面。"""
        stream = self.stream
        info = stream.punish_info if stream else {}
        html = _build_frontend_html(
            punish_id=info.get("id", stream.punish_id if stream else "unknown"),
            punish_type=info.get("type", "未知"),
            description=info.get("description", "未知"),
            amount=info.get("final_amount", info.get("base_amount", "未知")),
            deadline=info.get("deadline", "未知"),
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    # ── API 端点 ──

    def _serve_api_info(self) -> None:
        """返回惩罚令信息 JSON。"""
        stream = self.stream
        info = {
            "punish_id": stream.punish_id if stream else "unknown",
            "active": stream.running if stream else False,
            "stream_url": f"http://127.0.0.1:{stream.port}/stream" if stream else None,
            "punish_info": stream.punish_info if stream else {},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(info, ensure_ascii=False).encode("utf-8"))

    def _serve_api_photo(self) -> None:
        """拍照取证 API。"""
        stream = self.stream
        if stream is None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "推流未运行"}).encode("utf-8"))
            return
        result = stream.capture_photo()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))

    def _serve_api_photos(self) -> None:
        """返回当前惩罚令的取证照片列表。"""
        import glob as _glob
        stream = self.stream
        if stream is None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(json.dumps({"photos": []}).encode("utf-8"))
            return
        capture_dir = _ensure_capture_dir()
        pattern = os.path.join(capture_dir, f"{stream.punish_id}_*.jpg")
        files = sorted(_glob.glob(pattern))
        photos = []
        for fpath in files:
            fname = os.path.basename(fpath)
            # 从文件名提取时间戳: punish_id_YYYYMMDD_HHMMSS.jpg
            mtime = os.path.getmtime(fpath)
            captured_at = datetime.fromtimestamp(mtime).isoformat()
            photos.append({
                "filename": fname,
                "captured_at": captured_at,
            })
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"photos": photos}, ensure_ascii=False).encode("utf-8"))

    def _serve_api_stop(self) -> None:
        """结束惩罚 API：最小时长校验 → 拍照留底 → 自动分析 → 自动提交 → 写信号文件 → 通知主人审阅。"""
        stream = self.stream
        if stream is None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "推流未运行"}).encode("utf-8"))
            return

        # 0. 最小时长校验
        min_time_violated = False
        elapsed_seconds = 0
        if stream.started_at:
            started_dt = datetime.fromisoformat(stream.started_at)
            elapsed_seconds = (datetime.now() - started_dt).total_seconds()

            # 根据惩罚类型和数量确定最小时长（基础数量 × 倍率）
            info = stream.punish_info
            punish_type = info.get("type", "")
            base_amount_str = info.get("base_amount", "")
            multiplier = float(info.get("multiplier", 1.0))

            # 解析数量——支持阿拉伯数字（"20下"）和中文数字（"二十下"）
            def _parse_count(s: str) -> int:
                """从字符串中提取第一个数字（支持中英文）。"""
                # 先尝试阿拉伯数字
                arabic = _re.findall(r"\d+", s)
                if arabic:
                    return int(arabic[0])
                # 中文数字映射
                cn_map = {
                    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                }
                # 匹配 "二十" (20), "三十" (30) 等
                cn_tens = _re.findall(r"[二三四五六七八九]?十[一二三四五六七八九]?", s)
                if cn_tens:
                    t = cn_tens[0]
                    if len(t) == 1:  # "十"
                        return 10
                    prefix = cn_map.get(t[0], 0)  # "二" → 2, for "二十"
                    if prefix > 1:
                        return prefix * 10
                    elif t[1] != "十":  # "十二" → 12
                        return 10 + cn_map.get(t[1], 0)
                    else:  # "十X" pattern already handled
                        return 10
                # 匹配单个中文数字
                for ch in s:
                    if ch in cn_map:
                        return cn_map[ch]
                return 0

            base_count = _parse_count(str(base_amount_str))
            count = int(base_count * multiplier)
            if punish_type in ("耳光", "戒尺", "皮带", "数据线"):
                # 体罚：每下至少3秒，最少30秒
                c = count if count > 0 else 10
                min_seconds = max(c * 3, 30)
            elif punish_type in ("罚跪", "罚站"):
                # 罚跪/罚站：按分钟数，最少60秒
                c = count if count > 0 else 5
                min_seconds = max(c * 60, 60)
            else:
                # 综合/其他：最少60秒
                min_seconds = 60

            if elapsed_seconds < min_seconds * 0.5:  # 少于一半时间则拒绝
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": f"执行时长不足！已执行 {int(elapsed_seconds)}秒，至少需要 {int(min_seconds * 0.5)}秒。主人正在监督，别想糊弄。",
                    "elapsed_seconds": elapsed_seconds,
                    "min_seconds_required": int(min_seconds * 0.5),
                }, ensure_ascii=False).encode("utf-8"))
                return
            elif elapsed_seconds < min_seconds:
                min_time_violated = True  # 时间不够但还不太离谱，标记警告

        # 1. 拍照留底
        final_photo = stream.capture_photo()

        # 2. 关联最后一张照片到惩罚令 proof_media
        from punish import add_proof_media
        attach_result = add_proof_media(stream.punish_id, "photo", final_photo.get("file_path", ""))

        # 3. 自动分析 + 自动提交
        analysis = None
        submitted = False
        review_file = ""
        try:
            import analyze as analyze_mod
            analysis = analyze_mod.analyze_punishment(stream.punish_id)
            from punish import submit_proof
            import glob as _glob
            capture_dir = _ensure_capture_dir()
            pattern = os.path.join(capture_dir, f"{stream.punish_id}_*.jpg")
            photo_count = len(_glob.glob(pattern))
            analysis_summary = analysis.get("summary", "") if analysis else ""
            verdicts = []
            if analysis:
                for p in analysis.get("photos", []):
                    verdicts.append(p.get("verdict", ""))
            verdict_text = "；".join(verdicts) if verdicts else "无分析结果"
            proof_text = (
                f"【摄像头自动取证】共拍照 {photo_count} 张。\n"
                f"分析总结：{analysis_summary}\n"
                f"逐张判定：{verdict_text}"
            )
            submit_result = submit_proof(stream.punish_id, proof_text)
            submitted = "error" not in submit_result

            # 4. 写信号文件通知主人审阅
            if submitted:
                review_data = {
                    "punish_id": stream.punish_id,
                    "submitted_at": datetime.now().isoformat(),
                    "analysis": analysis,
                    "photo_count": photo_count,
                }
                review_dir = os.path.join(os.path.dirname(__file__), "data")
                os.makedirs(review_dir, exist_ok=True)
                review_file = os.path.join(review_dir, f"review_ready_{stream.punish_id}.json")
                with open(review_file, "w", encoding="utf-8") as f:
                    json.dump(review_data, f, ensure_ascii=False, indent=2)
        except Exception:
            analysis = {"error": "自动分析失败", "summary": ""}

        # 5. 清理资源
        stream.running = False
        if stream._cap:
            stream._cap.release()
            stream._cap = None
        with stream.frame_lock:
            stream.current_frame = None
        with _stream_lock:
            _active_streams.pop(stream.punish_id, None)
        _remove_stream_from_state(stream.punish_id)
        http_server = self.server
        threading.Thread(target=http_server.shutdown, daemon=True).start()

        # 汇总击打数据
        strike_data = {
            "visual": stream.visual_strikes.get_summary(),
            "audio": stream.audio_strikes.get_summary(),
            "combined_total": max(
                stream.visual_strikes.get_count(),
                stream.audio_strikes.get_count(),
            ),
        }
        stream.audio_strikes.stop()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({
            "done": True,
            "message": "惩罚执行完毕，分析已完成，等待主人审阅。",
            "elapsed_seconds": int(elapsed_seconds),
            "min_time_violated": min_time_violated,
            "final_photo": final_photo,
            "attached_to_punishment": "error" not in attach_result,
            "auto_submitted": submitted,
            "analysis": analysis,
            "review_file": review_file,
            "strike_data": strike_data,
            "stopped_at": datetime.now().isoformat(),
        }, ensure_ascii=False).encode("utf-8"))

    def _serve_api_strikes(self) -> None:
        """返回当前击打计数（前端轮询）。"""
        stream = self.stream
        if not stream:
            self.send_response(404)
            self.end_headers()
            return
        data = {
            "visual_count": stream.visual_strikes.get_count(),
            "audio_count": stream.audio_strikes.get_count(),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _serve_mjpeg(self) -> None:
        """MJPEG multipart 推流。"""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        stream = self.stream
        try:
            while stream and stream.running:
                with stream.frame_lock:
                    if stream.current_frame is None:
                        time.sleep(0.05)
                        continue
                    jpeg = stream.current_frame.copy()
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                self.wfile.write(jpeg.tobytes())
                self.wfile.write(b"\r\n")
                time.sleep(1.0 / max(stream.fps, 1))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开，正常退出

    def _serve_snapshot(self) -> None:
        """提供当前帧的单张 JPEG 快照。"""
        stream = self.stream
        if stream is None or stream.current_frame is None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write("503 - 摄像头尚未就绪".encode("utf-8"))
            return
        with stream.frame_lock:
            jpeg = stream.current_frame.copy()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg.tobytes())

    def log_message(self, format, *args):
        """抑制默认日志输出。"""
        pass


# ---------- 击打检测 ----------

class VisualStrikeCounter:
    """基于帧差值的击打次数检测器——每次挥手/打击产生大幅帧间像素变化。

    原理：正常推流帧间差异小（人基本不动），击打瞬间手臂快速挥动导致大面积像素变化。
    冷却期防重复计数（一次击打可能跨多帧）。
    """

    def __init__(self, motion_threshold: float = 30.0, cooldown_sec: float = 0.7,
                 spike_ratio: float = 3.0):
        self.motion_threshold = motion_threshold  # 帧差均值绝对阈值
        self.spike_ratio = spike_ratio            # 相对于基线的倍数阈值（避免普通动作误触）
        self.cooldown_sec = cooldown_sec
        self._prev_frame: np.ndarray | None = None
        self._recent_diffs: list[float] = []      # 滑动窗口记录近期帧差（用于建立基线）
        self._baseline_samples: int = 30          # 前N帧跳过（建立基线期间不触发）
        self.strikes: list[dict] = []
        self._last_strike_time: float = 0.0
        self._lock = threading.Lock()

    def _recent_baseline(self) -> float:
        """近期帧差的平均基线（排除最高20%的尖峰后取均值）。"""
        if len(self._recent_diffs) < 5:
            return 1.0
        sorted_diffs = sorted(self._recent_diffs)
        cutoff = max(1, int(len(sorted_diffs) * 0.8))
        return float(np.mean(sorted_diffs[:cutoff]))

    def feed(self, frame_bgr: np.ndarray) -> dict | None:
        """喂入一帧 BGR 图像，若检测到击打动作返回 strike dict，否则返回 None。"""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        if self._prev_frame is None:
            self._prev_frame = gray
            return None

        diff = cv2.absdiff(gray, self._prev_frame)
        mean_diff = float(diff.mean())
        self._prev_frame = gray

        # 维护滑动窗口基线（~4秒 @15fps）
        self._recent_diffs.append(mean_diff)
        if len(self._recent_diffs) > 60:
            self._recent_diffs.pop(0)

        now = time.time()
        # 建立基线期间不触发
        if len(self._recent_diffs) < self._baseline_samples:
            return None
        if now - self._last_strike_time < self.cooldown_sec:
            return None

        # 双阈值：绝对差值 > 30 AND 相对于基线 > 3x 尖峰
        baseline = self._recent_baseline()
        is_spike = (mean_diff > self.motion_threshold and
                    baseline > 0 and mean_diff > baseline * self.spike_ratio)

        if is_spike:
            self._last_strike_time = now
            strike = {
                "timestamp": datetime.now().isoformat(),
                "intensity": round(mean_diff, 1),
                "baseline": round(baseline, 1),
                "ratio": round(mean_diff / baseline, 1),
                "source": "visual",
            }
            with self._lock:
                self.strikes.append(strike)
            return strike
        return None

    def get_count(self) -> int:
        with self._lock:
            return len(self.strikes)

    def get_summary(self) -> dict:
        with self._lock:
            return {
                "method": "frame_differencing",
                "total": len(self.strikes),
                "motion_threshold": self.motion_threshold,
                "cooldown_sec": self.cooldown_sec,
                "strikes": list(self.strikes),
            }


class AudioStrikeDetector:
    """基于音频振幅的击打声检测器——耳光/戒尺/皮带声短促且响度骤升。

    使用 sounddevice 音频流，在回调中检测 RMS 尖峰。
    若 sounddevice 未安装则静默禁用。
    """

    def __init__(self, spike_multiplier: float = 5.0, cooldown_sec: float = 0.3,
                 sample_rate: int = 44100, calibrate_sec: float = 3.0):
        self.spike_multiplier = spike_multiplier  # 尖峰 = 基线RMS × 倍数
        self.cooldown_sec = cooldown_sec
        self.sample_rate = sample_rate
        self.calibrate_sec = calibrate_sec        # 启动后先校准N秒
        self.rms_threshold: float = 0.0           # 校准后自动设置
        self._baseline_rms: float = 0.0           # 背景噪音RMS基线
        self._calibration_samples: list[float] = []
        self._calibrated: bool = False
        self._started_at: float = 0.0
        self.strikes: list[dict] = []
        self._last_strike_time: float = 0.0
        self.running: bool = False
        self._stream = None
        self._lock = threading.Lock()

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            return
        rms = float(np.sqrt(np.mean(indata ** 2)))

        # 校准阶段：收集背景噪音样本
        if not self._calibrated:
            self._calibration_samples.append(rms)
            if time.time() - self._started_at >= self.calibrate_sec:
                # 校准完成：基线 = 中位数RMS（排除尖峰），阈值 = 基线 × 倍数
                if self._calibration_samples:
                    self._baseline_rms = float(np.median(self._calibration_samples))
                    self.rms_threshold = max(self._baseline_rms * self.spike_multiplier, 0.05)
                    self._calibrated = True
                    self._calibration_samples.clear()
                    print(f"[audio] 校准完成：基线RMS={self._baseline_rms:.4f}, "
                          f"阈值={self.rms_threshold:.4f}", file=_sys.stderr, flush=True)
            return  # 校准期间不检测

        now = time.time()
        if now - self._last_strike_time < self.cooldown_sec:
            return
        if rms > self.rms_threshold:
            self._last_strike_time = now
            strike = {
                "timestamp": datetime.now().isoformat(),
                "rms": round(rms, 4),
                "baseline_rms": round(self._baseline_rms, 4),
                "threshold": round(self.rms_threshold, 4),
                "ratio": round(rms / max(self._baseline_rms, 0.0001), 1),
                "source": "audio",
            }
            with self._lock:
                self.strikes.append(strike)

    def start(self) -> dict:
        if not _HAS_AUDIO:
            return {"status": "disabled", "reason": "sounddevice 未安装"}
        try:
            # 重置校准状态
            self._calibrated = False
            self._calibration_samples.clear()
            self._started_at = time.time()
            self.rms_threshold = 0.0
            self.running = True
            self._stream = _sd.InputStream(
                callback=self._audio_callback,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=1024,
            )
            self._stream.start()
            return {"status": "started"}
        except Exception as exc:
            self.running = False
            return {"error": f"音频流启动失败: {exc}"}

    def stop(self) -> None:
        self.running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def get_count(self) -> int:
        with self._lock:
            return len(self.strikes)

    def get_summary(self) -> dict:
        with self._lock:
            return {
                "method": "audio_rms_spike",
                "total": len(self.strikes),
                "baseline_rms": round(self._baseline_rms, 4),
                "rms_threshold": round(self.rms_threshold, 4),
                "spike_multiplier": self.spike_multiplier,
                "calibrated": self._calibrated,
                "cooldown_sec": self.cooldown_sec,
                "strikes": list(self.strikes),
            }


class CameraStream:
    """一路摄像头推流 —— 后台线程采集帧 + HTTP MJPEG 服务。"""

    def __init__(
        self,
        punish_id: str,
        camera_index: int = 0,
        port: int = 8080,
        width: int = 640,
        height: int = 480,
        fps: int = 15,
        quality: int = 30,
        punish_info: dict | None = None,
        auto_capture: bool = False,
    ):
        self.punish_id = punish_id
        self.camera_index = camera_index
        self.port = port
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality
        self.punish_info = punish_info or {}
        self.auto_capture = auto_capture

        self.running = False
        self._cap: Optional[cv2.VideoCapture] = None
        self._http_server: Optional[HTTPServer] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._http_thread: Optional[threading.Thread] = None
        self._auto_capture_thread: Optional[threading.Thread] = None

        # 当前帧（JPEG 编码后的字节）
        self.current_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()

        self.stream_id = str(uuid.uuid4())[:8]
        self.started_at: Optional[str] = None

        # 击打检测器
        self.visual_strikes = VisualStrikeCounter()
        self.audio_strikes = AudioStrikeDetector()

    # ---- 启动 / 停止 ----

    def start(self) -> dict:
        """启动摄像头采集和 MJPEG 推流。"""
        import socket

        if self.running:
            return {"error": "该惩罚的摄像头推流已在运行中。先停再开，蠢货。"}

        # 1. 打开摄像头
        self._cap = cv2.VideoCapture(self.camera_index)
        if not self._cap.isOpened():
            return {"error": f"无法打开摄像头（索引 {self.camera_index}）。检查摄像头是否连接、是否被其他程序占用。"}

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap.set(cv2.CAP_PROP_FPS, self.fps)

        # 预热摄像头
        for _ in range(5):
            self._cap.read()

        # 2. 启动 HTTP MJPEG 服务（先绑端口避免采集线程空转）
        handler = type("_BoundMJPEGHandler", (_MJPEGHandler,), {"stream": self})

        # 尝试绑定端口，失败则自动找可用端口
        max_retries = 10
        for attempt in range(max_retries):
            try:
                self._http_server = _ThreadingHTTPServer(("127.0.0.1", self.port), handler)
                self._http_server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                break
            except OSError:
                self.port += 1
                if attempt == max_retries - 1:
                    self._cap.release()
                    self._cap = None
                    return {"error": f"无法绑定 HTTP 端口（尝试了 {max_retries} 个端口，从 {self.port - max_retries} 到 {self.port - 1}）。所有端口都被占用。"}
                continue

        self.running = True
        self.started_at = datetime.now().isoformat()

        # 3. 后台采集线程
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()

        # 4. HTTP 服务线程（非 daemon，进程存活直到推流被停止）
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=False)
        self._http_thread.start()

        # 5. 自动随机取证线程
        if self.auto_capture:
            self._auto_capture_thread = threading.Thread(target=self._auto_capture_loop, daemon=True)
            self._auto_capture_thread.start()

        # 6. 音频击打检测
        audio_result = self.audio_strikes.start()
        if "error" in audio_result:
            print(f"[camera] 音频检测启动失败: {audio_result['error']}", file=_sys.stderr)
        elif audio_result.get("status") != "disabled":
            print(f"[camera] 音频击打检测已启动", file=_sys.stderr)

        # 注册到全局活跃流表 + 持久化文件
        stream_url = f"http://127.0.0.1:{self.port}/stream"
        snapshot_url = f"http://127.0.0.1:{self.port}/snapshot"
        with _stream_lock:
            _active_streams[self.punish_id] = self
        _add_stream_to_state(self.punish_id, self.port, self.stream_id, stream_url, snapshot_url)

        return {
            "message": f"摄像头推流已启动。主人浏览器打开 {stream_url} 即可实时观看。快照端点：{snapshot_url}",
            "punish_id": self.punish_id,
            "stream_id": self.stream_id,
            "stream_url": stream_url,
            "snapshot_url": snapshot_url,
            "port": self.port,
            "started_at": self.started_at,
        }

    def stop(self) -> dict:
        """停止推流，释放摄像头。"""
        if not self.running:
            return {"error": "推流未在运行。"}

        self.running = False

        # 停止音频检测器
        self.audio_strikes.stop()

        # 先释放摄像头设备
        if self._cap:
            self._cap.release()
            self._cap = None

        with self.frame_lock:
            self.current_frame = None

        # 停止 HTTP 服务
        if self._http_server:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None

        # 等待 HTTP 线程退出
        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=3)

        # 从全局表 + 持久化文件移除
        with _stream_lock:
            _active_streams.pop(self.punish_id, None)
        _remove_stream_from_state(self.punish_id)

        # 汇总击打数据
        visual_summary = self.visual_strikes.get_summary()
        audio_summary = self.audio_strikes.get_summary()
        strike_data = {
            "visual": visual_summary,
            "audio": audio_summary,
            "combined_total": max(visual_summary["total"], audio_summary["total"]),
        }

        return {
            "message": f"摄像头推流已停止。惩罚 ID={self.punish_id}。",
            "punish_id": self.punish_id,
            "stopped_at": datetime.now().isoformat(),
            "strike_data": strike_data,
        }

    # ---- 内部循环 ----

    def _capture_loop(self) -> None:
        """后台线程：持续从摄像头采集帧，编码为 JPEG。"""
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
        while self.running and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            # 视觉击打检测（帧差值）
            self.visual_strikes.feed(frame)
            _, jpeg = cv2.imencode(".jpg", frame, encode_params)
            with self.frame_lock:
                self.current_frame = jpeg
            time.sleep(1.0 / max(self.fps, 1))

    # ---- 自动随机取证 ----

    def _auto_capture_loop(self) -> None:
        """后台线程：随机间隔自动拍照取证，通过 HTTP API 调用避免摄像头竞争。"""
        import random as _random
        import sys as _sys
        import urllib.request as _req

        # 等待摄像头预热 + HTTP 服务器就绪
        time.sleep(4)
        url = f"http://127.0.0.1:{self.port}/api/photo"

        while self.running:
            interval = _random.randint(8, 30)
            time.sleep(interval)
            if not self.running:
                break
            try:
                with _req.urlopen(url, timeout=5) as r:
                    r.read()
                print(f"[auto-capture] OK interval={interval}s", file=_sys.stderr, flush=True)
            except Exception as e:
                print(f"[auto-capture] FAIL: {e}", file=_sys.stderr, flush=True)

    # ---- 拍照 ----

    def capture_photo(self) -> dict:
        """拍摄当前帧保存为 JPEG 文件。"""
        if not self.running or self.current_frame is None:
            # 尝试用独立方式抓一帧
            return self._capture_single_photo()

        capture_dir = _ensure_capture_dir()
        filename = f"{self.punish_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = os.path.join(capture_dir, filename)

        with self.frame_lock:
            jpeg_bytes = self.current_frame.tobytes()

        with open(filepath, "wb") as f:
            f.write(jpeg_bytes)

        return {
            "message": f"拍照取证完成。已保存：{filename}",
            "punish_id": self.punish_id,
            "file_path": filepath,
            "filename": filename,
            "captured_at": datetime.now().isoformat(),
        }

    def _capture_single_photo(self) -> dict:
        """推流未运行时，独立打开摄像头拍一张。"""
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            return {"error": f"无法打开摄像头（索引 {self.camera_index}）。"}

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # 预热
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        cap.release()

        if not ret:
            return {"error": "摄像头读取帧失败。"}

        capture_dir = _ensure_capture_dir()
        filename = f"{self.punish_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = os.path.join(capture_dir, filename)
        cv2.imwrite(filepath, frame)

        return {
            "message": f"拍照取证完成（独立模式）。已保存：{filename}",
            "punish_id": self.punish_id,
            "file_path": filepath,
            "filename": filename,
            "captured_at": datetime.now().isoformat(),
        }

    # ---- 录像 ----

    def capture_video(self, duration_seconds: int = 10) -> dict:
        """录制指定时长的视频存为 MP4。"""
        capture_dir = _ensure_capture_dir()
        filename = f"{self.punish_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        filepath = os.path.join(capture_dir, filename)

        # 如果推流正在运行，使用当前采集线程的帧录制
        # 否则独立打开摄像头录制
        if self.running and self._cap and self._cap.isOpened():
            return self._record_from_stream(filepath, duration_seconds)
        else:
            return self._record_standalone(filepath, duration_seconds)

    def _record_from_stream(self, filepath: str, duration: int) -> dict:
        """从正在运行的推流中录制。"""
        fps = self.fps
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))

        start = time.time()
        frames_written = 0
        while time.time() - start < duration:
            with self.frame_lock:
                if self.current_frame is not None:
                    # 需要解码 JPEG 回 BGR
                    frame = cv2.imdecode(self.current_frame, cv2.IMREAD_COLOR)
                    if frame is not None:
                        writer.write(frame)
                        frames_written += 1
            time.sleep(1.0 / fps)

        writer.release()

        return {
            "message": f"录像取证完成（{duration}秒）。已保存：{os.path.basename(filepath)}",
            "punish_id": self.punish_id,
            "file_path": filepath,
            "duration_seconds": duration,
            "frames_written": frames_written,
            "recorded_at": datetime.now().isoformat(),
        }

    def _record_standalone(self, filepath: str, duration: int) -> dict:
        """独立打开摄像头录制。"""
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            return {"error": f"无法打开摄像头（索引 {self.camera_index}）。"}

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        fps = min(self.fps, cap.get(cv2.CAP_PROP_FPS) or self.fps)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))

        # 预热
        for _ in range(5):
            cap.read()

        frames_written = 0
        start = time.time()
        while time.time() - start < duration:
            ret, frame = cap.read()
            if ret:
                writer.write(frame)
                frames_written += 1
            else:
                break

        writer.release()
        cap.release()

        return {
            "message": f"录像取证完成（独立模式，{duration}秒）。已保存：{os.path.basename(filepath)}",
            "punish_id": self.punish_id,
            "file_path": filepath,
            "duration_seconds": duration,
            "frames_written": frames_written,
            "recorded_at": datetime.now().isoformat(),
        }


# ---------- 全局管理函数 ----------

def start_stream(punish_id: str, punish_info: dict | None = None, auto_capture: bool = False) -> dict:
    """为指定惩罚令启动摄像头推流。"""
    # 检查是否已有推流在运行（跨进程检查）
    state = _load_stream_state()
    if punish_id in state:
        existing = state[punish_id]
        return {
            "error": f"该惩罚令已有推流在运行。端口 {existing['port']}。如需重启请先 camera-stop。",
            "existing": existing,
        }

    with _stream_lock:
        if punish_id in _active_streams:
            s = _active_streams[punish_id]
            return {
                "error": "该惩罚令已有推流在运行（当前进程）。",
                "existing": {"stream_url": f"http://127.0.0.1:{s.port}/stream"},
            }

    cfg = _load_config()

    # 找可用端口
    port = cfg["stream_port_start"]
    used_ports = {int(v["port"]) for v in state.values()}
    while port in used_ports:
        port += 1

    stream = CameraStream(
        punish_id=punish_id,
        camera_index=cfg["camera_index"],
        port=port,
        width=cfg["capture_width"],
        height=cfg["capture_height"],
        fps=cfg["stream_fps"],
        quality=cfg["stream_quality"],
        punish_info=punish_info,
        auto_capture=auto_capture,
    )
    return stream.start()


def stop_stream(punish_id: str) -> dict:
    """停止指定惩罚令的摄像头推流（支持跨进程）。"""
    # 1. 先尝试当前进程
    with _stream_lock:
        stream = _active_streams.get(punish_id)
    if stream is not None:
        return stream.stop()

    # 2. 检查持久化文件 —— 推流可能在另一个进程中
    state = _load_stream_state()
    if punish_id not in state:
        return {"error": f"未找到惩罚令 ID={punish_id} 的推流。"}

    # 3. 通过 HTTP /shutdown 远程关闭
    import urllib.request
    port = state[punish_id]["port"]
    shutdown_ok = False
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/shutdown", timeout=3)
        shutdown_ok = True
    except Exception:
        # HTTP 关闭失败——进程可能卡死，用系统命令兜底杀进程
        import subprocess
        import sys as _sys
        try:
            if _sys.platform == "win32":
                result = subprocess.run(
                    ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.splitlines():
                    if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                        pid = line.strip().split()[-1]
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=10,
                        )
                        break
            else:
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True, timeout=10,
                )
        except Exception:
            pass

    _remove_stream_from_state(punish_id)
    return {
        "message": f"摄像头推流已停止{'（远程关闭）' if shutdown_ok else '（强制清理）'}。惩罚 ID={punish_id}。",
        "punish_id": punish_id,
        "stopped_at": datetime.now().isoformat(),
    }


def capture_photo(punish_id: str) -> dict:
    """为指定惩罚令拍照取证（推流在别的进程则用独立模式）。"""
    with _stream_lock:
        stream = _active_streams.get(punish_id)

    if stream is not None:
        return stream.capture_photo()

    # 推流在别的进程或不存在，独立拍照
    cfg = _load_config()
    tmp_stream = CameraStream(
        punish_id=punish_id,
        camera_index=cfg["camera_index"],
        width=cfg["capture_width"],
        height=cfg["capture_height"],
    )
    return tmp_stream.capture_photo()


def capture_video(punish_id: str, duration_seconds: int = 10) -> dict:
    """为指定惩罚令录像取证（推流在别的进程则用独立模式）。"""
    with _stream_lock:
        stream = _active_streams.get(punish_id)

    if stream is not None:
        return stream.capture_video(duration_seconds)

    cfg = _load_config()
    tmp_stream = CameraStream(
        punish_id=punish_id,
        camera_index=cfg["camera_index"],
        width=cfg["capture_width"],
        height=cfg["capture_height"],
    )
    return tmp_stream.capture_video(duration_seconds)


def get_stream_info(punish_id: str) -> dict:
    """获取指定惩罚令的推流信息（跨进程读取）。"""
    with _stream_lock:
        stream = _active_streams.get(punish_id)
    if stream is not None:
        return {
            "punish_id": punish_id,
            "active": True,
            "stream_id": stream.stream_id,
            "stream_url": f"http://127.0.0.1:{stream.port}/stream",
            "snapshot_url": f"http://127.0.0.1:{stream.port}/snapshot",
            "port": stream.port,
            "started_at": stream.started_at,
        }

    # 检查持久化文件
    state = _load_stream_state()
    if punish_id in state:
        s = state[punish_id]
        return {
            "punish_id": punish_id,
            "active": True,
            "stream_id": s["stream_id"],
            "stream_url": s["stream_url"],
            "snapshot_url": s["snapshot_url"],
            "port": s["port"],
            "started_at": s["started_at"],
        }

    return {"punish_id": punish_id, "active": False}


def list_active_streams() -> list[dict]:
    """列出所有活跃推流（跨进程读取）。"""
    state = _load_stream_state()
    return [get_stream_info(pid) for pid in state]


def stop_all_streams() -> list[dict]:
    """停止所有推流（程序退出前调用，跨进程关闭）。"""
    import urllib.request

    results = []

    # 先停当前进程中的
    with _stream_lock:
        pids = list(_active_streams.keys())
    for pid in pids:
        results.append(stop_stream(pid))

    # 再远程关其他进程的（重新加载状态，跳过已停止的）
    state = _load_stream_state()
    for pid, info in state.items():
        if pid in pids:
            continue  # 已由当前进程停止
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/shutdown", timeout=3)
        except Exception:
            pass
        _remove_stream_from_state(pid)
        results.append({"punish_id": pid, "message": "远程推流已关闭。"})

    return results
