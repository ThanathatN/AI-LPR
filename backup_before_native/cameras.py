"""Multi-camera runtime: one CameraUnit (ISAPI + RTSP + Alarm I/O + recognition) per lane camera.

The AI engine is shared by every unit; each unit has its own trigger line, detection area,
I/O roles and duplicate filter. A barrier relay or a loop-detector input can be bound to a
different camera (e.g. a loop wired into camera A that triggers reading on camera B).
"""
import copy
import json
import logging
import threading
import time

from . import db
from .camera import VideoSource
from .config import LANE_DEFAULTS
from . import hik_capture
from .hik_capture import HikSDKSource, find_sdk_dir
from .io_manager import IOManager
from .isapi import HikCamera
from .pipeline import RecognitionService

log = logging.getLogger("lpr.cameras")
CONN_FIELDS = ("name", "enabled", "host", "http_port", "rtsp_port", "username", "password", "stream",
               "source_override", "lane", "sort", "capture", "sdk_port", "channel")


def lane_merge(cfg: dict) -> dict:
    return db._merge(LANE_DEFAULTS, cfg or {})


class CameraUnit:
    def __init__(self, row: dict, engine, manager):
        self.manager = manager
        self.id = row["id"]
        self.row = row
        self._lane = lane_merge(json.loads(row.get("config") or "{}"))
        self.video = VideoSource(f"video-{self.id}")
        self.iom = IOManager(self.id, self.lane)
        self.iom.resolve_barrier = self._barrier_target
        self.iom.on_trigger = manager.route_trigger
        self.rec = RecognitionService(self, engine)
        self.cam = None
        self.device = {}

    # ------------------------------------------------------------ config
    @property
    def name(self):
        return self.row["name"]

    def lane(self) -> dict:
        return copy.deepcopy(self._lane)

    def rec_cfg(self) -> dict:
        """Global recognition settings + this camera's lane overrides."""
        g = db.get_settings()["recognition"]
        return {**g, **self._lane["recognition"], "enabled": bool(self.row["enabled"]) and self._lane["recognition"]["enabled"]}

    def update(self, row: dict):
        conn_changed = any(str(row.get(k)) != str(self.row.get(k)) for k in
                           ("host", "http_port", "rtsp_port", "username", "password", "stream", "source_override", "enabled",
                            "capture", "sdk_port", "channel"))
        self.row = row
        self._lane = lane_merge(json.loads(row.get("config") or "{}"))
        if conn_changed:
            self.connect()
        else:
            self.iom.publish()

    # ------------------------------------------------------------ lifecycle
    def _capture_kind(self) -> str:
        """'sdk' (HCNetSDK, default for Hikvision cameras) or 'rtsp' (OpenCV; also for files / test sources)."""
        r = self.row
        if r.get("source_override") or not r.get("host") or (r.get("capture") or "sdk") != "sdk":
            return "rtsp"
        if find_sdk_dir() is None:
            log.warning("camera %s: HCNetSDK DLLs not found in hiksdk/ — falling back to RTSP", self.id)
            return "rtsp"
        return "sdk"

    def _use_source(self, kind):
        """Swap the frame source object if the capture method changed (everything else only calls
        video.latest() / status(), so the rest of the program is unaffected)."""
        cls = HikSDKSource if kind == "sdk" else VideoSource
        if not isinstance(self.video, cls):
            old = self.video
            self.video = cls(f"video-{self.id}")
            self.rec.video = self.video
            old.stop()

    def connect(self):
        r = self.row
        self.cam = HikCamera(r["host"], r["username"], r["password"], r.get("http_port") or 80,
                             r.get("rtsp_port") or 554) if r.get("host") else None
        if not r["enabled"]:
            self.video.start({} if isinstance(self.video, HikSDKSource) else "")
            self.iom.attach(None)
            return
        kind = self._capture_kind()
        self._use_source(kind)
        stream = str(r.get("stream") or "101")
        if kind == "sdk":
            # stream "101" = channel 1 main, "102" = channel 1 sub (same codes as the RTSP URL)
            self.video.start({"host": r["host"], "port": int(r.get("sdk_port") or 8000),
                              "username": r["username"], "password": r["password"],
                              "channel": int(r.get("channel") or 1),
                              "stream_type": 1 if stream.endswith("2") else 0,
                              "hwnd": hik_capture.WINDOWS.get(self.id), "display_buf": 3})
        else:
            self.video.start(r.get("source_override") or (self.cam.rtsp_url(stream) if self.cam else ""))
        cam = self.cam

        def init_device():
            if cam is None:
                self.iom.attach(None)
                return
            try:
                self.device = cam.device_info()
            except Exception as e:
                self.device = {"error": str(e)}
            self.iom.attach(cam)
        threading.Thread(target=init_device, daemon=True, name=f"device-init-{self.id}").start()

    def start(self):
        self.connect()
        self.rec.start()

    def stop(self):
        self.rec.stop()
        self.video.stop()
        self.iom.stop()

    # ------------------------------------------------------------ gate
    def _barrier_target(self):
        c = self._lane["io"]
        cid = int(c.get("barrier_camera", 0) or 0)
        target = self if cid in (0, self.id) else self.manager.units.get(cid)
        return (target.iom if target else None), int(c.get("barrier_output", 0) or 0)

    def open_gate(self, output_id: int = None, source="auto", user="system", detail=""):
        iom, port = self._barrier_target()
        port = int(output_id or port or 0)
        if not port or iom is None:
            raise RuntimeError("ยังไม่ได้กำหนดช่อง Alarm Output สำหรับไม้กั้นของกล้องนี้")
        iom.pulse(port, int(self._lane["io"].get("pulse_ms", 1000)), source=source, user=user,
                  detail=f"{detail} [{self.name}]".strip())
        self.iom.counters["gate_open"] += 1
        return port, iom.cam_id

    # ------------------------------------------------------------ status
    def status(self) -> dict:
        rc = self._lane["recognition"]
        iom, rec = self.iom, self.rec
        return {
            "id": self.id, "name": self.name, "lane": self.row.get("lane", "entry"), "enabled": bool(self.row["enabled"]),
            "host": self.row.get("host", ""), "model": self.device.get("model", ""),
            "video": self.video.status(),
            "io": {"online": iom.online, "error": iom.error, "safety_hold": iom._safety_hold, "stream": iom.stream_ok,
                   "conflicts": len(iom.conflicts),
                   "inputs": {p["id"]: iom.in_state.get(p["id"], False) for p in iom.inputs},
                   "outputs": {p["id"]: iom.out_state.get(p["id"], False) for p in iom.outputs}},
            "recognition": {"enabled": rc["enabled"] and bool(self.row["enabled"]), "mode": rc["trigger_mode"],
                            "armed": rec.armed, "armed_left": max(0, round(rec.armed_until - time.time(), 1)),
                            "latency": rec.last_latency, "proc_fps": round(rec.proc_fps, 1)},
            "tracks": rec.live_tracks(),
        }


class CameraManager:
    def __init__(self, engine):
        self.engine = engine
        self.units = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------ boot
    def migrate(self):
        """First start after the multi-camera upgrade: turn the old single-camera settings into camera #1."""
        if db.one("SELECT id FROM cameras LIMIT 1"):
            return
        s = db.get_settings()
        c, r, io = s["camera"], s["recognition"], s["io"]
        roi = r.get("roi") or {"x": 0, "y": 0, "w": 1, "h": 1}
        x, y, w, h = roi.get("x", 0), roi.get("y", 0), roi.get("w", 1), roi.get("h", 1)
        rec = {k: r[k] for k in ("enabled", "trigger_mode", "trigger_line", "post_cross_wait", "arm_seconds",
                                  "process_fps", "min_plate_width") if k in r}
        rec["area"] = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        lane_io = {k: io[k] for k in ("barrier_output", "alarm_output", "pulse_ms", "inputs") if k in io}
        db.insert("cameras", {"name": c.get("name", "Gate 1"), "enabled": 1, "host": c.get("host", ""),
                              "http_port": c.get("http_port", 80), "rtsp_port": c.get("rtsp_port", 554),
                              "username": c.get("username", "admin"), "password": c.get("password", ""),
                              "stream": c.get("stream", "101"), "source_override": c.get("source_override", ""),
                              "lane": "entry", "sort": 1,
                              "config": json.dumps({"recognition": rec, "io": lane_io}, ensure_ascii=False),
                              "created_at": db.now_iso()})
        log.info("migrated single-camera settings into the cameras table")

    def start(self):
        self.migrate()
        for row in db.query("SELECT * FROM cameras ORDER BY sort, id"):
            self._spawn(row)
        threading.Thread(target=self._hint_loop, daemon=True, name="hints").start()
        threading.Thread(target=self._fps_loop, daemon=True, name="fps").start()

    def _fps_loop(self, every=5.0):
        """Print, per camera, the FPS actually decoded vs. the FPS the LPR thread processes (every 5 s)."""
        last = {}
        while True:
            time.sleep(every)
            for u in self.all():
                dec = getattr(u.video, "_dec_count", None)        # HCNetSDK: frames out of PlayCtrl
                dec = u.video.frame_id if dec is None else dec      # RTSP: frames read
                lpr = u.rec.frames_done
                d0, l0, t0 = last.get(u.id, (dec, lpr, time.time() - every))
                dt = max(1e-6, time.time() - t0)
                last[u.id] = (dec, lpr, time.time())
                if not u.row["enabled"]:
                    continue
                print(f"[FPS] {u.name} (ch{u.row.get('channel') or 1}, {u.video.status().get('backend')}): "
                      f"decode {(dec - d0) / dt:5.1f} fps | LPR {(lpr - l0) / dt:5.1f} fps", flush=True)

    def attach_window(self, cam_id: int, hwnd):
        """Desktop viewer: let PlayCtrl draw camera `cam_id` into native window `hwnd` (None = detach)."""
        if hwnd:
            hik_capture.WINDOWS[cam_id] = hwnd
        else:
            hik_capture.WINDOWS.pop(cam_id, None)
        u = self.units.get(cam_id)
        if u and isinstance(u.video, HikSDKSource):
            u.video.set_window(hwnd)

    def _spawn(self, row):
        u = CameraUnit(row, self.engine, self)
        with self._lock:
            self.units[u.id] = u
        u.start()
        return u

    # ------------------------------------------------------------ access
    def get(self, cam_id: int = 0) -> CameraUnit:
        with self._lock:
            if cam_id and cam_id in self.units:
                return self.units[cam_id]
            if not cam_id and self.units:
                return next(iter(self.units.values()))
        raise KeyError(cam_id)

    def all(self):
        with self._lock:
            return list(self.units.values())

    # ------------------------------------------------------------ CRUD
    def add(self, data: dict) -> CameraUnit:
        row = {k: data[k] for k in CONN_FIELDS if k in data}
        row.setdefault("name", "กล้องใหม่")
        row["config"] = json.dumps(data.get("config") or {}, ensure_ascii=False)
        row["created_at"] = db.now_iso()
        row.setdefault("sort", (db.one("SELECT MAX(sort) m FROM cameras")["m"] or 0) + 1)
        cid = db.insert("cameras", row)
        return self._spawn(db.one("SELECT * FROM cameras WHERE id=?", (cid,)))

    def update(self, cam_id: int, data: dict) -> CameraUnit:
        u = self.get(cam_id)
        row = {k: data[k] for k in CONN_FIELDS if k in data}
        if "password" in row and not row["password"]:
            row.pop("password")          # blank password field = keep the stored one
        if "config" in data:
            cur = json.loads(u.row.get("config") or "{}")
            row["config"] = json.dumps(db._merge(cur, data["config"]), ensure_ascii=False)
        if row:
            db.update("cameras", cam_id, row)
        u.update(db.one("SELECT * FROM cameras WHERE id=?", (cam_id,)))
        return u

    def remove(self, cam_id: int):
        with self._lock:
            u = self.units.pop(cam_id, None)
        if u:
            u.stop()
        db.execute("DELETE FROM cameras WHERE id=?", (cam_id,))

    # ------------------------------------------------------------ cross-camera
    def route_trigger(self, port_id: int, target_id: int):
        u = self.units.get(target_id)
        if u:
            u.rec.on_input_trigger(port_id)

    def _hint_loop(self):
        """Keep the OCR's registered-plate hints in sync with the vehicle list."""
        while True:
            try:
                self.engine.hint_keys = frozenset(r["plate_key"] for r in
                                                  db.query("SELECT plate_key FROM vehicles WHERE enabled=1"))
                self.engine.list_bias = float(db.get_settings()["recognition"].get("list_bias", 1.0))
            except Exception:
                log.exception("hint refresh failed")
            time.sleep(10)
