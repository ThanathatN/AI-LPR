"""Hikvision ISAPI client (device info, alarm I/O, snapshots)."""
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

log = logging.getLogger("lpr.isapi")
NS = "http://www.hikvision.com/ver20/XMLSchema"


def _strip_ns(xml_text: str) -> ET.Element:
    xml_text = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml_text, count=0)
    return ET.fromstring(xml_text.encode("utf-8"))


def _text(el, path, default=""):
    f = el.find(path)
    return f.text.strip() if f is not None and f.text else default


class HikCamera:
    def __init__(self, host, username, password, http_port=80, rtsp_port=554, timeout=4):
        self.host, self.username, self.password = host, username, password
        self.http_port, self.rtsp_port, self.timeout = int(http_port), int(rtsp_port), timeout
        self.s = requests.Session()
        self.s.auth = HTTPDigestAuth(username, password)
        self._io_lock = threading.Lock()

    @property
    def base(self):
        port = "" if self.http_port == 80 else f":{self.http_port}"
        return f"http://{self.host}{port}"

    # Video and snapshots are NOT fetched over ISAPI/RTSP — images come only from HCNetSDK (hik_capture).
    # ISAPI here is for device info, stream configuration, image settings and Alarm I/O.

    def _get(self, path, **kw):
        r = self.s.get(self.base + path, timeout=kw.pop("timeout", self.timeout), **kw)
        if r.status_code == 401:
            raise PermissionError("ชื่อผู้ใช้หรือรหัสผ่านกล้องไม่ถูกต้อง (401)")
        r.raise_for_status()
        return r

    def _put(self, path, xml_body):
        r = self.s.put(self.base + path, data=xml_body.encode("utf-8"),
                       headers={"Content-Type": "application/xml"}, timeout=self.timeout)
        if r.status_code == 401:
            raise PermissionError("ชื่อผู้ใช้หรือรหัสผ่านกล้องไม่ถูกต้อง (401)")
        if r.status_code >= 400:
            msg = _text(_strip_ns(r.text), "subStatusCode", r.reason) if r.text.startswith("<") else r.reason
            raise RuntimeError(f"ISAPI {r.status_code}: {msg}")
        return r

    # ---------------------------------------------------------------- info
    def device_info(self) -> dict:
        root = _strip_ns(self._get("/ISAPI/System/deviceInfo").text)
        return {c.tag: (c.text or "").strip() for c in root}

    def device_time(self) -> str:
        root = _strip_ns(self._get("/ISAPI/System/time").text)
        return _text(root, "localTime")

    def stream_info(self, channel="101") -> dict:
        """Encoder configuration only (codec / resolution / fps / bitrate) — no image data."""
        root = _strip_ns(self._get(f"/ISAPI/Streaming/channels/{channel}").text)
        v = root.find("Video")
        if v is None:
            return {}
        return {"codec": _text(v, "videoCodecType"), "width": _text(v, "videoResolutionWidth"),
                "height": _text(v, "videoResolutionHeight"),
                "fps": str(int(_text(v, "maxFrameRate", "0") or 0) / 100),
                "bitrate": _text(v, "vbrUpperCap") or _text(v, "constantBitRate")}

    # ---------------------------------------------------------------- I/O
    def outputs(self) -> list:
        root = _strip_ns(self._get("/ISAPI/System/IO/outputs").text)
        out = []
        for p in root.findall("IOOutputPort"):
            out.append({"id": int(_text(p, "id", "0")), "name": _text(p, "name"),
                        "default_state": _text(p, "PowerOnState/defaultState", "low"),
                        "output_state": _text(p, "PowerOnState/outputState", "pulse"),
                        "pulse_ms": int(_text(p, "PowerOnState/pulseDuration", "1000") or 1000)})
        return out

    def inputs(self) -> list:
        root = _strip_ns(self._get("/ISAPI/System/IO/inputs").text)
        return [{"id": int(_text(p, "id", "0")), "name": _text(p, "name"),
                 "enabled": _text(p, "enabled", "true") == "true",
                 "triggering": _text(p, "triggering", "high")} for p in root.findall("IOInputPort")]

    def port_status(self, kind: str, port_id: int) -> bool:
        root = _strip_ns(self._get(f"/ISAPI/System/IO/{kind}s/{port_id}/status", timeout=2).text)
        return _text(root, "ioState") == "active"

    def set_output(self, port_id: int, high: bool):
        body = (f'<?xml version="1.0" encoding="UTF-8"?><IOPortData version="2.0" xmlns="{NS}">'
                f'<outputState>{"high" if high else "low"}</outputState></IOPortData>')
        with self._io_lock:
            self._put(f"/ISAPI/System/IO/outputs/{port_id}/trigger", body)

    def pulse_output(self, port_id: int, ms: int = 1000):
        self.set_output(port_id, True)

        def release():
            time.sleep(ms / 1000)
            try:
                self.set_output(port_id, False)
            except Exception as e:
                log.warning("release output %s failed: %s", port_id, e)
        threading.Thread(target=release, daemon=True).start()

    def configure_output(self, port_id: int, name: str, default_state: str, output_state: str, pulse_ms: int):
        body = (f'<?xml version="1.0" encoding="UTF-8"?><IOOutputPort version="2.0" xmlns="{NS}">'
                f"<id>{port_id}</id><PowerOnState><defaultState>{default_state}</defaultState>"
                f"<outputState>{output_state}</outputState><pulseDuration>{int(pulse_ms)}</pulseDuration>"
                f"</PowerOnState><name>{_esc(name)}</name></IOOutputPort>")
        self._put(f"/ISAPI/System/IO/outputs/{port_id}", body)

    # ---------------------------------------------------------------- event linkage
    def event_triggers(self) -> list:
        """Camera-side event linkages. Any trigger with an IO notification fires the relay by itself."""
        root = _strip_ns(self._get("/ISAPI/Event/triggers").text)
        out = []
        for t in root.findall("EventTrigger"):
            notes = t.findall("EventTriggerNotificationList/EventTriggerNotification")
            out.append({"id": _text(t, "id"), "type": _text(t, "eventType"),
                        "methods": [_text(n, "notificationMethod") for n in notes],
                        "outputs": [int(_text(n, "outputIOPortID", "0") or 0) for n in notes
                                    if _text(n, "notificationMethod") == "IO"]})
        return out

    def remove_output_linkage(self, trigger_id: str):
        """Drop the 'IO' (alarm output) notification from one camera event trigger."""
        xml = self._get(f"/ISAPI/Event/triggers/{trigger_id}").text
        cleaned = re.sub(r"<EventTriggerNotification>(?:(?!</EventTriggerNotification>).)*?"
                         r"<notificationMethod>IO</notificationMethod>.*?</EventTriggerNotification>\s*",
                         "", xml, flags=re.S)
        if cleaned != xml:
            self._put(f"/ISAPI/Event/triggers/{trigger_id}", cleaned)

    def alert_stream(self, stop):
        """Yield camera events from the long-lived alertStream: {'type','state','port'}."""
        with self.s.get(self.base + "/ISAPI/Event/notification/alertStream", stream=True, timeout=(5, 40)) as r:
            r.raise_for_status()
            buf = ""
            for chunk in r.iter_content(4096):
                if stop():
                    return
                buf += chunk.decode("utf-8", "replace")
                while "</EventNotificationAlert>" in buf:
                    block, buf = buf.split("</EventNotificationAlert>", 1)
                    et = re.search(r"<eventType>([^<]+)</eventType>", block)
                    st = re.search(r"<eventState>([^<]+)</eventState>", block)
                    port = re.search(r"<inputIOPortID>(\d+)</inputIOPortID>", block) or \
                        re.search(r"<dynInputIOPortID>(\d+)</dynInputIOPortID>", block)
                    yield {"type": et.group(1) if et else "", "state": st.group(1) if st else "",
                           "port": int(port.group(1)) if port else 0}
                if len(buf) > 200_000:
                    buf = buf[-20_000:]

    # ---------------------------------------------------------------- image (exposure for LPR)
    IMAGE_KEYS = {          # our name -> (sub-resource, element path inside it)
        "shutter": ("Shutter", "ShutterLevel"),
        "wdr": ("WDR", "mode"), "wdr_level": ("WDR", "WDRLevel"),
        "hlc": ("HLC", "enabled"), "hlc_level": ("HLC", "HLCLevel"),
        "blc": ("BLC", "enabled"),
        "dnr": ("NoiseReduce", "mode"), "dnr_level": ("NoiseReduce", "GeneralMode/generalLevel"),
        "sharpness": ("Sharpness", "SharpnessLevel"),
        "gain": ("Gain", "GainLevel"),
        "brightness": ("Color", "brightnessLevel"), "contrast": ("Color", "contrastLevel"),
        "ircut": ("IrcutFilter", "IrcutFilterType"),
    }

    def image_settings(self, channel=1) -> dict:
        """Current image values + the options the camera offers for each."""
        cur = _strip_ns(self._get(f"/ISAPI/Image/channels/{channel}").text)
        try:
            cap = _strip_ns(self._get(f"/ISAPI/Image/channels/{channel}/capabilities").text)
        except Exception:
            cap = None
        out = {}
        for k, (sec, path) in self.IMAGE_KEYS.items():
            el = cur.find(f"{sec}/{path}")
            if el is None:
                continue
            item = {"value": (el.text or "").strip()}
            c = cap.find(f"{sec}/{path}") if cap is not None else None
            if c is not None:
                if c.get("opt"):
                    item["opt"] = c.get("opt").split(",")
                if c.get("min") is not None:
                    item["min"], item["max"] = int(c.get("min")), int(c.get("max"))
            out[k] = item
        return out

    def set_image(self, values: dict, channel=1):
        """Change image values. Each sub-resource (Shutter, WDR...) is read, edited and PUT back."""
        by_sec = {}
        for k, v in values.items():
            if k in self.IMAGE_KEYS:
                sec, path = self.IMAGE_KEYS[k]
                by_sec.setdefault(sec, []).append((path, str(v).lower() if isinstance(v, bool) else str(v)))
        errors = []
        for sec, items in by_sec.items():
            try:
                xml = self._get(f"/ISAPI/Image/channels/{channel}/{sec}").text
                for path, v in items:
                    tag = path.split("/")[-1]
                    xml, n = re.subn(rf"(<{tag}>)[^<]*(</{tag}>)", rf"\g<1>{_esc(v)}\g<2>", xml, count=1)
                    if not n:
                        raise RuntimeError(f"{sec}/{path} not found")
                self._put(f"/ISAPI/Image/channels/{channel}/{sec}", xml)
            except Exception as e:
                errors.append(f"{sec}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def configure_input(self, port_id: int, name: str, enabled: bool, triggering: str):
        body = (f'<?xml version="1.0" encoding="UTF-8"?><IOInputPort version="2.0" xmlns="{NS}">'
                f"<id>{port_id}</id><enabled>{'true' if enabled else 'false'}</enabled>"
                f"<triggering>{triggering}</triggering><name>{_esc(name)}</name></IOInputPort>")
        self._put(f"/ISAPI/System/IO/inputs/{port_id}", body)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
