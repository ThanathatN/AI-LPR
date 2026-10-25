"""Recognition service of ONE camera (lane).

  detection thread  : frame -> YOLO plate boxes (inside the detection area) -> vehicle tracks
  OCR worker thread : picks the freshest crop of each live track -> Thai OCR -> track.reads
  decision          : multi-frame CTC consensus -> access rules -> gate + evidence log

Trigger modes (same idea as dedicated entrance ANPR cameras such as Hikvision DS-TCG406-E)
  line  : a track is reported when its plate crosses the virtual trigger line (optionally one direction)
  video : a track is reported as soon as its consensus is confident (optional approach/leave filter)
  input : like video, but only while armed by a loop-detector Alarm Input
  mixed : line crossing OR loop-detector input, whichever comes first
"""
import base64
import itertools
import logging
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from . import access, db
from .bus import bus
from .config import PLATE_DIR, SNAP_DIR
from .lpr import dataset, thai
from .lpr.engine import PlateRead, consensus

log = logging.getLogger("lpr.pipeline")

# BGR
GREEN, RED, AMBER, WHITE, LINE_C, AREA_C = (58, 196, 82), (60, 60, 230), (20, 150, 250), (235, 235, 235), (40, 60, 230), (230, 200, 60)
TRACK_TTL = 1.6          # seconds without a detection before a track ends
MAX_READS = 8            # OCR reads kept per track
DIR_TH = {"in": "เข้า", "out": "ออก"}


class Track:
    _ids = itertools.count(1)

    def __init__(self, bbox, conf, now, center):
        self.id = next(self._ids)
        self.bbox, self.det_conf = bbox, conf
        self.center = center              # normalised (x, y)
        self.side = None                  # side of the trigger line
        self.first_ts = self.last_ts = now
        self.path = [(center[0], center[1], bbox[2] - bbox[0])]   # (cx, cy, width) history
        self.reads = []                   # [PlateRead]
        self.frames = {}                  # id(read) -> frame
        self.pending = None               # (crop, bbox, det_conf, frame)
        self.last_ocr = 0.0
        self.crossed_ts = 0.0
        self.crossed_dir = ""             # forward | reverse | input
        self.emitted = False
        self.label = ""
        self.result = ""                  # granted / denied / dup / skip after emission

    def motion(self):
        """approach (towards the camera: moving down / growing) | leave | '' (not enough movement)."""
        if len(self.path) < 3:
            return ""
        (x0, y0, w0), (x1, y1, w1) = self.path[0], self.path[-1]
        grow = (w1 - w0) / max(1.0, w0)
        if y1 - y0 > 0.03 or grow > 0.15:
            return "approach"
        if y0 - y1 > 0.03 or grow < -0.15:
            return "leave"
        return ""


class RecognitionService:
    def __init__(self, unit, engine):
        self.unit, self.engine = unit, engine
        self.video, self.io = unit.video, unit.iom
        self.armed_until = 0.0
        self.trigger_until = 0.0          # mixed mode: loop fired before any plate was tracked
        self.tracks = {}
        self.last_emitted = {}            # key -> ts
        self.proc_fps = 0.0
        self.frames_done = 0              # frames the LPR detection thread has processed (FPS counter)
        self.last_latency = 0
        self._tlock = threading.RLock()
        self._live_cache = (None, -1, b"")
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._detect_loop, daemon=True, name=f"detect-{self.unit.id}").start()
        threading.Thread(target=self._ocr_loop, daemon=True, name=f"ocr-{self.unit.id}").start()

    def stop(self):
        self._stop.set()

    def cfg(self):
        return self.unit.rec_cfg()

    def _pub(self, kind, data):
        bus.publish(kind, {"cam": self.unit.id, **data})

    # ------------------------------------------------------------ trigger
    def on_input_trigger(self, port_id):
        c = self.cfg()
        mode = c.get("trigger_mode")
        if mode == "input":
            self.arm(c.get("arm_seconds", 6), f"IN{port_id}")
        elif mode == "mixed":
            now = time.time()
            self.trigger_until = now + float(c.get("arm_seconds", 6))
            with self._tlock:
                for t in self.tracks.values():
                    if not t.crossed_ts and not t.emitted:
                        t.crossed_ts, t.crossed_dir = now, "input"
            self._pub("armed", {"until": self.trigger_until, "source": f"IN{port_id}", "seconds": c.get("arm_seconds", 6)})

    def arm(self, seconds: float, source="manual"):
        self.armed_until = time.time() + float(seconds)
        self._pub("armed", {"until": self.armed_until, "source": source, "seconds": seconds})

    @property
    def armed(self):
        return self.cfg().get("trigger_mode") != "input" or time.time() < self.armed_until

    # ------------------------------------------------------------ geometry
    @staticmethod
    def _area(c):
        a = c.get("area") or [[0, 0], [1, 0], [1, 1], [0, 1]]
        return np.array(a, np.float32) if len(a) >= 3 else np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)

    def _roi(self, frame, c):
        """Crop to the bounding box of the detection area (+ margin) — the detector only sees the lane."""
        h, w = frame.shape[:2]
        a = self._area(c)
        x1, y1 = max(0.0, a[:, 0].min() - 0.03), max(0.0, a[:, 1].min() - 0.03)
        x2, y2 = min(1.0, a[:, 0].max() + 0.03), min(1.0, a[:, 1].max() + 0.03)
        x, y = int(x1 * w), int(y1 * h)
        rw, rh = max(32, int((x2 - x1) * w)), max(32, int((y2 - y1) * h))
        x, y = max(0, min(x, w - 32)), max(0, min(y, h - 32))
        return frame[y:y + rh, x:x + rw], (x, y)

    @staticmethod
    def _inside(area, p):
        return cv2.pointPolygonTest(area, (float(p[0]), float(p[1])), False) >= 0

    @staticmethod
    def _side(line, p):
        (x1, y1), (x2, y2) = (line["x1"], line["y1"]), (line["x2"], line["y2"])
        v = (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1)
        return 1 if v > 0 else -1 if v < 0 else 0

    @staticmethod
    def _within_segment(line, p, margin=0.08):
        x1, y1, x2, y2 = line["x1"], line["y1"], line["x2"], line["y2"]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy or 1e-9
        t = ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2
        return -margin <= t <= 1 + margin

    # ------------------------------------------------------------ detection + tracking
    def _detect_loop(self):
        last_fid, n, t0 = -1, 0, time.time()
        while not self._stop.is_set():
            c = self.cfg()
            if not c.get("enabled", True) or self.engine.status != "ready":
                time.sleep(0.3)
                continue
            now = time.time()
            if not self.armed:
                self._expire(c, now)
                time.sleep(0.05)
                continue
            frame, fid, _ = self.video.latest()
            if frame is None or fid == last_fid:
                time.sleep(0.01)
                continue
            last_fid = fid
            started = time.time()
            try:
                sub, (ox, oy) = self._roi(frame, c)
                dets = self.engine.detect(sub, c.get("min_det_conf", 0.4))
            except Exception:
                log.exception("detection failed")
                time.sleep(0.5)
                continue
            H, W = frame.shape[:2]
            now = time.time()
            area, max_w = self._area(c), float(c.get("max_plate_width", 900))
            boxes = []
            for b, conf in dets:
                bb = (b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy)
                if bb[2] - bb[0] > max_w:
                    continue
                if self._inside(area, ((bb[0] + bb[2]) / 2 / W, (bb[1] + bb[3]) / 2 / H)):
                    boxes.append((bb, conf))
            self._update_tracks(boxes, frame, W, H, c, now)
            self._expire(c, now)
            n += 1
            self.frames_done += 1
            if now - t0 >= 2:
                self.proc_fps, n, t0 = n / (now - t0), 0, now
            period = 1.0 / max(1, float(c.get("process_fps", 8)))
            spent = time.time() - started
            if spent < period:
                time.sleep(period - spent)
        self.proc_fps = 0.0

    def _update_tracks(self, boxes, frame, W, H, c, now):
        line = c.get("trigger_line") or {}
        mode = c.get("trigger_mode")
        uses_line = mode in ("line", "mixed") and line
        min_w = c.get("min_plate_width", 60)
        with self._tlock:
            alive = [t for t in self.tracks.values() if now - t.last_ts < TRACK_TTL]
            used = set()
            for bbox, conf in sorted(boxes, key=lambda b: -b[1]):
                cx, cy = (bbox[0] + bbox[2]) / 2 / W, (bbox[1] + bbox[3]) / 2 / H
                best, bd = None, 0.14
                for t in alive:
                    if t.id in used:
                        continue
                    d = ((t.center[0] - cx) ** 2 + (t.center[1] - cy) ** 2) ** 0.5
                    if d < bd:
                        best, bd = t, d
                if best is None:
                    best = Track(bbox, conf, now, (cx, cy))
                    self.tracks[best.id] = best
                    if line:
                        best.side = self._side(line, (cx, cy))
                    if mode == "mixed" and now < self.trigger_until:      # loop already active
                        best.crossed_ts, best.crossed_dir = now, "input"
                used.add(best.id)
                best.bbox, best.det_conf, best.last_ts = bbox, conf, now
                best.path = (best.path + [(cx, cy, bbox[2] - bbox[0])])[-40:]
                # virtual line crossing
                if uses_line and not best.crossed_ts:
                    s = self._side(line, (cx, cy))
                    if best.side and s and s != best.side and self._within_segment(line, (cx, cy)):
                        direction = "forward" if best.side < 0 < s else "reverse"
                        if line.get("direction", "any") in ("any", direction):
                            best.crossed_ts, best.crossed_dir = now, direction
                            self._pub("cross", {"track": best.id, "dir": direction})
                    if s:
                        best.side = s
                best.center = (cx, cy)
                if bbox[2] - bbox[0] >= min_w and not best.emitted:
                    crop = self.engine._crop(frame, bbox)
                    best.pending = (crop, bbox, conf, frame)
            self._evaluate_all(c, now)

    def _expire(self, c, now):
        with self._tlock:
            for t in list(self.tracks.values()):
                if now - t.last_ts > TRACK_TTL:
                    if not t.emitted and c.get("trigger_mode") not in ("line", "mixed"):
                        self._evaluate(t, c, now, final=True)
                    del self.tracks[t.id]

    # ------------------------------------------------------------ OCR worker
    def _ocr_loop(self):
        while not self._stop.is_set():
            c = self.cfg()
            if self.engine.status != "ready":
                time.sleep(0.3)
                continue
            now = time.time()
            with self._tlock:
                cands = [t for t in self.tracks.values()
                         if t.pending is not None and not t.emitted and len(t.reads) < MAX_READS
                         and now - t.last_ocr > 0.12]
                # crossed-but-unread tracks first, then the biggest (closest) plate
                cands.sort(key=lambda t: (not t.crossed_ts, len(t.reads), -(t.bbox[2] - t.bbox[0])))
                t = cands[0] if cands else None
                job = t.pending if t else None
                if t:
                    t.pending, t.last_ocr = None, now
            if not t:
                time.sleep(0.02)
                continue
            crop, bbox, conf, frame = job
            try:
                r = self.engine.read_crop(crop, bbox, conf, c)
            except Exception:
                log.exception("ocr failed")
                continue
            if r is None:
                continue
            self._pub("read", {"track": t.id, "text": r.text, "conf": r.conf, "ms": r.ms})
            with self._tlock:
                if t.id not in self.tracks:
                    continue
                t.reads.append(r)
                t.frames[id(r)] = frame
                res = self._consensus(t)
                if res:
                    disp = thai.parse_registration(res[0])[0] if c.get("plate_format") == "TH" else res[0]
                    t.label = f"{disp} {res[1] * 100:.0f}%"
                self._evaluate(t, c, time.time())

    def _consensus(self, t):
        return consensus(t.reads, self.engine.hint_keys, self.engine.list_bias)

    # ------------------------------------------------------------ decision
    def _evaluate_all(self, c, now):
        for t in list(self.tracks.values()):
            self._evaluate(t, c, now)

    def _direction(self, t):
        lane = self.unit.row.get("lane", "entry")
        if lane == "entry":
            return "in"
        if lane == "exit":
            return "out"
        if t.crossed_dir in ("forward", "reverse"):
            return "in" if t.crossed_dir == "forward" else "out"
        m = t.motion()
        return "in" if m == "approach" else "out" if m == "leave" else ""

    def _evaluate(self, t: Track, c, now, final=False):
        if t.emitted or not t.reads:
            return
        mode = c.get("trigger_mode", "line")
        res = self._consensus(t)
        if not res:
            return
        key, agree, support, best = res
        conf = round(min(1.0, agree * 0.55 + best.conf * 0.45), 3)
        min_conf, min_votes = float(c.get("min_conf", 0.55)), int(c.get("min_votes", 2))
        if mode in ("line", "mixed"):
            if not t.crossed_ts:
                return
            waited = now - t.crossed_ts
            # after crossing: report once votes agree, or after the wait window with whatever we have
            ok = (support >= min_votes and conf >= min_conf) or \
                 (waited >= float(c.get("post_cross_wait", 0.8)) and conf >= min_conf * 0.85) or \
                 (waited >= 2.5 and support >= 1)
        else:
            ok = (support >= min_votes and conf >= min_conf) or \
                 (final and conf >= max(min_conf, 0.75))
            want = c.get("motion", "any")
            if ok and want != "any":
                m = t.motion()
                if not m and not final:
                    return                    # wait until the movement direction is clear
                if m and m != want:
                    t.emitted, t.result = True, "skip"
                    return
        if not ok:
            return
        t.emitted = True
        if self._is_duplicate(key, c, now):
            t.result = "dup"
            return
        self.last_emitted[key] = now
        disp = thai.parse_registration(key)[0] if c.get("plate_format") == "TH" else key
        read = PlateRead(bbox=t.bbox, det_conf=best.det_conf, text=disp, key=key, province=best.province,
                         raw=" / ".join(r.raw for r in t.reads[-4:]), ocr_conf=best.ocr_conf,
                         format_score=best.format_score, conf=conf, crop=best.crop, ms=best.ms)
        frame = t.frames.get(id(best))
        # province: the two sharpest (largest) crops of this vehicle are read together
        crops = [r.crop for r in sorted(t.reads, key=lambda r: -(r.bbox[2] - r.bbox[0]))[:2]]
        src = {"line": "line", "video": "video", "input": "input", "mixed": "input" if t.crossed_dir == "input" else "line"}[mode]
        threading.Thread(target=self._emit_track, args=(t, read, frame, src, crops, self._direction(t)), daemon=True).start()

    def _emit_track(self, t, read, frame, src, crops, direction):
        ev = self.emit(read, frame, src, crops=crops, direction=direction)
        t.result = ev["decision"]

    def _is_duplicate(self, key, c, now):
        span = float(c.get("dedupe_seconds", 25))
        self.last_emitted = {k: ts for k, ts in self.last_emitted.items() if now - ts < span}
        for k in self.last_emitted:
            if thai.edit_distance(k, key) <= 1:
                self.last_emitted[k] = now
                return True
        return False

    # ------------------------------------------------------------ actions
    def _act(self, decision: dict, source: str, plate: str, allow_gate=True) -> str:
        acts = []
        acfg = db.get_settings()["access"]
        if decision["decision"] == "granted" and allow_gate and acfg.get("auto_open", True) \
                and decision.get("auto_open", True):
            for o in decision.get("outputs") or [0]:
                try:
                    port, cam_id = self.unit.open_gate(int(o) or None, source=source, detail=plate)
                    acts.append(f"OUT{port} เปิดไม้กั้น" + ("" if cam_id == self.unit.id else f" (กล้อง #{cam_id})"))
                except Exception as e:
                    acts.append(f"สั่งเปิดไม่สำเร็จ: {e}")
        if decision.get("alarm") and acfg.get("blocklist_alarm", True):
            try:
                port = self.io.alarm(plate)
                if port:
                    acts.append(f"OUT{port} แจ้งเตือน")
            except Exception as e:
                acts.append(f"แจ้งเตือนไม่สำเร็จ: {e}")
        return " · ".join(acts) or ("-" if decision["decision"] == "denied" else "ไม่สั่งงาน I/O")

    def _save_images(self, frame, read, color, overlay=None):
        day = datetime.now().strftime("%Y%m%d")
        stamp = datetime.now().strftime("%H%M%S_%f") + f"_c{self.unit.id}"
        snap_rel = plate_rel = ""
        if frame is not None:
            img = frame.copy()
            if read is not None and read.bbox[2] > read.bbox[0]:
                x1, y1, x2, y2 = map(int, read.bbox)
                cv2.rectangle(img, (x1, y1), (x2, y2), color, max(2, img.shape[1] // 600))
            scale = min(1.0, 1600 / img.shape[1])
            if scale < 1:
                img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if overlay:
                img = burn_overlay(img, overlay, color)
            os.makedirs(SNAP_DIR / day, exist_ok=True)
            snap_rel = f"{day}/{stamp}.jpg"
            cv2.imwrite(str(SNAP_DIR / snap_rel), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if read is not None and read.crop is not None and read.crop.size:
            os.makedirs(PLATE_DIR / day, exist_ok=True)
            plate_rel = f"{day}/{stamp}.jpg"
            cv2.imwrite(str(PLATE_DIR / plate_rel), read.crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return snap_rel, plate_rel

    def emit(self, read, frame, source="video", allow_gate=True, province=None, crops=None, direction=""):
        t0 = time.time()
        self.last_emitted[read.key] = time.time()   # manual/test captures also suppress the video duplicate
        thai_fmt = self.cfg().get("plate_format") == "TH"
        need_prov = province is None and not read.province and thai_fmt
        crops = [x for x in (crops or [read.crop]) if x is not None]
        # the province only matters for matching when two registered vehicles share a registration
        ambiguous = db.one("SELECT COUNT(*) n FROM vehicles WHERE plate_key=?", (read.key,))["n"] > 1
        if need_prov and ambiguous:
            province, need_prov = self.engine.read_province_multi(crops), False
        province = province if province is not None else read.province
        decision = access.decide(read.key, province or "", camera_id=self.unit.id)
        action = self._act(decision, source, read.text, allow_gate)   # gate first, paperwork after
        if need_prov:
            province = self.engine.read_province_multi(crops)
        read.province = province or ""
        province = read.province
        color = GREEN if decision["decision"] == "granted" else RED
        ts = db.now_iso()
        overlay = None
        if db.get_settings()["recognition"].get("overlay", True):
            overlay = {"camera": self.unit.name, "ts": ts, "plate": f"{read.text} {province}".strip(),
                       "direction": DIR_TH.get(direction, ""),
                       "decision": "อนุญาต" if decision["decision"] == "granted" else "ไม่อนุญาต"}
        snap, plate_img = self._save_images(frame, read, color, overlay)
        row = {"ts": ts, "plate": read.text, "province": province, "raw_text": read.raw,
               "confidence": round(read.conf, 3), "vehicle_id": (decision["vehicle"] or {}).get("id"),
               "owner": (decision["vehicle"] or {}).get("owner", ""), "group_name": decision["group_name"],
               "group_color": decision["group_color"], "list_type": decision["list_type"],
               "decision": decision["decision"], "reason": decision["reason"], "gate_action": action,
               "source": source, "snapshot": snap, "plate_img": plate_img,
               "latency_ms": int((time.time() - t0) * 1000) + read.ms, "camera": self.unit.name,
               "camera_id": self.unit.id, "direction": direction}
        row["id"] = db.insert("events", row)
        self.last_latency = row["latency_ms"]
        payload = event_payload(row)
        payload["vehicle"] = decision["vehicle"]
        bus.publish("event", payload)
        log.info("EVENT [%s] %s %s %.2f -> %s (%s)", self.unit.name, read.text, province, read.conf,
                 decision["decision"], action)
        # high-confidence exact matches of registered plates become (unverified) training samples
        if plate_img and source in ("line", "video", "input") and decision["vehicle"] and not decision.get("fuzzy") \
                and read.conf >= 0.9 and db.get_settings()["ai"].get("auto_samples", True):
            try:
                dataset.add_from_event(row["id"], read.key, province, read.key, "auto", "auto")
            except Exception:
                log.exception("auto sample failed")
        return payload

    # ------------------------------------------------------------ tools
    def capture_now(self, commit=True, source="manual", user="system"):
        frame, _, _ = self.video.latest()
        if frame is None:
            raise RuntimeError("ยังไม่มีภาพจากกล้อง")
        c = self.cfg()
        sub, off = self._roi(frame, c)
        reads = self.engine.recognize(sub, {**c, "min_det_conf": min(0.3, c.get("min_det_conf", 0.4))},
                                      with_province=True, offset=off)
        if not reads:
            return {"found": False, "reads": [], "image": _b64(frame, None)}
        if commit:
            ev = self.emit(reads[0], frame, source, province=reads[0].province,
                           direction={"entry": "in", "exit": "out"}.get(self.unit.row.get("lane"), ""))
            return {"found": True, "event": ev, "reads": [r.to_dict() for r in reads]}
        return {"found": True, "reads": [r.to_dict() for r in reads], "image": _b64(frame, reads)}

    def analyze_image(self, img, commit=False, open_gate=False):
        c = self.cfg()
        t0 = time.time()
        reads = self.engine.recognize(img, {**c, "min_det_conf": 0.25, "min_plate_width": 20}, with_province=True)
        out = []
        for r in reads:
            d = access.decide(r.key, r.province, camera_id=self.unit.id)
            out.append({**r.to_dict(), "decision": d["decision"], "reason": d["reason"],
                        "group_name": d["group_name"], "list_type": d["list_type"],
                        "crop": _b64(r.crop, None, 480)})
        ev = None
        if commit and reads:
            ev = self.emit(reads[0], img, "test", allow_gate=open_gate, province=reads[0].province)
        return {"reads": out, "ms": int((time.time() - t0) * 1000), "image": _b64(img, reads), "event": ev}

    def simulate(self, plate_text: str, province: str, act: bool):
        if self.cfg().get("plate_format") == "TH":
            disp, key, _ = thai.parse_registration(plate_text)
        else:
            disp, key = plate_text, thai.plate_key(plate_text)
        if not key:
            key, disp = thai.plate_key(plate_text), plate_text
        frame, _, _ = self.video.latest()
        read = PlateRead(bbox=(0, 0, 0, 0), det_conf=1.0, text=disp, key=key, province=province,
                         raw=plate_text, ocr_conf=1.0, format_score=1.0, conf=1.0)
        return self.emit(read, frame, "simulate", allow_gate=act, province=province,
                         direction={"entry": "in", "exit": "out"}.get(self.unit.row.get("lane"), ""))

    # ------------------------------------------------------------ live view
    def render_live(self, width=1280, quality=72):
        frame, fid, _ = self.video.latest()
        if frame is None:
            return None
        cached_fid, cached_w, data = self._live_cache
        if cached_fid == fid and cached_w == width:
            return data
        h, w = frame.shape[:2]
        scale = min(1.0, width / w)
        img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame.copy()
        ih, iw = img.shape[:2]
        c = self.cfg()
        area = self._area(c)
        if not (len(area) == 4 and area.min() <= 0.001 and area.max() >= 0.999):
            pts = (area * [iw, ih]).astype(np.int32)
            cv2.polylines(img, [pts], True, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.polylines(img, [pts], True, AREA_C, 2, cv2.LINE_AA)
        line = c.get("trigger_line") or {}
        if c.get("trigger_mode") in ("line", "mixed") and line:
            _trigger_line(img, line, iw, ih)
        now = time.time()
        with self._tlock:
            tracks = [t for t in self.tracks.values() if now - t.last_ts < 0.8]
        for t in tracks:
            x1, y1, x2, y2 = [int(v * scale) for v in t.bbox]
            color = GREEN if t.result == "granted" else RED if t.result == "denied" else AMBER
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            # Hershey fonts have no Thai glyphs: video shows track id + confidence, the plate text lives in the UI
            label = f"#{t.id} {t.label.split(' ')[-1] if t.label else '...'}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (x1, y1 - th - 8), (x1 + tw + 8, y1), color, -1)
            cv2.putText(img, label, (x1 + 4, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        data = buf.tobytes() if ok else b""
        self._live_cache = (fid, width, data)
        return data

    def live_tracks(self):
        now = time.time()
        with self._tlock:
            return [{"id": t.id, "label": t.label, "reads": len(t.reads), "crossed": bool(t.crossed_ts),
                     "dir": t.crossed_dir, "motion": t.motion(), "result": t.result}
                    for t in self.tracks.values() if now - t.last_ts < 1.0]


def event_payload(row: dict) -> dict:
    d = dict(row)
    d["snapshot_url"] = f"/api/media/snapshots/{row['snapshot']}" if row.get("snapshot") else ""
    d["plate_url"] = f"/api/media/plates/{row['plate_img']}" if row.get("plate_img") else ""
    return d


def _trigger_line(img, line, iw, ih):
    p1 = (int(line["x1"] * iw), int(line["y1"] * ih))
    p2 = (int(line["x2"] * iw), int(line["y2"] * ih))
    cv2.line(img, p1, p2, (0, 0, 0), 6, cv2.LINE_AA)
    cv2.line(img, p1, p2, LINE_C, 3, cv2.LINE_AA)
    for p in (p1, p2):
        cv2.circle(img, p, 6, LINE_C, -1, cv2.LINE_AA)
    # direction arrow (normal of the line, pointing to the "forward" side)
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = max(1.0, (dx * dx + dy * dy) ** 0.5)
    nx, ny = -dy / L, dx / L
    d = line.get("direction", "any")
    for sgn, show in ((1, d in ("any", "forward")), (-1, d in ("any", "reverse"))):
        if show:
            tip = (int(mx + nx * 34 * sgn), int(my + ny * 34 * sgn))
            cv2.arrowedLine(img, (int(mx), int(my)), tip, LINE_C, 3, cv2.LINE_AA, tipLength=0.4)


# ---------------------------------------------------------------- evidence overlay (Thai text via Pillow)
_FONTS = {}


def _font(size):
    if size not in _FONTS:
        from PIL import ImageFont
        f = None
        # Tahoma first: its Thai marks render correctly without complex text shaping (Pillow without raqm)
        for path in ("C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/LeelawUI.ttf",
                     "/usr/share/fonts/truetype/tlwg/Garuda.ttf"):
            try:
                f = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        _FONTS[size] = f
    return _FONTS[size]


def burn_overlay(img, o: dict, color):
    """Top info bar like an ANPR camera's capture overlay: camera · time · plate · direction · decision."""
    try:
        from PIL import Image, ImageDraw
        h, w = img.shape[:2]
        size = max(15, w // 64)
        font = _font(size)
        if font is None:
            return img
        bar = int(size * 1.9)
        dark = img.copy()
        cv2.rectangle(dark, (0, 0), (w, bar), (18, 18, 20), -1)
        img = cv2.addWeighted(dark, 0.78, img, 0.22, 0)
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        pad = size // 2
        left = "   ".join(x for x in (o.get("camera"), o.get("ts"), o.get("plate"),
                                      f"ทิศทาง: {o['direction']}" if o.get("direction") else "") if x)
        d.text((pad, (bar - size) // 2 - 2), left, font=font, fill=(240, 240, 240))
        tag = o.get("decision", "")
        tw = d.textlength(tag, font=font)
        x0 = w - tw - pad * 3
        d.rectangle([x0, 4, w - pad, bar - 4], fill=(color[2], color[1], color[0]))
        d.text((x0 + pad, (bar - size) // 2 - 2), tag, font=font, fill=(255, 255, 255))
        return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        log.exception("overlay failed")
        return img


def _b64(img, reads, max_w=1280):
    if img is None:
        return ""
    out = img.copy()
    for r in reads or []:
        x1, y1, x2, y2 = map(int, r.bbox)
        cv2.rectangle(out, (x1, y1), (x2, y2), GREEN, max(2, out.shape[1] // 500))
    scale = min(1.0, max_w / out.shape[1])
    if scale < 1:
        out = cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""
