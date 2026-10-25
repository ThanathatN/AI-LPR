"""Alarm I/O of ONE camera: barrier output, loop-detector trigger input, radar anti-fall safety input.

Inputs are read two ways at once:
  * the camera's ISAPI alertStream (event-driven, ~instant) — needs the IO-1 trigger to notify "center"
  * polling /IO/inputs/{id}/status as a fallback
Every camera has its own IOManager; the barrier relay may live on another camera (lane config
``barrier_camera``) — ``resolve_barrier`` is supplied by the camera manager for that.
"""
import logging
import threading
import time

from . import db
from .bus import bus

log = logging.getLogger("lpr.io")
EVENT_HOLD = 2.0   # seconds an alertStream 'active' keeps the input active without a refresh


class IOManager:
    def __init__(self, cam_id: int, lane_cfg):
        self.cam_id = cam_id
        self.lane_cfg = lane_cfg          # callable -> this camera's lane config {"recognition", "io"}
        self.cam = None
        self.outputs = []
        self.inputs = []
        self.in_state = {}
        self.out_state = {}
        self.online = False
        self.error = ""
        self.stream_ok = False
        self.conflicts = []        # camera event linkages that also drive our outputs
        self.on_trigger = None     # fn(port_id, target_camera_id)
        self.resolve_barrier = lambda: (self, int(self.cfg().get("barrier_output", 0) or 0))
        self._evt_ts = {}          # input id -> last alertStream active timestamp
        self._lock = threading.RLock()
        self._safety_hold = False
        self._last_hold_refresh = 0.0
        self._started = False
        self._stop = threading.Event()
        self._gen = 0              # bumps when the camera changes so old stream threads exit
        self.counters = {"gate_open": 0, "triggers": 0}

    # ------------------------------------------------------------ setup
    def attach(self, cam):
        self.cam = cam
        self._gen += 1
        self.in_state, self._evt_ts = {}, {}
        if cam is None:
            self.online, self.error, self.outputs, self.inputs = False, "ไม่มี ISAPI (แหล่งวิดีโอทดแทน)", [], []
            return
        self.refresh_ports()
        threading.Thread(target=self._alert_loop, args=(self._gen,), daemon=True, name=f"io-alerts-{self.cam_id}").start()
        if not self._started:
            self._started = True
            threading.Thread(target=self._poll, daemon=True, name=f"io-poll-{self.cam_id}").start()

    def stop(self):
        self._gen += 1
        self._stop.set()

    def refresh_ports(self):
        if not self.cam:
            return
        try:
            self.outputs = self.cam.outputs()
            self.inputs = self.cam.inputs()
            self.online, self.error = True, ""
        except Exception as e:
            self.online, self.error = False, str(e)
            log.warning("cam %s I/O port query failed: %s", self.cam_id, e)
            return
        self.check_conflicts()

    def check_conflicts(self):
        if not self.cam:
            return []
        try:
            outs = {p["id"] for p in self.outputs}
            self.conflicts = [t for t in self.cam.event_triggers() if set(t["outputs"]) & outs]
        except Exception as e:
            log.warning("linkage check failed: %s", e)
        return self.conflicts

    def fix_conflicts(self, ids=None):
        for t in list(self.conflicts):
            if ids and t["id"] not in ids:
                continue
            self.cam.remove_output_linkage(t["id"])
            db.log_io("output", t["outputs"][0] if t["outputs"] else 0, "unlink", "system",
                      f"camera linkage {t['id']} removed", self.cam_id)
        self.check_conflicts()
        try:
            for p in self.outputs:
                self.cam.set_output(p["id"], False)
        except Exception:
            pass
        self.publish()
        return self.conflicts

    def cfg(self):
        return self.lane_cfg()["io"]

    def role_of(self, kind: str, port_id: int) -> str:
        c = self.cfg()
        if kind == "output":
            if int(c.get("barrier_camera", 0) or 0) in (0, self.cam_id) and int(c.get("barrier_output", 0) or 0) == port_id:
                return "barrier"
            if int(c.get("alarm_output", 0) or 0) == port_id:
                return "alarm"
            return "none"
        return c.get("inputs", {}).get(str(port_id), {}).get("role", "none")

    def _need_cam(self):
        if not self.cam:
            raise RuntimeError("กล้องนี้ไม่มี Alarm I/O (ใช้แหล่งวิดีโอทดแทน)")

    # ------------------------------------------------------------ actions
    def set_output(self, port_id: int, high: bool, source="manual", user="system"):
        self._need_cam()
        self.cam.set_output(port_id, high)
        self.out_state[port_id] = high
        db.log_io("output", port_id, "high" if high else "low", source, user, self.cam_id)
        self.publish()

    def pulse(self, port_id: int, ms: int = None, source="manual", user="system", detail=""):
        self._need_cam()
        ms = int(ms or self.cfg().get("pulse_ms", 1000))
        self.cam.pulse_output(port_id, ms)
        self.out_state[port_id] = True
        db.log_io("output", port_id, f"pulse {ms}ms", source, detail or user, self.cam_id)
        self.publish()

        def clear():
            time.sleep(ms / 1000 + 0.3)
            self.out_state[port_id] = False
            self.publish()
        threading.Thread(target=clear, daemon=True).start()

    def alarm(self, detail=""):
        port = int(self.cfg().get("alarm_output", 0) or 0)
        if port and self.cam:
            self.pulse(port, 3000, source="blocklist", detail=detail)
        return port

    # ------------------------------------------------------------ state
    def snapshot(self):
        return {
            "cam": self.cam_id,
            "online": self.online, "error": self.error, "safety_hold": self._safety_hold, "stream": self.stream_ok,
            "conflicts": self.conflicts,
            "inputs": [{**p, "active": self.in_state.get(p["id"], False), "role": self.role_of("input", p["id"])}
                       for p in self.inputs],
            "outputs": [{**p, "active": self.out_state.get(p["id"], False), "role": self.role_of("output", p["id"])}
                        for p in self.outputs],
            "counters": self.counters,
        }

    def publish(self):
        bus.publish("io", self.snapshot())

    def _set_input(self, port_id: int, active: bool, via: str):
        with self._lock:
            prev = self.in_state.get(port_id)
            self.in_state[port_id] = active
        if prev is not None and prev != active:
            self._on_input(port_id, active, via)

    def _on_input(self, port_id: int, active: bool, via: str):
        role = self.role_of("input", port_id)
        db.log_io("input", port_id, "active" if active else "inactive", role, via, self.cam_id)
        log.info("cam %s input %s -> %s (%s via %s)", self.cam_id, port_id, active, role, via)
        if active and role == "trigger":
            self.counters["triggers"] += 1
            if self.on_trigger:
                target = int(self.cfg().get("inputs", {}).get(str(port_id), {}).get("target", 0) or 0)
                self.on_trigger(port_id, target or self.cam_id)
        self.publish()

    # ------------------------------------------------------------ alertStream (event-driven inputs)
    def _alert_loop(self, gen):
        while gen == self._gen and not self._stop.is_set():
            cam = self.cam
            try:
                for ev in cam.alert_stream(lambda: gen != self._gen):
                    if not self.stream_ok:
                        self.stream_ok = True
                    if ev["type"] == "IO" and ev["port"]:
                        if ev["state"] == "active":
                            self._evt_ts[ev["port"]] = time.time()
                            self._set_input(ev["port"], True, "event")
                        else:
                            self._evt_ts.pop(ev["port"], None)
            except Exception as e:
                if gen == self._gen:
                    log.info("cam %s alertStream reconnecting: %s", self.cam_id, str(e)[:120])
            self.stream_ok = False
            time.sleep(3)

    # ------------------------------------------------------------ polling
    def _poll(self):
        last_out = last_conf = 0.0
        while not self._stop.is_set():
            interval = max(0.1, db.get_settings()["io"].get("poll_ms", 300) / 1000)
            if not self.cam:
                time.sleep(1)
                continue
            try:
                if not self.inputs and not self.outputs:
                    self.refresh_ports()
                now = time.time()
                for p in self.inputs:
                    polled = self.cam.port_status("input", p["id"])
                    recent = now - self._evt_ts.get(p["id"], 0) < EVENT_HOLD
                    self._set_input(p["id"], polled or recent, "poll" if polled else "event")
                if now - last_out > 1.5:
                    for p in self.outputs:
                        self.out_state[p["id"]] = self.cam.port_status("output", p["id"])
                    last_out = now
                if now - last_conf > 60:
                    self.check_conflicts()
                    last_conf = now
                self._safety_tick()
                if not self.online:
                    self.online, self.error = True, ""
                    self.publish()
            except Exception as e:
                if self.online:
                    log.warning("cam %s I/O poll failed: %s", self.cam_id, e)
                    self.online, self.error = False, str(e)[:200]
                    self.publish()
                time.sleep(2)
            time.sleep(interval)

    def _safety_tick(self):
        """Radar / anti-fall input: keep the barrier (possibly on another camera) open while an object is under the arm."""
        safety_ports = [p["id"] for p in self.inputs if self.role_of("input", p["id"]) == "safety"]
        if not safety_ports and not self._safety_hold:
            return
        active = any(self.in_state.get(i) for i in safety_ports)
        target, barrier = self.resolve_barrier()
        if not barrier or target is None or not target.cam:
            return
        if active and (not self._safety_hold or time.time() - self._last_hold_refresh > 2):
            target.cam.set_output(barrier, True)
            if not self._safety_hold:
                db.log_io("output", barrier, "hold", "safety", f"anti-fall active (cam {self.cam_id})", target.cam_id)
            self._safety_hold, self._last_hold_refresh = True, time.time()
            target.out_state[barrier] = True
            target.publish()
        elif not active and self._safety_hold:
            target.cam.set_output(barrier, False)
            self._safety_hold = False
            target.out_state[barrier] = False
            db.log_io("output", barrier, "release", "safety", f"anti-fall clear (cam {self.cam_id})", target.cam_id)
            target.publish()
