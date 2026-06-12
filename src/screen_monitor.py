"""
screen_monitor.py - 娱乐/社交剥夺监督模块
进程监控 + 截图取证 + Win32悬浮窗 + 到期自动停止
"""
import ctypes, ctypes.wintypes as _w, json, os, subprocess, sys, tempfile, threading, time, uuid
from datetime import datetime
import cv2, numpy as np

from _paths import PROJECT_ROOT

_BASE = PROJECT_ROOT
PROOFS_DIR = os.path.join(PROJECT_ROOT, "data", "proofs")
STATUS_FILE = os.path.join(PROJECT_ROOT, "data", "screen_monitor.json")

FORBIDDEN_PROCESSES = [
    "steam.exe","epicgameslauncher.exe","battle.net.exe","ubisoftconnect.exe","eaapp.exe","goggalaxy.exe",
    "leagueclient.exe","league of legends.exe","valorant.exe","genshinimpact.exe","honkaiimpact.exe",
    "starrail.exe","wutheringwaves.exe","naraka-blade-point.exe","pubg.exe","tslgame.exe","cod.exe","overwatch.exe",
    "douyin.exe","vlc.exe","potplayer.exe","obs64.exe","streamlabs.exe","douyu.exe","huya.exe",
]

SOCIAL_FORBIDDEN_PROCESSES = [
    "discord.exe","telegram.exe","wechat.exe","weixin.exe","wechatappex.exe",
    "qq.exe","qqpc.exe","qqbrowser.exe",
]

FORBIDDEN_WINDOW_KEYWORDS = [
    "youtube","bilibili","twitch","douyin","tiktok","netflix","disney+","prime video",
    "hbo","斗鱼","虎牙","抖音","哔哩哔哩",
]

SOCIAL_FORBIDDEN_WINDOW_KEYWORDS = [
    "微信","wechat","朋友圈","微博","weibo","twitter","x.com","facebook",
    "instagram","reddit","贴吧","tieba","知乎","zhihu","豆瓣","douban",
    "linkedin","telegram","discord","qq空间",
]

SCREENSHOT_INTERVAL = 300
PROCESS_CHECK_INTERVAL = 10
_monitor_lock = threading.Lock()
_active_monitors = {}

def _python_windowed_executable():
    if sys.platform == "win32":
        d = os.path.dirname(sys.executable)
        pw = os.path.join(d, "pythonw.exe")
        if os.path.exists(pw): return pw
    return sys.executable

def _subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

def _pid_is_running(pid):
    if not pid: return False
    try: p = int(pid)
    except: return False
    if sys.platform == "win32":
        r = subprocess.run(["tasklist","/FI",f"PID eq {p}"],capture_output=True,text=True,timeout=5,creationflags=_subprocess_creationflags())
        return str(p) in r.stdout
    try: os.kill(p, 0); return True
    except OSError: return False

def _terminate_pid(pid):
    if not pid: return False
    try: p = int(pid)
    except: return False
    try:
        if sys.platform == "win32": subprocess.run(["taskkill","/F","/PID",str(p)],capture_output=True,timeout=8,creationflags=_subprocess_creationflags())
        else: os.kill(p, 15)
        return True
    except: return False

def _spawn_float_window(punish_id, started_at, social_mode=False):
    if sys.platform != "win32": return None
    try:
        proc = subprocess.Popen([_python_windowed_executable(),os.path.abspath(__file__),"float",punish_id,started_at.isoformat(),"1" if social_mode else "0"],cwd=_BASE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=_subprocess_creationflags())
        return proc.pid
    except: return None

def _capture_screen_win32():
    try:
        u,g = ctypes.windll.user32, ctypes.windll.gdi32
        sw = u.GetSystemMetrics(78) or u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(79) or u.GetSystemMetrics(1)
        if sw <= 0 or sh <= 0: return None
        hdc_s = u.GetDC(None); hdc_m = g.CreateCompatibleDC(hdc_s)
        hbmp = g.CreateCompatibleBitmap(hdc_s, sw, sh)
        g.SelectObject(hdc_m, hbmp); g.BitBlt(hdc_m, 0, 0, sw, sh, hdc_s, 0, 0, 0x00CC0020)
        class BI(ctypes.Structure):
            _fields_=[("biSize",_w.DWORD),("biWidth",_w.LONG),("biHeight",_w.LONG),("biPlanes",_w.WORD),("biBitCount",_w.WORD),("biCompression",_w.DWORD),("biSizeImage",_w.DWORD),("biXPelsPerMeter",_w.LONG),("biYPelsPerMeter",_w.LONG),("biClrUsed",_w.DWORD),("biClrImportant",_w.DWORD)]
        bi=BI(); bi.biSize=ctypes.sizeof(BI); bi.biWidth,bi.biHeight=sw,-sh; bi.biPlanes,bi.biBitCount,bi.biCompression=1,32,0
        buf=(ctypes.c_ubyte*(sw*sh*4))(); g.GetDIBits(hdc_m,hbmp,0,sh,buf,ctypes.byref(bi),0)
        img=np.frombuffer(buf,dtype=np.uint8).reshape(sh,sw,4); bgr=cv2.cvtColor(img[:,:,:3],cv2.COLOR_BGRA2BGR)
        g.DeleteObject(hbmp); g.DeleteDC(hdc_m); u.ReleaseDC(None,hdc_s)
        return bgr
    except: return None

def _check_processes_windows(social_mode=False):
    forbidden = list(FORBIDDEN_PROCESSES)
    if social_mode: forbidden.extend(SOCIAL_FORBIDDEN_PROCESSES)
    violations = []
    try:
        r = subprocess.run(["tasklist","/FO","CSV","/NH"],capture_output=True,text=True,timeout=15,creationflags=_subprocess_creationflags())
        for line in r.stdout.splitlines():
            lower = line.strip('"').lower()
            for f in forbidden:
                if f.lower() in lower:
                    parts=line.split(",")
                    if parts:
                        n=parts[0].strip('"')
                        if n not in violations: violations.append(n)
    except: pass
    return violations

def _kill_process(name):
    try: subprocess.run(["taskkill","/F","/IM",name],capture_output=True,timeout=15,creationflags=_subprocess_creationflags()); return True
    except: return False

def _close_window_by_hwnd(hwnd):
    """发送 WM_CLOSE 关闭窗口"""
    try: ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    except: pass

def _check_window_titles_windows(social_mode=False):
    """枚举所有可见窗口标题，匹配禁止关键词，命中则尝试关闭窗口"""
    forbidden = list(FORBIDDEN_WINDOW_KEYWORDS)
    if social_mode: forbidden.extend(SOCIAL_FORBIDDEN_WINDOW_KEYWORDS)
    violations = []

    def _enum_cb(hwnd, _lparam):
        try:
            if not ctypes.windll.user32.IsWindowVisible(hwnd): return True
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length <= 0: return True
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            lower = title.lower()
            for kw in forbidden:
                if kw.lower() in lower:
                    violations.append(title[:100])
                    _close_window_by_hwnd(hwnd)
                    break
        except: pass
        return True

    try:
        cb = ctypes.WINFUNCTYPE(ctypes.c_bool, _w.HWND, _w.LPARAM)(_enum_cb)
        ctypes.windll.user32.EnumWindows(cb, 0)
    except: pass
    return violations

def _parse_duration(s):
    """解析时长字符串为秒数。支持 '1分钟'/'30秒'/'2小时' 及中文 '三十分钟' 等，全局搜索。"""
    import re
    s = s.strip()
    # 优先阿拉伯数字（全局搜索，不锚定开头）
    m = re.search(r'(\d+(?:\.\d+)?)\s*分钟', s)
    if m: return int(float(m.group(1)) * 60)
    m = re.search(r'(\d+(?:\.\d+)?)\s*小时', s)
    if m: return int(float(m.group(1)) * 3600)
    m = re.search(r'(\d+(?:\.\d+)?)\s*秒', s)
    if m: return int(float(m.group(1)))
    # 中文数字回退
    CN_NUM = {"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,
              "十一":11,"十二":12,"二十":20,"三十":30,"四十":40,"五十":50,"六十":60}
    for cn, val in sorted(CN_NUM.items(), key=lambda x:-len(x[0])):
        if cn in s:
            if "秒" in s: return val
            if "分钟" in s or "分" in s: return val * 60
            if "小时" in s or "时" in s: return val * 3600
            break
    if '半' in s and '分钟' in s: return 30
    if '半' in s and '小时' in s: return 1800
    return None

class _WNDCLASSEXW(ctypes.Structure):
    _fields_=[("cbSize",_w.UINT),("style",_w.UINT),("lpfnWndProc",ctypes.c_void_p),("cbClsExtra",_w.INT),("cbWndExtra",_w.INT),("hInstance",_w.HINSTANCE),("hIcon",_w.HICON),("hCursor",_w.HICON),("hbrBackground",_w.HBRUSH),("lpszMenuName",_w.LPCWSTR),("lpszClassName",_w.LPCWSTR),("hIconSm",_w.HICON)]
class _RECT(ctypes.Structure):
    _fields_=[("left",_w.LONG),("top",_w.LONG),("right",_w.LONG),("bottom",_w.LONG)]
class _PS(ctypes.Structure):
    _fields_=[("hdc",_w.HDC),("fErase",_w.BOOL),("rcPaint",_RECT),("fRestore",_w.BOOL),("fIncUpdate",_w.BOOL),("rgbReserved",_w.BYTE*32)]
class _MSG(ctypes.Structure):
    _fields_=[("hwnd",_w.HWND),("message",_w.UINT),("wParam",_w.WPARAM),("lParam",_w.LPARAM),("time",_w.DWORD),("pt_x",_w.LONG),("pt_y",_w.LONG)]

ctypes.windll.user32.DefWindowProcW.argtypes = (_w.HWND, _w.UINT, ctypes.c_ulonglong, ctypes.c_longlong)
_float_windows = {}

def _float_wndproc(hwnd, msg, wparam, lparam):
    tid = threading.get_ident(); fw = _float_windows.get(tid)
    if fw is None: return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == 0x000F: fw._paint(); return 0
    elif msg == 0x0084:
        x,y=lparam&0xFFFF,(lparam>>16)&0xFFFF; r=_RECT(); ctypes.windll.user32.GetWindowRect(hwnd,ctypes.byref(r))
        return 1 if y-r.top<25 and x-r.left>fw._width-35 else 2
    elif msg == 0x0201:
        x,y=lparam&0xFFFF,(lparam>>16)&0xFFFF
        if y<25 and x>fw._width-35: fw._toggle_minimize()
        return 0
    elif msg == 0x0113:
        ctypes.windll.user32.InvalidateRect(hwnd,None,True)
        try:
            s_all=_load_all_status()
            s=s_all.get(fw.punish_id)
            if not s or not s.get("running"):
                fw.destroy()
                return 0
            vs=s.get("violations",[])
            if vs:
                count=sum(1 for v in vs if v.get("type")!="_batch")
                if count and fw._last_shown!=count:
                    fw._last_shown=count
                    fw.show_toast(f"⚠️ 主人警告！\n违规第 {count} 次！")
        except: pass
        return 0
    elif msg == 0x0010: fw._running=False; ctypes.windll.user32.DestroyWindow(hwnd); return 0
    elif msg == 0x0002: _float_windows.pop(tid,None); ctypes.windll.user32.PostQuitMessage(0); return 0
    return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

class _FloatWindow:
    def __init__(self,pid,started_at,social_mode=False):
        self.punish_id=pid; self.started_at=started_at; self.social_mode=social_mode
        self.hwnd=0; self._tm=""; self._tu=0.0; self._min=False; self._run=True; self._last_shown=""
        self._width=180; self._height=120; self._hf=self._hfb=self._hfs=self._hft=0; self._bd=self._pb=self._tb=0

    def show_toast(self,m): self._tm=m; self._tu=time.time()+10.0
    def destroy(self): self._run=False

    def create(self):
        u,g=ctypes.windll.user32,ctypes.windll.gdi32; k=ctypes.windll.kernel32
        self._tid=threading.get_ident(); _float_windows[self._tid]=self
        hinst=k.GetModuleHandleW(None); cn=f"FW_{self.punish_id[:6]}"
        wc=_WNDCLASSEXW(); wc.cbSize=ctypes.sizeof(_WNDCLASSEXW)
        self._wndproc_hook=ctypes.WINFUNCTYPE(ctypes.c_long,_w.HWND,_w.UINT,_w.WPARAM,_w.LPARAM)(_float_wndproc)
        wc.lpfnWndProc=ctypes.cast(self._wndproc_hook,ctypes.c_void_p)
        wc.hInstance=hinst; wc.lpszClassName=cn; wc.hbrBackground=g.CreateSolidBrush(0x000A0A0A); wc.hCursor=u.LoadCursorW(None,32512)
        if not u.RegisterClassExW(ctypes.byref(wc)): pass  # 可能已注册，忽略错误继续
        sw=u.GetSystemMetrics(0); x,y=sw-self._width-15,30
        wt="社交剥夺" if self.social_mode else "娱乐剥夺"
        self.hwnd=u.CreateWindowExW(0x00000008|0x00000080,cn,wt,0x80000000,x,y,self._width,self._height,0,0,hinst,None)
        if not self.hwnd: return False
        self._hf=g.CreateFontW(18,0,0,0,700,0,0,0,0,0,0,0,0,"Microsoft YaHei")
        self._hfb=g.CreateFontW(20,0,0,0,700,0,0,0,0,0,0,0,0,"Microsoft YaHei")
        self._hfs=g.CreateFontW(13,0,0,0,400,0,0,0,0,0,0,0,0,"Microsoft YaHei")
        self._hft=g.CreateFontW(16,0,0,0,700,0,0,0,0,0,0,0,0,"Microsoft YaHei")
        self._bd=g.CreateSolidBrush(0x000D0D0D); self._pb=g.CreatePen(0,3,0x003030C0)
        self._tb=g.CreateSolidBrush(0x002222AA)
        u.SetTimer(self.hwnd,1,1000,None); u.ShowWindow(self.hwnd,5); u.UpdateWindow(self.hwnd)
        return True

    def run_loop(self):
        u=ctypes.windll.user32; msg=_MSG()
        while self._run and u.GetMessageW(ctypes.byref(msg),0,0,0)>0: u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
        u.KillTimer(self.hwnd,1); u.DestroyWindow(self.hwnd)

    def _paint(self):
        u,g=ctypes.windll.user32,ctypes.windll.gdi32; ps=_PS(); hdc=u.BeginPaint(self.hwnd,ctypes.byref(ps))
        try:
            r=_RECT(); u.GetClientRect(self.hwnd,ctypes.byref(r)); w,h=r.right,r.bottom
            u.FillRect(hdc,ctypes.byref(r),self._bd)
            op=g.SelectObject(hdc,self._pb); g.MoveToEx(hdc,0,0,None); g.LineTo(hdc,w-1,0); g.LineTo(hdc,w-1,h-1); g.LineTo(hdc,0,h-1); g.LineTo(hdc,0,0); g.SelectObject(hdc,op)
            g.SetBkMode(hdc,1)
            has_toast=bool(self._tm and time.time()<self._tu)
            md="⛔ 社交剥夺执行中" if self.social_mode else "⛔ 娱乐剥夺执行中"
            ms="禁止：游戏 | 视频 | 社交 | 手机" if self.social_mode else "禁止：游戏 | 视频 | 直播 | 娱乐"
            g.SetTextColor(hdc,0x000030C0); g.SelectObject(hdc,self._hfb)
            tr=_RECT(); tr.left,tr.top,tr.right,tr.bottom=8,6,w-8,28
            u.DrawTextW(hdc,md,-1,ctypes.byref(tr),0x0001)
            delta=datetime.now()-self.started_at; t=int(delta.total_seconds())
            hh,mm,ss=t//3600,(t%3600)//60,t%60
            g.SetTextColor(hdc,0x00999999); g.SelectObject(hdc,self._hf)
            tr2=_RECT(); tr2.left,tr2.top,tr2.right,tr2.bottom=8,30,w-8,50
            u.DrawTextW(hdc,f"{hh:02d}:{mm:02d}:{ss:02d}",-1,ctypes.byref(tr2),0x0001)
            if has_toast:
                tr5=_RECT(); tr5.left,tr5.top,tr5.right,tr5.bottom=6,56,w-6,h-4
                u.FillRect(hdc,ctypes.byref(tr5),self._tb)
                g.SetTextColor(hdc,0x00FFFFFF); g.SelectObject(hdc,self._hft)
                tr5.left+=8; tr5.top+=6; tr5.right-=4
                u.DrawTextW(hdc,self._tm,-1,ctypes.byref(tr5),0x0001|0x0010)
            else:
                g.SetTextColor(hdc,0x00666666); g.SelectObject(hdc,self._hfs)
                tr3=_RECT(); tr3.left,tr3.top,tr3.right,tr3.bottom=8,54,w-8,90
                u.DrawTextW(hdc,ms,-1,ctypes.byref(tr3),0x0001)
            tr4=_RECT(); tr4.left,tr4.top,tr4.right,tr4.bottom=w-24,2,w-2,18
            u.DrawTextW(hdc,"_",-1,ctypes.byref(tr4),0x0001|0x0200)
        finally: u.EndPaint(self.hwnd,ctypes.byref(ps))

    def _toggle_minimize(self):
        u=ctypes.windll.user32
        if self._min: u.SetWindowPos(self.hwnd,0,0,0,self._width,self._height,0x0001|0x0002); self._min=False
        else: u.SetWindowPos(self.hwnd,0,0,0,self._width,20,0x0001|0x0002); self._min=True

def _build_floating_window_win32(pid,started_at,social_mode=False):
    fw=_FloatWindow(pid,started_at,social_mode); return fw if fw.create() else None

def _load_all_status():
    if not os.path.exists(STATUS_FILE): return {}
    try:
        with open(STATUS_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: return {}

def _save_all_status(data):
    dir_name = os.path.dirname(STATUS_FILE)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATUS_FILE)
    except Exception:
        os.unlink(tmp_path)
        raise

class ScreenMonitor:
    def __init__(self,punish_id):
        self.punish_id=punish_id; self.mid=str(uuid.uuid4())[:8]; self.started_at=None; self.running=False
        self.sc=0; self.violations=[]; self.social=self._detect_social(); self.fpid=None
        self._st=self._pt=self._dt=None; self._ev=threading.Event()

    def _detect_social(self):
        try:
            cp=os.path.join(_BASE,"config.json")
            if os.path.exists(cp):
                with open(cp,"r",encoding="utf-8") as f:
                    for p in json.load(f).get("active_punishments",[]):
                        if p.get("id")==self.punish_id and p.get("type")=="社交剥夺": return True
        except: pass
        return False

    def _load_deadline(self):
        try:
            cp=os.path.join(_BASE,"config.json")
            if os.path.exists(cp):
                with open(cp,"r",encoding="utf-8") as f:
                    for p in json.load(f).get("active_punishments",[]):
                        if p.get("id")==self.punish_id and p.get("deadline"):
                            return datetime.fromisoformat(p["deadline"])
        except: pass
        return None

    def _load_duration(self):
        """从惩罚令数量中解析监控时长（秒），无法解析返回 None"""
        try:
            cp=os.path.join(_BASE,"config.json")
            if os.path.exists(cp):
                with open(cp,"r",encoding="utf-8") as f:
                    for p in json.load(f).get("active_punishments",[]):
                        if p.get("id")==self.punish_id:
                            amount=p.get("final_amount") or p.get("base_amount","")
                            return _parse_duration(amount)
        except: pass
        return None

    def start(self):
        if self.running: return {"error":"监督已在运行中。"}
        os.makedirs(PROOFS_DIR,exist_ok=True); self.started_at=datetime.now(); self.running=True; self._ev.clear()
        self._pt=threading.Thread(target=self._proc_loop,daemon=True); self._pt.start()
        dl=self._load_deadline()
        dur=self._load_duration()
        ws=None
        if dl: ws=max(1,(dl-datetime.now()).total_seconds())
        if dur and (ws is None or dur<ws): ws=max(1,dur)
        if ws: self._dt=threading.Thread(target=self._dline_watch,args=(ws,),daemon=False); self._dt.start()
        with _monitor_lock: _active_monitors[self.punish_id]=self
        self._save()
        if os.environ.get("SCREEN_MONITOR_NO_FLOAT")!="1":
            self.fpid=_spawn_float_window(self.punish_id,self.started_at,self.social); self._save()
        return {"message":"监督已启动。","punish_id":self.punish_id,"monitor_id":self.mid,"process_id":os.getpid(),"float_process_id":self.fpid}

    def stop(self):
        if not self.running: return {"error":"监督未在运行。"}
        self.running=False; self._ev.set()
        if self.fpid: _terminate_pid(self.fpid)
        rf=self._write_signal()  # 先写信号文件，再清状态
        with _monitor_lock: _active_monitors.pop(self.punish_id,None)
        self._save()
        tt=int((datetime.now()-self.started_at).total_seconds()) if self.started_at else 0
        return {"message":"监督已停止。","punish_id":self.punish_id,"duration_seconds":tt,"screenshot_count":self.sc,"violations_count":len(self.violations),"violations":self.violations[-10:],"review_file":rf}

    # 截图循环已禁用 — _proc_loop 每10秒独立做进程+窗口检测，无需截图
    # def _scr_loop(self):
    #     while self.running and not self._ev.is_set():
    #         try:
    #             img=_capture_screen_win32()
    #             if img is not None:
    #                 fp=os.path.join(PROOFS_DIR,f"s_{self.punish_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    #                 cv2.imwrite(fp,img,[cv2.IMWRITE_JPEG_QUALITY,60]); self.sc+=1
    #                 sc=0
    #                 for p in _check_processes_windows(self.social):
    #                     if self._rec("process",p): sc+=1
    #                     _kill_process(p)
    #                 for w in _check_window_titles_windows(self.social):
    #                     if self._rec("window_title",w): sc+=1
    #                 if sc>0:
    #                     self.violations.append({"type":"_batch","detail":"scr:"+str(sc)+"v","time":datetime.now().isoformat()}); self._save()
    #                 else:
    #                     try: os.remove(fp)
    #                     except: pass
    #         except: pass
    #         self._ev.wait(SCREENSHOT_INTERVAL)

    def _proc_loop(self):
        while self.running and not self._ev.is_set():
            nc=0
            try:
                for p in _check_processes_windows(self.social):
                    if self._rec("process",p): nc+=1
                    _kill_process(p)
                for w in _check_window_titles_windows(self.social):
                    if self._rec("window_title",w): nc+=1
            except: pass
            if nc>0:
                self.violations.append({"type":"_batch","detail":"proc:"+str(nc)+"v","time":datetime.now().isoformat()}); self._save()
            self._ev.wait(PROCESS_CHECK_INTERVAL)

    def _rec(self,vt,detail):
        now=datetime.now()
        for v in self.violations[-50:]:
            if v.get("type")==vt and v.get("detail")==detail:
                if (now-datetime.fromisoformat(v["time"])).total_seconds()<600: return False
        self.violations.append({"type":vt,"detail":detail,"time":now.isoformat()}); return True

    def _save(self):
        all_status=_load_all_status()
        if self.running and self.started_at:
            all_status[self.punish_id]={"punish_id":self.punish_id,"monitor_id":self.mid,"started_at":self.started_at.isoformat(),"running":True,"screenshot_count":self.sc,"violations_count":len(self.violations),"violations":self.violations[-20:],"process_id":os.getpid(),"float_process_id":self.fpid}
        else: all_status.pop(self.punish_id,None)
        _save_all_status(all_status)

    def _dline_watch(self,wait_sec):
        self._ev.wait(wait_sec)
        if not self.running: return
        # 必须先提交证明，把状态改为 submitted，再 stop()
        # 防止 check_overdue 在这几毫秒间隙中抢跑升级
        try:
            import punish
            vc=len([v for v in self.violations if v.get("type")!="_batch"])
            proof_txt=f"【屏幕监控自动结算】执行完毕，共截图 {self.sc} 张，发现 {vc} 次违规。"
            punish.submit_proof(self.punish_id, proof_txt)
        except Exception: pass
        self.stop()
        self._print_summary()

    def _write_signal(self):
        tt=int((datetime.now()-self.started_at).total_seconds()) if self.started_at else 0
        rvs=[v for v in self.violations if v.get("type")!="_batch"]
        rd={"punish_id":self.punish_id,"finished_at":datetime.now().isoformat(),"duration_seconds":tt,"screenshot_count":self.sc,"violations_count":len(rvs),"violations":rvs}
        d=os.path.join(_BASE,"data"); os.makedirs(d,exist_ok=True)
        rf=os.path.join(d,f"review_ready_screen_{self.punish_id}.json")
        try:
            with open(rf,"w",encoding="utf-8") as f: json.dump(rd,f,ensure_ascii=False,indent=2)
            with open(os.path.join(_BASE,"data","_debug_signal.txt"),"a",encoding="utf-8") as df:
                df.write(f"OK:{rf}\n")
        except Exception as e:
            with open(os.path.join(_BASE,"data","_debug_signal.txt"),"a",encoding="utf-8") as df:
                df.write(f"FAIL:{rf}:{e}\n")
        return rf

    def _print_summary(self):
        tt=int((datetime.now()-self.started_at).total_seconds()) if self.started_at else 0
        rvs=[v for v in self.violations if v.get("type")!="_batch"]
        print("REVIEW_READY_SCREEN:"+json.dumps({"punish_id":self.punish_id,"duration_seconds":tt,"screenshots":self.sc,"violations":len(rvs)},ensure_ascii=False),flush=True)

def stop_monitoring(punish_id):
    with _monitor_lock: m=_active_monitors.get(punish_id)
    if m is not None: return m.stop()
    e=_load_all_status().get(punish_id)
    if not e or not e.get("running"): return {"error":"未找到监督。"}
    km=_terminate_pid(e.get("process_id")); kf=_terminate_pid(e.get("float_process_id"))
    a=_load_all_status(); a.pop(punish_id,None); _save_all_status(a)
    return {"message":"监督已停止。","punish_id":punish_id,"killed_monitor_pid":e.get("process_id") if km else None,"killed_float_pid":e.get("float_process_id") if kf else None}

def get_status(punish_id):
    with _monitor_lock:
        m=_active_monitors.get(punish_id)
        if m is not None: return {"punish_id":m.punish_id,"running":m.running,"started_at":m.started_at.isoformat() if m.started_at else None,"screenshot_count":m.sc,"violations_count":len(m.violations),"violations":m.violations[-20:]}
    return _load_all_status().get(punish_id,{"error":"未找到监督记录。"})

def list_all_status():
    r={}
    with _monitor_lock:
        for pid,m in _active_monitors.items(): r[pid]={"running":m.running,"started_at":m.started_at.isoformat() if m.started_at else None,"screenshot_count":m.sc,"violations_count":len(m.violations)}
    return r

def launch_background_monitor(punish_id):
    a=_load_all_status(); e=a.get(punish_id)
    if e and e.get("running") and _pid_is_running(e.get("process_id")): return {"message":"监督已在运行中。","punish_id":punish_id,"process_id":e.get("process_id"),"float_process_id":e.get("float_process_id"),"status":e}
    if e: a.pop(punish_id,None); _save_all_status(a)
    try: proc=subprocess.Popen([_python_windowed_executable(),os.path.abspath(__file__),"start",punish_id],cwd=_BASE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=_subprocess_creationflags())
    except Exception as ex: return {"error":f"启动失败: {ex}"}
    dl=time.time()+6
    while time.time()<dl:
        time.sleep(0.25); e=_load_all_status().get(punish_id)
        if e and e.get("running"): return {"message":"监督已后台启动。","punish_id":punish_id,"process_id":e.get("process_id") or proc.pid,"float_process_id":e.get("float_process_id"),"status":e}
    return {"error":"进程已拉起但状态未写入。","punish_id":punish_id,"process_id":proc.pid}

def _print_json(data): print(json.dumps(data,ensure_ascii=False,indent=2),flush=True)

if __name__=="__main__":
    if len(sys.argv)<2: print("screen_monitor <start|stop|status|status-all|float|cleanup> [id]"); sys.exit(1)
    cmd,pid=sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None
    if cmd=="start":
        if not pid: sys.exit(1)
        m = ScreenMonitor(pid)
        _print_json(m.start())
        try:
            while m.running: time.sleep(2)
            time.sleep(1)  # 缓冲一秒防主线程抢退
        except KeyboardInterrupt: pass
        finally:
            # 确保退出时清理浮窗
            m.stop()
    elif cmd=="float":
        if not pid or len(sys.argv)<4: sys.exit(1)
        started=datetime.fromisoformat(sys.argv[3]); social=len(sys.argv)>4 and sys.argv[4]=="1"
        fw=_build_floating_window_win32(pid,started,social)
        if fw: fw.run_loop()
    elif cmd=="stop":
        if not pid: sys.exit(1)
        _print_json(stop_monitoring(pid))
    elif cmd=="status":
        if not pid: sys.exit(1)
        _print_json(get_status(pid))
    elif cmd=="status-all": _print_json(list_all_status())
    elif cmd=="cleanup":
        # 清理状态文件中记录的所有监控/浮窗进程
        killed = 0
        for k, v in list(_load_all_status().items()):
            if _terminate_pid(v.get("process_id")): killed += 1
            if _terminate_pid(v.get("float_process_id")): killed += 1
        # 清空状态文件
        _save_all_status({})
        _print_json({"message": f"清理完成", "killed_processes": killed})
    else: print(f"unknown: {cmd}")
