# -*- coding: utf-8 -*-
"""屏幕准心 Crosshair Overlay — 圆角无边框面板（抗锯齿重绘版）

本版改动：
1. 收起态「准心 / 已收起」分离，不再重叠
2. 面板放大（384×686），行距放松
3. 位置精度 0.1 px（原 0.01 过于精细）
4. 功能开关带滑动动画
5. 所有圆角、圆点改为 PIL 超采样图片，画布不再出现锯齿/马赛克
6. 文字改用像素字号，150% 缩放下也清晰一致
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import colorchooser

import numpy as np
from PIL import Image, ImageDraw, ImageTk

# 打包成 exe 后 __file__ 指向临时解压目录，配置必须放在 exe 旁边
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
# onedir 打包时 exe 在子文件夹里，而配置文件放在上一级：
# 若同级没有、上一级有，就用上一级，这样打包前后共用同一份配置
if not os.path.exists(os.path.join(HERE, "crosshair.json")):
    _parent = os.path.dirname(HERE)
    if os.path.exists(os.path.join(_parent, "crosshair.json")):
        HERE = _parent

CFG_PATH = os.path.join(HERE, "crosshair.json")

DEFAULT = {
    "style": "cross", "color": "#00FF66", "size": 20, "thick": 2, "gap": 7,
    "opacity": 85, "outline": True, "dx": 0.0, "dy": 0.0, "visible": True,
    "theme": "dark", "accent": 0, "panel_alpha": 0.94,
}
STEP = 0.1            # 位置步进（px）
STYLES = [("cross", "十字"), ("dot", "圆点"), ("circle", "圆环"), ("tcross", "T 形"),
          ("x", "X 形"), ("chevron", "人字"), ("square", "方框"), ("triangle", "三角")]
PRESET = ["#00FF66", "#FF3B30", "#00E5FF", "#FFD60A", "#FFFFFF", "#FF2D95", "#FF9500"]

THEMES = {
    "dark": dict(bg="#232529", card="#2e3137", fg="#edeff2", sub="#a2a8b0",
                 line="#3d424a", accent="#37d67a"),
    "light": dict(bg="#f4f5f7", card="#ffffff", fg="#1f2226", sub="#61666e",
                  line="#dcdfe4", accent="#1a73e8"),
}
ACCENTS = {
    "dark": [("#232529", "#98a2b0"), ("#182530", "#4fc3f7"), ("#241d2e", "#c08bff")],
    "light": [("#f4f5f7", "#5f6368"), ("#e9f2fb", "#0288d1"), ("#f4edfb", "#8e24aa")],
}
KEY = "#010203"
SS = 6

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x08
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x8000000
WS_POPUP = 0x80000000
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x4, 0x10, 0x40
ULW_ALPHA = 0x2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL = 0x1, 0x2
VK_H = 0x48

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


u32.DefWindowProcW.restype = LRESULT
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.CreateWindowExW.restype = wt.HWND
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u32.GetDC.restype = wt.HDC
u32.GetDC.argtypes = [wt.HWND]
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wt.UINT]
u32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.GetParent.argtypes = [wt.HWND]
u32.GetParent.restype = wt.HWND
u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
u32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT),
                                    ctypes.POINTER(wt.SIZE), wt.HDC, ctypes.POINTER(wt.POINT),
                                    wt.DWORD, ctypes.c_void_p, wt.DWORD]
u32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
u32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32.DestroyWindow.argtypes = [wt.HWND]
u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
u32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.SelectObject.restype = wt.HGDIOBJ
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.CreateDIBSection.restype = wt.HBITMAP
g32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                 ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteDC.argtypes = [wt.HDC]


# ---------------------------------------------------------------- 准心
def hex_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _shapes(d, c, cy, L, g, t, col, extra):
    style = _shapes.style
    if style in ("cross", "tcross", "x", "chevron"):
        if style == "cross":
            seg = [((c - g - L, cy), (c - g, cy)), ((c + g, cy), (c + g + L, cy)),
                   ((c, cy - g - L), (c, cy - g)), ((c, cy + g), (c, cy + g + L))]
        elif style == "tcross":
            seg = [((c - g - L, cy), (c - g, cy)), ((c + g, cy), (c + g + L, cy)),
                   ((c, cy + g), (c, cy + g + L))]
        elif style == "x":
            k = L * 0.7071
            seg = [((c - g - k, cy - g - k), (c - g, cy - g)),
                   ((c + g, cy + g), (c + g + k, cy + g + k)),
                   ((c - g - k, cy + g + k), (c - g, cy + g)),
                   ((c + g, cy - g), (c + g + k, cy - g - k))]
        else:
            seg = [((c - L, cy - L * 0.55), (c, cy + L * 0.35)),
                   ((c, cy + L * 0.35), (c + L, cy - L * 0.55))]
        for a, b in seg:
            d.line([a, b], fill=col, width=t)
        return
    if style == "dot":
        r = max(1.5, t * 0.9) + extra
        d.ellipse([c - r, cy - r, c + r, cy + r], fill=col)
        return
    if style == "circle":
        r = g + L * 0.62
        d.ellipse([c - r - extra, cy - r - extra, c + r + extra, cy + r + extra],
                  outline=col, width=t)
        return
    if style == "square":
        r = g + L * 0.55
        d.rectangle([c - r - extra, cy - r - extra, c + r + extra, cy + r + extra],
                    outline=col, width=t)
        return
    r = g + L * 0.62
    pts = [(c, cy - r), (c - r * 0.87, cy + r * 0.5), (c + r * 0.87, cy + r * 0.5)]
    d.line(pts + [pts[0]], fill=col, width=t, joint="curve")


def render(cfg, fx=0.0, fy=0.0, ss=SS, out_size=None, scale=1.0):
    size, gap = cfg["size"] * scale, cfg["gap"] * scale
    thick = max(0.6, cfg["thick"] * scale)
    reach = int(size + gap) + 6
    n = reach * 2
    W = n * ss
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cyv = W / 2 + fx * ss, W / 2 + fy * ss
    rgb = hex_rgb(cfg["color"])
    a = int(255 * cfg["opacity"] / 100)
    _shapes.style = cfg["style"]
    t = max(1, int(round(thick * ss)))
    if cfg["outline"]:
        _shapes(d, cx, cyv, size * ss, gap * ss, t + 2 * ss, (0, 0, 0, int(a * 0.78)), 1 * ss)
    _shapes(d, cx, cyv, size * ss, gap * ss, t, rgb + (a,), 0)
    target = out_size if out_size else n
    return img.resize((target, target), Image.LANCZOS)


def to_premultiplied(img):
    a = np.array(img, dtype=np.uint16)
    alpha = a[:, :, 3:4]
    rgb = (a[:, :, :3] * alpha // 255).astype(np.uint8)
    out = np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], a[:, :, 3].astype(np.uint8)])
    return np.ascontiguousarray(out)


# ---------------------------------------------------------------- 覆盖层
class Overlay(threading.Thread):
    def __init__(self, cmdq, evtq):
        super().__init__(daemon=True)
        self.cmdq = cmdq          # 面板 -> 覆盖层
        self.evtq = evtq          # 覆盖层 -> 面板（热键等事件）
        self.cfg = dict(DEFAULT)
        self.hwnd = None
        self._alive = True
        self._last = 240
        self._sig = None
        self._fx = None
        self._fy = None
        self._proc = WNDPROC(lambda h, m, w, l: u32.DefWindowProcW(h, m, w, l))

    def run(self):
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        cls = "ScreenCrosshairOverlay3"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        u32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.hwnd = u32.CreateWindowExW(ex, cls, "crosshair", WS_POPUP,
                                        0, 0, 240, 240, None, None, hinst, None)
        u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self._apply()
        u32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, VK_H)

        msg = wt.MSG()
        tick = 0
        while self._alive:
            while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY:
                    self.evtq.put(("hotkey", 1))
                else:
                    u32.TranslateMessage(ctypes.byref(msg))
                    u32.DispatchMessageW(ctypes.byref(msg))
            try:
                while True:
                    kind, payload = self.cmdq.get_nowait()
                    if kind == "cfg":
                        self.cfg = payload
                        self._apply()
                    elif kind == "quit":
                        self._alive = False
            except queue.Empty:
                pass
            tick += 1
            if tick % 200 == 0:
                self._position()
            ctypes.windll.kernel32.Sleep(10)

        u32.UnregisterHotKey(None, 1)
        u32.DestroyWindow(self.hwnd)

    def _geometry(self, n):
        sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        xe = sw / 2 - n / 2 + float(self.cfg["dx"])
        ye = sh / 2 - n / 2 + float(self.cfg["dy"])
        xi, yi = int(round(xe)), int(round(ye))
        return xi, yi, xe - xi, ye - yi

    def _position(self):
        if not self.cfg.get("visible", True):
            return                      # 隐藏时不要动窗口，否则会被 SWP_SHOWWINDOW 顶回来
        n = self._last
        x, y, _, _ = self._geometry(n)
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, n, n, SWP_NOACTIVATE)

    def _apply(self):
        sig = tuple(self.cfg.get(k) for k in
                    ("style", "color", "size", "thick", "gap", "opacity", "outline"))
        n = (int(self.cfg["size"] + self.cfg["gap"]) + 6) * 2
        self._last = n
        x, y, fx, fy = self._geometry(n)
        if sig != self._sig or fx != self._fx or fy != self._fy:
            self._sig, self._fx, self._fy = sig, fx, fy
            buf = to_premultiplied(render(self.cfg, fx, fy))
            hdc = u32.GetDC(None)
            mem = g32.CreateCompatibleDC(hdc)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = n
            bmi.bmiHeader.biHeight = -n
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            hbmp = g32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            raw = buf.tobytes()
            ctypes.memmove(bits, raw, len(raw))
            old = g32.SelectObject(mem, hbmp)
            size = wt.SIZE(n, n)
            src, dst = wt.POINT(0, 0), wt.POINT(x, y)
            bf = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            u32.UpdateLayeredWindow(self.hwnd, hdc, ctypes.byref(dst), ctypes.byref(size),
                                    mem, ctypes.byref(src), 0, ctypes.byref(bf), ULW_ALPHA)
            g32.SelectObject(mem, old)
            g32.DeleteObject(hbmp)
            g32.DeleteDC(mem)
            u32.ReleaseDC(None, hdc)
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, n, n, SWP_NOACTIVATE)
        u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE if self.cfg.get("visible", True) else 0)


# ---------------------------------------------------------------- 抗锯齿图元
def rounded_image(w, h, r, fill, outline=None, bw=1, ss=4, hard=False):
    """生成圆角矩形图片。

    hard=True 用于**贴着窗口边界的背景**：
    色键透明（colorkey）无法处理半透明像素——边缘的抗锯齿像素会和近黑色的
    色键混合出 (15,16,18) 这类颜色，既不是面板色也不等于色键，于是删不掉，
    在浅色主题下就表现为四个圆角上的黑色碎点。
    所以边界背景必须只保留 alpha=0 / 255 两种像素，由 colorkey 干净切开。
    """
    if hard:
        W, H = max(1, int(w)), max(1, int(h))
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=fill,
                            outline=outline, width=max(1, int(bw)))
        # 兜底：把任何中间 alpha 二值化，确保没有半透明像素漏到窗口边界
        alpha = im.split()[3].point(lambda v: 255 if v >= 128 else 0)
        im.putalpha(alpha)
        return im

    if w * h > 40000:          # 大尺寸（如整块面板背景）降采样率，避免生成耗时
        ss = 2
    W, H = max(1, int(w * ss)), max(1, int(h * ss))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r * ss, fill=fill,
                        outline=outline, width=max(1, int(bw * ss)))
    return im.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)


def circle_image(dia, fill, outline=None, bw=1, ss=4):
    W = max(1, int(dia * ss))
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, W - 1, W - 1], fill=fill, outline=outline, width=max(1, int(bw * ss)))
    return im.resize((max(1, int(dia)), max(1, int(dia))), Image.LANCZOS)


# ---------------------------------------------------------------- 面板
class Panel:
    W, H = 384, 686
    CW, CH = 232, 46

    def __init__(self, root):
        self.root = root
        self.cfg = dict(DEFAULT)
        if os.path.exists(CFG_PATH):
            try:
                with open(CFG_PATH, "r", encoding="utf-8") as f:
                    self.cfg.update(json.load(f))
            except Exception:
                pass
        self.cfg["dx"] = float(self.cfg["dx"])
        self.cfg["dy"] = float(self.cfg["dy"])
        self.cmdq = queue.Queue()
        self.evtq = queue.Queue()
        self.overlay = Overlay(self.cmdq, self.evtq)
        self.overlay.start()

        self.drag = None
        self.moving = None
        self.collapsed = False
        self._fading = False
        self._imgs = {}
        self._icons = {}
        self._refs = []
        self._anim_tok = {}
        self._push_job = None
        self._last_pushed = None
        self.sl_items = {}
        self.tile_items = {}
        self.sw_items = {}
        self.pos_items = {}
        self.toggle_items = {}
        self.regions = []

        self.px = int(self.cfg.pop("_px", u32.GetSystemMetrics(0) - self.W - 40))
        self.py = int(self.cfg.pop("_py", 90))

        root.overrideredirect(True)
        root.configure(bg=KEY)
        root.attributes("-transparentcolor", KEY)
        root.attributes("-topmost", True)
        root.geometry(f"{self.W}x{self.H}")
        self.cv = tk.Canvas(root, width=self.W, height=self.H, bg=KEY,
                            highlightthickness=0, bd=0)
        self.cv.pack()
        self.cv.bind("<Button-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_motion)
        self.cv.bind("<ButtonRelease-1>", self.on_release)

        self.f_title = ("Microsoft YaHei UI", -21, "bold")
        self.f_label = ("Microsoft YaHei UI", -15)
        self.f_val = ("Microsoft YaHei UI", -15)
        self.f_small = ("Microsoft YaHei UI", -13)

        root.update_idletasks()
        self.hwnd = self._top_hwnd()
        self.place(self.px, self.py, self.W, self.H)
        self.build()
        self.push()
        root.after(60, self.pump)

    # ---------- 窗口 ----------
    def _set_alpha(self, a):
        """设置面板不透明度。

        Windows 下 Tk 的 -alpha 走 SetLayeredWindowAttributes(LWA_ALPHA)，
        会把之前设的 colorkey 清掉，导致圆角外的 KEY 色露出来变成黑角。
        所以每次改 alpha 之后都必须重新设一次 -transparentcolor。
        """
        try:
            a = float(a)
        except (TypeError, ValueError):
            a = 1.0
        a = max(0.05, min(1.0, a))
        self.root.attributes("-alpha", a)
        self.root.attributes("-transparentcolor", KEY)

    def _top_hwnd(self):
        WS_CHILD = 0x40000000
        h = self.root.winfo_id()
        for _ in range(4):
            if not h:
                break
            if not (u32.GetWindowLongW(h, -16) & WS_CHILD):
                return h
            h = u32.GetParent(h)
        return self.root.winfo_id()

    def place(self, x, y, w, h):
        u32.SetWindowPos(self.hwnd, 0, int(x), int(y), int(w), int(h),
                         SWP_NOZORDER | SWP_NOACTIVATE)
        self.px, self.py = int(x), int(y)

    # ---------- 素材 ----------
    def img_rrect(self, w, h, r, fill, outline=None, bw=1, hard=False):
        key = ("r", round(w, 1), round(h, 1), round(r, 1), fill, outline, bw, hard)
        im = self._imgs.get(key)
        if im is None:
            if len(self._imgs) > 800:
                self._imgs.clear()
            im = ImageTk.PhotoImage(
                rounded_image(w, h, r, fill, outline, bw, hard=hard))
            self._imgs[key] = im
        return im

    def img_circle(self, d, fill, outline=None, bw=1):
        key = ("c", round(d, 1), fill, outline, bw)
        im = self._imgs.get(key)
        if im is None:
            if len(self._imgs) > 800:
                self._imgs.clear()
            im = ImageTk.PhotoImage(circle_image(d, fill, outline, bw))
            self._imgs[key] = im
        return im

    def _icon(self, key):
        ck = (key, self.cfg["color"])
        im = self._icons.get(ck)
        if im is None:
            cfg = dict(self.cfg)
            cfg.update({"style": key, "size": 13, "gap": 5, "thick": 3, "opacity": 100})
            im = ImageTk.PhotoImage(render(cfg, ss=5, out_size=34))
            self._icons[ck] = im
        return im

    def th(self):
        base = dict(THEMES[self.cfg["theme"]])
        base["bg"] = ACCENTS[self.cfg["theme"]][self.cfg["accent"] % 3][0]
        return base

    # ---------- 配置下发（16ms 合并；淡入淡出期间暂缓） ----------
    def push(self):
        if self._fading:
            return
        if self._push_job is None:
            self._push_job = self.root.after(16, self._flush)

    def _flush(self):
        self._push_job = None
        cfg = dict(self.cfg)
        if cfg == self._last_pushed:
            return
        self._last_pushed = cfg
        self.cmdq.put(("cfg", cfg))

    def pump(self):
        try:
            while True:
                kind, _ = self.evtq.get_nowait()
                if kind == "hotkey":
                    self.cfg["visible"] = not self.cfg.get("visible", True)
                    self._animate_toggle("visible", self.cfg["visible"])
                    self.push()
        except queue.Empty:
            pass
        self.root.after(60, self.pump)

    # ---------- 构建界面（仅在主题/色调变化或首次） ----------
    def build(self):
        cv, t = self.cv, self.th()
        cv.delete("all")
        self.regions = []
        self.sl_items = {}
        self.tile_items = {}
        self.sw_items = {}
        self.pos_items = {}
        self.toggle_items = {}
        cv.configure(bg=KEY)
        if not self._fading:
            self._set_alpha(self.cfg["panel_alpha"])

        if self.collapsed:
            self._draw_collapsed(t)
            return

        cv.create_image(0, 0, anchor="nw",
                        image=self.img_rrect(self.W, self.H, 20, t["bg"], t["line"], 1,
                                             hard=True))
        cv.create_text(24, 34, text="准心", anchor="w", fill=t["fg"], font=self.f_title)
        self.rbutton(self.W - 74, 20, 24, 24, "—", self.collapse, t)
        self.rbutton(self.W - 44, 20, 24, 24, "×", self.quit, t)

        cv.create_text(24, 76, text="样式", anchor="w", fill=t["sub"], font=self.f_small)
        x = 22
        for key, _ in STYLES:
            self.style_tile(x, 92, 42, key, self.cfg["style"] == key, t)
            x += 44

        cv.create_text(24, 154, text="颜色", anchor="w", fill=t["sub"], font=self.f_small)
        x = 26
        for hexv in PRESET:
            self.swatch(x, 172, hexv, t)
            x += 34
        self.rbutton(268, 161, 62, 26, "自定义", self.pick_color, t, 13)

        y = 216
        for label, key, lo, hi, isf in (("尺寸", "size", 4, 60, False),
                                        ("线宽", "thick", 1, 10, False),
                                        ("间隙", "gap", 0, 30, False),
                                        ("准心不透明度", "opacity", 15, 100, False),
                                        ("面板不透明度", "panel_alpha", 30, 100, True)):
            self.slider(24, y, label, key, lo, hi, isf, t)
            y += 46

        ypos = 468
        cv.create_text(24, ypos - 30, text="位置（步进 %.1f px）" % STEP,
                       anchor="w", fill=t["sub"], font=self.f_small)
        self.pos_row(24, ypos, "X", "dx", t)
        self.pos_row(206, ypos, "Y", "dy", t)
        self.rbutton(24, ypos + 36, 70, 26, "归中", self.center, t, 13)

        self.toggle(24, 566, "描边", "outline", t)
        self.toggle(200, 566, "显示准心", "visible", t)

        cv.create_text(24, 620, text="主题", anchor="w", fill=t["sub"], font=self.f_small)
        for i, (val, name) in enumerate((("dark", "深色"), ("light", "浅色"))):
            self.pill(74 + i * 62, 606, 56, 28, name, self.cfg["theme"] == val,
                      (lambda v=val: self.set_theme(v)), t)
        cv.create_text(212, 620, text="色调", anchor="w", fill=t["sub"], font=self.f_small)
        for i in range(3):
            cx = 254 + i * 34
            chip = ACCENTS[self.cfg["theme"]][i][1]
            sel = self.cfg["accent"] == i
            cv.create_image(cx, 606, anchor="nw",
                            image=self.img_circle(22, chip,
                                                  t["fg"] if sel else t["line"],
                                                  3 if sel else 1))
            self.regions.append((cx - 3, 603, cx + 25, 631,
                                 (lambda k=i: self.set_accent(k)), None))

        cv.create_text(24, self.H - 22, text="Ctrl + Alt + H 　显示 / 隐藏准心",
                       anchor="w", fill=t["sub"], font=self.f_small)
        self.push()

    redraw = build

    def _draw_collapsed(self, t):
        cv, W, H = self.cv, self.CW, self.CH
        cv.create_image(0, 0, anchor="nw",
                        image=self.img_rrect(W, H, 14, t["bg"], t["line"], 1,
                                             hard=True))
        cv.create_text(18, H / 2, text="准心", anchor="w", fill=t["fg"], font=self.f_title)
        cv.create_text(96, H / 2, text="已收起", anchor="w", fill=t["sub"], font=self.f_small)
        self.rbutton(W - 72, 10, 26, 26, "▴", self.expand, t)
        self.rbutton(W - 40, 10, 26, 26, "×", self.quit, t)
        self.regions.append((0, 0, W - 78, H, self.expand, None))
        self.push()

    # ---------- 控件 ----------
    def style_tile(self, x, y, s, key, sel, t):
        img = self.cv.create_image(x, y, anchor="nw",
                                   image=self.img_rrect(s, s, 11,
                                                        t["accent"] if sel else t["card"],
                                                        t["accent"] if sel else t["line"], 1))
        icon = self.cv.create_image(x + s / 2, y + s / 2, image=self._icon(key))
        self.tile_items[key] = [img, icon, x, y, s]
        self.regions.append((x, y, x + s, y + s, (lambda k=key: self.set_style(k)), None))

    def swatch(self, x, y, hexv, t):
        sel = self.cfg["color"].lower() == hexv.lower()
        img = self.cv.create_image(x, y, anchor="nw",
                                   image=self.img_circle(22, hexv,
                                                         t["fg"] if sel else t["line"],
                                                         2 if sel else 1))
        self.sw_items[hexv] = [img, x, y]
        self.regions.append((x - 3, y - 3, x + 25, y + 25,
                             (lambda h=hexv: self.set_color(h)), None))

    def rbutton(self, x, y, w, h, text, cmd, t, fs=15):
        self.cv.create_image(x, y, anchor="nw",
                             image=self.img_rrect(w, h, min(h / 2, 10), t["card"], t["line"], 1))
        self.cv.create_text(x + w / 2, y + h / 2, text=text, fill=t["fg"],
                            font=("Microsoft YaHei UI", -fs))
        self.regions.append((x, y, x + w, y + h, cmd, None))

    def pill(self, x, y, w, h, text, sel, cmd, t):
        self.cv.create_image(x, y, anchor="nw",
                             image=self.img_rrect(w, h, h / 2,
                                                  t["accent"] if sel else t["card"],
                                                  t["accent"] if sel else t["line"], 1))
        fg = "#0d1211" if (sel and self.cfg["theme"] == "dark") else (
            "#ffffff" if sel else t["fg"])
        self.cv.create_text(x + w / 2, y + h / 2, text=text, fill=fg, font=self.f_val)
        self.regions.append((x, y, x + w, y + h, cmd, None))

    def toggle(self, x, y, label, key, t):
        on = bool(self.cfg.get(key, True))
        w, h, kd = 44, 24, 20
        track = self.cv.create_image(x, y, anchor="nw",
                                     image=self.img_rrect(w, h, h / 2,
                                                          t["accent"] if on else t["line"]))
        kx = x + (w - kd - 2) if on else x + 2
        knob = self.cv.create_image(kx, y + 2, anchor="nw",
                                    image=self.img_circle(kd, "#ffffff"))
        self.cv.create_text(x + w + 10, y + h / 2, text=label, anchor="w",
                            fill=t["fg"], font=self.f_val)
        self.toggle_items[key] = [track, knob, x, y, w, kd]
        self.regions.append((x, y, x + w + 10 + 68, y + h,
                             (lambda k=key: self.toggle_key(k)), None))

    def slider(self, x, y, label, key, lo, hi, isf, t):
        cv = self.cv
        cv.create_text(x, y + 8, text=label, anchor="w", fill=t["fg"], font=self.f_label)
        x0, x1, yy = x + 108, x + 296, y + 9
        cv.create_image(x0 - 4, yy - 4, anchor="nw",
                        image=self.img_rrect(x1 - x0 + 8, 8, 4, t["line"]))
        fill = cv.create_line(x0, yy, x0 + 1, yy, width=8, capstyle="round", fill=t["accent"])
        knob = cv.create_image(x0 - 9, yy - 9, anchor="nw",
                               image=self.img_circle(18, t["card"], t["accent"], 2))
        txt = cv.create_text(x + 310, yy, text="", anchor="w", fill=t["fg"], font=self.f_val)
        self.sl_items[key] = [fill, knob, txt, x0, x1, lo, hi, isf, yy]
        self.regions.append(("slider", key, x0, x1, lo, hi, isf, yy))
        self._update_slider(key)

    def _update_slider(self, key):
        fill, knob, txt, x0, x1, lo, hi, isf, yy = self.sl_items[key]
        val = float(self.cfg[key])
        base = lo / 100.0 if isf else lo
        span = (hi - lo) / 100.0 if isf else (hi - lo)
        p = max(0.0, min(1.0, (val - base) / span))
        kx = x0 + (x1 - x0) * p
        self.cv.coords(fill, x0, yy, max(x0 + 0.5, kx), yy)
        self.cv.coords(knob, kx - 9, yy - 9)
        self.cv.itemconfig(txt, text=(f"{val*100:.0f}%" if isf else f"{int(val)}"))

    def pos_row(self, x, y, label, key, t):
        cv = self.cv
        cv.create_text(x, y + 12, text=label, anchor="w", fill=t["sub"], font=self.f_label)
        cv.create_image(x + 18, y, anchor="nw",
                        image=self.img_rrect(84, 26, 7, t["card"], t["line"], 1))
        tid = cv.create_text(x + 60, y + 13, text=f"{float(self.cfg[key]):+.1f}",
                             fill=t["fg"], font=self.f_val)
        self.pos_items[key] = tid
        self.rbutton(x + 108, y, 26, 26, "−", (lambda k=key: self.step(k, -STEP)), t, 16)
        self.rbutton(x + 138, y, 26, 26, "+", (lambda k=key: self.step(k, +STEP)), t, 16)

    # ---------- 交互 ----------
    def on_press(self, e):
        x, y = e.x, e.y
        for r in self.regions:
            if r[0] == "slider":
                _, key, x0, x1, lo, hi, isf, yy = r
                if abs(y - yy) <= 14 and x0 - 14 <= x <= x1 + 14:
                    self.moving = (key, x0, x1, lo, hi, isf)
                    self.apply_slider(x)
                    return
                continue
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                if r[4]:
                    r[4]()
                return
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        rc = wt.RECT()
        u32.GetWindowRect(self.hwnd, ctypes.byref(rc))
        self.drag = (pt.x - rc.left, pt.y - rc.top)
        self.root.after(8, self._drag_tick)

    def _drag_tick(self):
        if not self.drag:
            return
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        ox, oy = self.drag
        nx, ny = pt.x - ox, pt.y - oy
        u32.SetWindowPos(self.hwnd, 0, nx, ny, 0, 0,
                         SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        self.px, self.py = nx, ny
        self.root.after(8, self._drag_tick)

    def on_motion(self, e):
        if self.moving:
            self.apply_slider(e.x)

    def on_release(self, e):
        self.moving = None
        self.drag = None

    def apply_slider(self, x):
        key, x0, x1, lo, hi, isf = self.moving
        p = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
        if isf:
            self.cfg[key] = round((lo + (hi - lo) * p) / 100, 2)
        else:
            self.cfg[key] = int(round(lo + (hi - lo) * p))
        self._update_slider(key)
        if key == "panel_alpha" and not self._fading:
            self._set_alpha(self.cfg["panel_alpha"])
        self.push()

    def step(self, key, delta):
        self.cfg[key] = round(float(self.cfg[key]) + delta, 1)
        self.cv.itemconfig(self.pos_items[key], text=f"{self.cfg[key]:+.1f}")
        self.push()

    def center(self):
        self.cfg["dx"] = self.cfg["dy"] = 0.0
        for k in ("dx", "dy"):
            self.cv.itemconfig(self.pos_items[k], text=f"{0.0:+.1f}")
        self.push()

    def _paint_tile(self, key, sel):
        it = self.tile_items.get(key)
        if not it:
            return
        img, _, _, _, s = it
        t = self.th()
        self.cv.itemconfig(img, image=self.img_rrect(
            s, s, 11, t["accent"] if sel else t["card"],
            t["accent"] if sel else t["line"], 1))

    def set_style(self, k):
        old = self.cfg["style"]
        if k == old:
            return
        self.cfg["style"] = k
        self._paint_tile(old, False)
        self._paint_tile(k, True)
        self.push()

    def _paint_swatch(self, hexv, sel):
        it = self.sw_items.get(hexv) or self.sw_items.get(str(hexv).upper())
        if not it:
            return
        img, _, _ = it
        t = self.th()
        self.cv.itemconfig(img, image=self.img_circle(
            22, hexv, t["fg"] if sel else t["line"], 2 if sel else 1))

    def set_color(self, h):
        old = self.cfg["color"]
        if h.lower() == old.lower():
            return
        self.cfg["color"] = h
        self._paint_swatch(old, False)
        self._paint_swatch(h, True)
        for key, it in self.tile_items.items():
            self.cv.itemconfig(it[1], image=self._icon(key))
        self.push()

    def pick_color(self):
        c = colorchooser.askcolor(color=self.cfg["color"], parent=self.root)[1]
        if c:
            self.set_color(c.upper())

    def toggle_key(self, key):
        self.cfg[key] = not bool(self.cfg.get(key, True))
        self._animate_toggle(key, self.cfg[key])
        self.push()

    def _animate_toggle(self, key, new):
        it = self.toggle_items.get(key)
        if not it:
            return
        track, knob, x, y, w, kd = it
        t = self.th()
        tok = self._anim_tok.get(key, 0) + 1
        self._anim_tok[key] = tok
        img_off = self.img_rrect(w, 24, 12, t["line"])
        img_on = self.img_rrect(w, 24, 12, t["accent"])
        start, end = (0.0, 1.0) if new else (1.0, 0.0)
        steps = 9

        def frame(i, switched=False):
            if self._anim_tok.get(key) != tok:
                return
            p = i / float(steps)
            e = 1 - (1 - p) ** 3
            pos = start + (end - start) * e
            self.cv.coords(knob, x + 2 + (w - kd - 4) * pos, y + 2)
            if not switched and p >= 0.5:
                self.cv.itemconfig(track, image=(img_on if new else img_off))
                switched = True
            if i < steps:
                self.root.after(14, lambda: frame(i + 1, switched))

        frame(0)

    # ---------- 主题淡入淡出 ----------
    def _fade(self, on_mid):
        target = float(self.cfg["panel_alpha"])
        span = max(0.18, target - 0.30)
        lo = target - span
        steps = 8
        gap = 16
        self._fading = True

        def out(i):
            self._set_alpha(target - span * i / steps)
            if i < steps:
                self.root.after(gap, lambda: out(i + 1))
            else:
                on_mid()                      # 在最暗处换肤，避免中途重建的顿挫被看到
                self._fading = False
                self.push()                   # 动画结束后再下发一次配置
                inn(0)

        def inn(i):
            self._set_alpha(lo + span * i / steps)
            if i < steps:
                self.root.after(gap, lambda: inn(i + 1))
            else:
                self._set_alpha(target)

        out(0)

    def _prewarm(self):
        """提前生成新主题的图片，避免在淡出最暗处做重活造成顿挫。"""
        t = self.th()
        self.img_rrect(self.W, self.H, 20, t["bg"], t["line"], 1)
        self.img_rrect(42, 42, 11, t["accent"], t["accent"], 1)
        self.img_rrect(42, 42, 11, t["card"], t["line"], 1)
        self.img_rrect(44, 24, 12, t["accent"])
        self.img_rrect(44, 24, 12, t["line"])
        self.img_rrect(56, 28, 14, t["accent"], t["accent"], 1)
        self.img_rrect(56, 28, 14, t["card"], t["line"], 1)
        self.img_circle(18, t["card"], t["accent"], 2)
        self.img_circle(22, "#ffffff")

    def set_theme(self, v):
        if v == self.cfg["theme"]:
            return
        self.cfg["theme"] = v
        self._prewarm()
        self._fade(self.build)

    def set_accent(self, i):
        if i == self.cfg["accent"]:
            return
        self.cfg["accent"] = i
        self._prewarm()
        self._fade(self.build)

    # ---------- 收起 / 展开 / 保存 ----------
    def collapse(self):
        self.collapsed = True
        self.cv.configure(width=self.CW, height=self.CH)
        self.place(self.px, self.py, self.CW, self.CH)
        self.build()

    def expand(self):
        self.collapsed = False
        self.cv.configure(width=self.W, height=self.H)
        self.place(self.px, self.py, self.W, self.H)
        self.build()

    def save(self):
        data = dict(self.cfg)
        data["_px"], data["_py"] = self.px, self.py
        try:
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            pass  # 窗口化运行时无 stdout，不能 print

    def quit(self):
        self.save()
        self.cmdq.put(("quit", None))
        self.root.destroy()


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
