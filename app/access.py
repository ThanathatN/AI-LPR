"""Access-control decision for a recognised plate."""
import json
from datetime import datetime

from . import db
from .lpr import thai

LIST_LABEL = {"allow": "Allowlist", "visitor": "Visitor", "deny": "Blocklist"}


def _in_schedule(sched: dict, now: datetime) -> bool:
    days = sched.get("days", [0, 1, 2, 3, 4, 5, 6])
    if now.weekday() not in days:
        return False
    start, end, cur = sched.get("start", "00:00"), sched.get("end", "23:59"), now.strftime("%H:%M")
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end  # overnight window


_SEL = ("SELECT v.*, g.name AS group_name, g.type AS list_type, g.color AS group_color, "
        "g.auto_open, g.outputs AS group_outputs, g.schedule, g.cameras AS group_cameras FROM vehicles v "
        "LEFT JOIN groups g ON g.id=v.group_id ")


def find_vehicle(key: str, province: str, fuzzy: bool):
    rows = db.query(_SEL + "WHERE v.plate_key=?", (key,))
    if not rows and fuzzy and len(key) >= 4:
        cands = db.query(_SEL + "WHERE length(v.plate_key) BETWEEN ? AND ?",
                         (len(key) - 1, len(key) + 1))
        rows = [r for r in cands if thai.edit_distance(r["plate_key"], key) <= 1]
        if len(rows) > 1:   # ambiguous fuzzy match is never trusted
            return None, False
        if rows:
            return rows[0], True
    if not rows:
        return None, False
    if len(rows) > 1 and province:
        same = [r for r in rows if r["province"] == province]
        if same:
            return same[0], False
    return rows[0], False


def decide(key: str, province: str = "", now: datetime = None, camera_id: int = None) -> dict:
    now = now or datetime.now()
    cfg = db.get_settings()["access"]
    v, fuzzy = find_vehicle(key, province, cfg.get("fuzzy_match", False))
    res = {"decision": "denied", "reason": "", "vehicle": None, "list_type": "unknown",
           "group_name": "", "group_color": "", "outputs": [], "alarm": False, "fuzzy": fuzzy}
    if not v:
        if cfg.get("unknown_action") == "open":
            res.update(decision="granted", reason="รถไม่ลงทะเบียน (นโยบาย: เปิดให้ผ่าน)")
        else:
            res["reason"] = "ไม่พบในรายชื่อ (Unregistered)"
        return res
    res.update(vehicle={k: v[k] for k in ("id", "plate", "province", "owner", "phone", "department",
                                         "vehicle_type", "brand", "color")},
               list_type=v["list_type"] or "unknown", group_name=v["group_name"] or "",
               group_color=v["group_color"] or "")
    today = now.strftime("%Y-%m-%d")
    if not v["enabled"]:
        res["reason"] = "ทะเบียนถูกระงับการใช้งาน"
    elif v["valid_from"] and today < v["valid_from"]:
        res["reason"] = f"ยังไม่ถึงวันเริ่มสิทธิ์ ({v['valid_from']})"
    elif v["valid_to"] and today > v["valid_to"]:
        res["reason"] = f"สิทธิ์หมดอายุแล้ว ({v['valid_to']})"
    elif v["list_type"] == "deny":
        res.update(reason="อยู่ในบัญชีดำ (Blocklist)", alarm=True)
    elif not v["group_name"]:
        res["reason"] = "ยังไม่ได้กำหนดกลุ่มสิทธิ์"
    elif camera_id and json.loads(v["group_cameras"] or "[]") and \
            int(camera_id) not in [int(c) for c in json.loads(v["group_cameras"] or "[]")]:
        res["reason"] = "กลุ่มนี้ไม่มีสิทธิ์ผ่านช่องทางนี้"
    elif not _in_schedule(json.loads(v["schedule"] or "{}"), now):
        res["reason"] = "นอกช่วงเวลาที่ได้รับอนุญาต"
    else:
        res["decision"] = "granted"
        res["reason"] = f"อนุญาต — {v['group_name']}" + (" (fuzzy match)" if fuzzy else "")
        res["outputs"] = json.loads(v["group_outputs"] or "[]") if v["auto_open"] else []
        res["auto_open"] = bool(v["auto_open"])
    return res
