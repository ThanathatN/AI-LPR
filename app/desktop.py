"""Desktop live view (like HCNetSDK ClientDemo) + the AI-LPR server in the same process.

    .venv\\Scripts\\python.exe -m app.desktop [--cams 1,2] [--port 8080]

Two Tk panels; the native HWND of each panel goes to PlayM4_Play(port, hwnd), so PlayCtrl decodes and
draws the live video itself (no cv2.imshow, no Python in the display path). The same PlayCtrl port also
hands LPR a snapshot on demand (PlayM4_GetBMP); LPR runs in its own threads, so however slow it is,
the live view does not stutter.

Slot 1 / slot 2 show the cameras given with --cams (camera ids from the "กล้อง & I/O" page), by default
the first two cameras. Each camera's channel (1, 2, ...) comes from its own settings — for an NVR add two
cameras with the same IP and channel 1 / channel 2.
"""
import argparse
import threading
import time
import tkinter as tk

import uvicorn

from . import db

BG, PANEL, TEXT, MUTED, OK, BAD = "#1a1b1e", "#26272b", "#e4e4e6", "#8a8b91", "#45b058", "#e5484d"


class Slot:
    def __init__(self, parent, idx, row):
        self.row = row
        self.cam_id = row["id"] if row else None
        box = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground="#36373c")
        box.grid(row=0, column=idx, sticky="nsew", padx=6, pady=6)
        parent.columnconfigure(idx, weight=1, uniform="slot")
        title = f"CH{(row or {}).get('channel') or 1} · {row['name']}" if row else f"ช่อง {idx + 1} · ยังไม่ได้กำหนดกล้อง"
        tk.Label(box, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(6, 2))
        # PlayCtrl renders straight into this widget's HWND
        # bg="" = Tk never paints this area, so it cannot flicker over the video drawn by the SDK
        self.video = tk.Frame(box, bg="", width=800, height=450)
        self.video.pack(fill="both", expand=True, padx=6)
        self.info = tk.Label(box, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 10), anchor="w", justify="left")
        self.info.pack(fill="x", padx=8, pady=(4, 2))
        self.result = tk.Label(box, text="", bg=PANEL, fg=TEXT, font=("Leelawadee UI", 14, "bold"), anchor="w")
        self.result.pack(fill="x", padx=8, pady=(0, 8))

    @property
    def hwnd(self):
        return self.video.winfo_id()


def main():
    ap = argparse.ArgumentParser(description="AI-LPR desktop live view")
    ap.add_argument("--cams", default="", help="camera ids for slot 1 and 2, e.g. 1,2")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    from . import main as server          # creates engine + camera manager (not started yet)
    server.cams.migrate()
    rows = db.query("SELECT * FROM cameras WHERE enabled=1 ORDER BY sort, id")
    if args.cams:
        by_id = {r["id"]: r for r in rows}
        picked = [by_id.get(int(x)) for x in args.cams.split(",")[:2] if x.strip()]
    else:
        picked = rows[:2]
    picked = (picked + [None, None])[:2]

    try:                                   # real pixels for PlayCtrl (no blurry Windows display scaling)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.title("AI-LPR · Live View (HCNetSDK)")
    root.configure(bg=BG)
    root.geometry("1700x620")
    grid = tk.Frame(root, bg=BG)
    grid.pack(fill="both", expand=True)
    grid.rowconfigure(0, weight=1)
    slots = [Slot(grid, i, r) for i, r in enumerate(picked)]
    status = tk.Label(root, text=f"Web: http://localhost:{args.port}", bg=BG, fg=MUTED, anchor="w", font=("Segoe UI", 9))
    status.pack(fill="x", padx=10, pady=(0, 6))
    root.update()                          # realise the widgets so their HWNDs exist

    # windows must be registered before the cameras connect (they are read in CameraUnit.connect)
    from . import hik_capture
    for s in slots:
        if s.cam_id:
            hik_capture.WINDOWS[s.cam_id] = s.hwnd

    cfg = uvicorn.Config(server.app, host=args.host, port=args.port, log_level="info", access_log=False)
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True, name="uvicorn")
    th.start()

    def unit(cam_id):
        try:
            return server.cams.units.get(cam_id)
        except Exception:
            return None

    last_dec = {}

    def tick():
        for s in slots:
            u = unit(s.cam_id) if s.cam_id else None
            if not u:
                s.info.config(text="กำลังเริ่มระบบ..." if s.cam_id else "")
                continue
            st = u.video.status()
            dec = getattr(u.video, "_dec_count", u.video.frame_id)
            d0, t0 = last_dec.get(s.cam_id, (dec, time.time() - 1))
            last_dec[s.cam_id] = (dec, time.time())
            fps = (dec - d0) / max(1e-6, time.time() - t0)
            tracks = u.rec.live_tracks()
            line = (f"{'● ออนไลน์' if st['connected'] else '○ ' + (st['error'] or 'กำลังเชื่อมต่อ')}   "
                    f"{st['width']}×{st['height']}   decode {fps:4.1f} fps   LPR {u.rec.proc_fps:4.1f} fps   "
                    f"{st.get('backend', '').upper()}{'' if st.get('display') else ' (ไม่มีหน้าต่าง)'}")
            s.info.config(text=line, fg=OK if st["connected"] else BAD)
            if tracks:
                t = tracks[-1]
                s.result.config(text=f"#{t['id']}  {t['label'] or 'กำลังอ่าน...'}", fg=TEXT)
            else:
                ev = db.one("SELECT ts, plate, province, decision FROM events WHERE camera_id=? ORDER BY id DESC LIMIT 1",
                            (s.cam_id,))
                if ev:
                    s.result.config(text=f"{ev['ts'][11:]}   {ev['plate']} {ev['province'] or ''}   "
                                         f"{'อนุญาต' if ev['decision'] == 'granted' else 'ไม่อนุญาต'}",
                                    fg=OK if ev["decision"] == "granted" else BAD)
        root.after(500, tick)

    def on_resize(_e=None):
        for s in slots:
            u = unit(s.cam_id) if s.cam_id else None
            if u and hasattr(u.video, "refresh_window"):
                u.video.refresh_window()

    def on_close():
        status.config(text="กำลังปิดระบบ...")
        root.update()
        # stop PlayCtrl drawing BEFORE the windows are destroyed
        srv.should_exit = True             # lifespan shutdown: stop cameras -> hik_capture.shutdown()
        th.join(timeout=15)
        hik_capture.shutdown()
        root.destroy()

    root.bind("<Configure>", lambda e: e.widget is root and root.after_idle(on_resize))
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(500, tick)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()


if __name__ == "__main__":
    main()
