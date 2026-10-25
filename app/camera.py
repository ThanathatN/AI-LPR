"""Test video source: plays a LOCAL video file (camera "source_override") in its own thread, looping.

Real cameras never come through here — their video comes only from HCNetSDK (app/hik_capture.py).
Network URLs (rtsp://, http://, ...) are refused on purpose.
"""
import logging
import os
import re
import threading
import time

import cv2

log = logging.getLogger("lpr.camera")
_NETWORK_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def is_network_url(src: str) -> bool:
    return bool(_NETWORK_URL.match((src or "").strip()))


class VideoSource:
    def __init__(self, name="video"):
        self.name = name
        self.url = ""
        self.frame = None
        self.frame_id = 0
        self.frame_ts = 0.0
        self.connected = False
        self.error = ""
        self.fps = 0.0
        self.width = self.height = 0
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self, url: str):
        self.url = url
        if self._thread and self._thread.is_alive():
            self._restart.set()
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def latest(self):
        with self._lock:
            return self.frame, self.frame_id, self.frame_ts

    def read(self, timeout=2.0):
        """Wait for the next new frame -> BGR ndarray (None on timeout). Same as HikSDKSource.read."""
        last, end = self.frame_id, time.time() + timeout
        while self.frame_id == last and time.time() < end and not self._stop.is_set():
            time.sleep(0.005)
        return self.latest()[0] if self.frame_id != last else None

    def status(self):
        return {"connected": self.connected and time.time() - self.frame_ts < 5, "error": self.error,
                "fps": round(self.fps, 1), "width": self.width, "height": self.height, "backend": "file"}

    def _open(self):
        return cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            self._restart.clear()
            if not self.url:                      # camera disabled / not configured
                self.connected, self.error = False, "ปิดใช้งาน หรือยังไม่ได้ตั้งค่าแหล่งวิดีโอ"
                with self._lock:
                    self.frame = None
                self._restart.wait(1.0)
                continue
            if is_network_url(self.url) or not os.path.isfile(self.url):
                self.connected = False
                self.error = ("ไม่รองรับ URL เครือข่าย — ภาพจากกล้องใช้ HCNetSDK เท่านั้น" if is_network_url(self.url)
                              else f"ไม่พบไฟล์วิดีโอทดสอบ: {self.url}")
                with self._lock:
                    self.frame = None
                self._restart.wait(5.0)
                continue
            cap = self._open()
            if not cap.isOpened():
                self.connected, self.error = False, "เปิดไฟล์วิดีโอทดสอบไม่สำเร็จ"
                log.warning("test video open failed, retry in %.0fs", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 15)
                continue
            backoff, self.error = 1.0, ""
            count, t0, fails = 0, time.time(), 0
            while not self._stop.is_set() and not self._restart.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    fails += 1
                    if fails > 25:
                        self.error = "อ่านไฟล์วิดีโอทดสอบไม่ได้"
                        break
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)          # loop the test file
                    continue
                fails = 0
                with self._lock:
                    self.frame = frame
                    self.frame_id += 1
                    self.frame_ts = time.time()
                self.connected = True
                self.height, self.width = frame.shape[:2]
                count += 1
                if time.time() - t0 >= 2:
                    self.fps = count / (time.time() - t0)
                    count, t0 = 0, time.time()
                time.sleep(1 / 25)                               # play at ~real time
            cap.release()
            self.connected = False
