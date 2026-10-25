"""AI-LPR web server: REST API + live video + WebSocket push + SPA hosting."""
import asyncio
import csv
import io as _io
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import cv2
import numpy as np
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, access, auth, db, hik_capture
from .bus import bus
from .cameras import CameraManager
from .camera import is_network_url
from .hik_capture import HikSDKSource
from .config import DATASET_DIR, PLATE_DIR, SNAP_DIR, WEB_DIR
from .isapi import HikCamera
from .lpr import dataset, thai
from .lpr.engine import LPREngine
from .lpr.trainer import Trainer
from .pipeline import event_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("lpr")

engine = LPREngine()
cams = CameraManager(engine)
trainer = Trainer(engine)
STATE = {"started": time.time()}

ADMIN, OPERATOR, VIEWER = auth.require("admin"), auth.require("operator"), auth.require("viewer")

# image presets for plate reading (DS-TCG406-E style "entrance & exit exposure")
IMAGE_PRESETS = {
    "lpr_gate": {"shutter": "1/250", "wdr": "open", "wdr_level": 50, "hlc": True, "hlc_level": 50,
                 "dnr": "general", "dnr_level": 35, "sharpness": 60},
    "lpr_fast": {"shutter": "1/500", "wdr": "open", "wdr_level": 50, "hlc": True, "hlc_level": 60,
                 "dnr": "general", "dnr_level": 35, "sharpness": 60},
}


def unit_of(cam: int = 0):
    try:
        return cams.get(int(cam or 0))
    except KeyError:
        raise HTTPException(404, "ไม่พบกล้อง")


# ====================================================================== lifecycle
def seed():
    auth.ensure_default_admin()
    if not db.one("SELECT id FROM groups LIMIT 1"):
        all_days = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}
        for name, typ, color, auto, sched, desc in [
            ("พนักงาน", "allow", "#22c55e", 1, all_days, "พนักงานประจำ เข้า-ออกได้ตลอด 24 ชม."),
            ("ผู้บริหาร / VIP", "allow", "#eab308", 1, all_days, "ผู้บริหารและแขก VIP"),
            ("ผู้มาติดต่อ", "visitor", "#3b82f6", 1, {"days": [0, 1, 2, 3, 4, 5], "start": "07:00", "end": "19:00"},
             "ผู้มาติดต่อ จันทร์-เสาร์ 07:00-19:00"),
            ("บัญชีดำ", "deny", "#ef4444", 0, all_days, "ห้ามเข้า แจ้งเตือนทันที"),
        ]:
            db.insert("groups", {"name": name, "type": typ, "color": color, "auto_open": auto, "outputs": "[]",
                                 "schedule": json.dumps(sched), "description": desc, "created_at": db.now_iso()})


def system_status():
    units = [u.status() for u in cams.all()]
    first = units[0] if units else None
    st = trainer.state
    out = {
        "engine": {"status": engine.status, "error": engine.error, "model": engine.model_name,
                   "ocr_model": engine.ocr_model, **engine.stats,
                   "proc_fps": round(sum(u["recognition"]["proc_fps"] for u in units), 1)},
        "cameras": units,
        "train": {"state": st["state"], "progress": st["progress"], "message": st["message"]},
        "uptime": int(time.time() - STATE["started"]),
        "time": db.now_iso(),
    }
    if first:   # legacy single-camera fields (first camera)
        out.update(video=first["video"], io=first["io"], recognition=first["recognition"], tracks=first["tracks"],
                   device={"name": first["name"], "host": first["host"], "model": first["model"]})
    else:
        out.update(video={"connected": False, "fps": 0, "width": 0, "height": 0, "error": "ยังไม่มีกล้อง"},
                   io={"online": False, "conflicts": 0}, recognition={"enabled": False, "mode": "", "armed": False,
                                                                      "armed_left": 0, "latency": 0},
                   tracks=[], device={"name": "ยังไม่มีกล้อง", "host": "", "model": ""})
    return out


async def status_ticker():
    while True:
        await asyncio.sleep(1)
        try:
            bus.publish("status", system_status())
        except Exception:
            log.exception("status tick failed")


def maintenance():
    while True:
        try:
            days = int(db.get_settings()["system"].get("retention_days", 90))
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            old = db.query("SELECT id, snapshot, plate_img FROM events WHERE ts < ?", (cutoff,))
            for e in old:
                for base, rel in ((SNAP_DIR, e["snapshot"]), (PLATE_DIR, e["plate_img"])):
                    if rel:
                        try:
                            os.remove(base / rel)
                        except OSError:
                            pass
            if old:
                db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
                db.execute("DELETE FROM io_events WHERE ts < ?", (cutoff,))
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now_iso(),))
        except Exception:
            log.exception("maintenance failed")
        time.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    default_handler = loop.get_exception_handler()

    def quiet_resets(lp, ctx):  # Windows Proactor noise when a viewer closes the MJPEG stream
        if isinstance(ctx.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
            return
        if default_handler:
            default_handler(lp, ctx)
        else:
            lp.default_exception_handler(ctx)
    loop.set_exception_handler(quiet_resets)
    bus.bind(loop)
    seed()
    engine.load_async(db.get_settings()["recognition"]["detector_model"])
    cams.start()
    threading.Thread(target=maintenance, daemon=True, name="maintenance").start()
    task = asyncio.create_task(status_ticker())
    yield
    task.cancel()
    for u in cams.all():
        u.stop()
    hik_capture.shutdown()        # StopRealPlay / PlayM4 close / Logout for every camera, then NET_DVR_Cleanup


app = FastAPI(title="AI-LPR", version=__version__, lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": str(exc)}, status_code=500)


# ====================================================================== auth
@app.post("/api/auth/login")
def api_login(body: dict = Body(...)):
    res = auth.login(body.get("username", "").strip(), body.get("password", ""))
    if not res:
        db.audit(body.get("username", "?"), "login_failed", "")
        raise HTTPException(401, "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    token, user = res
    db.audit(user["username"], "login", "")
    return {"token": token, "user": user}


@app.post("/api/auth/logout")
def api_logout(request: Request, user=Depends(VIEWER)):
    db.execute("DELETE FROM sessions WHERE token=?", (auth.token_from_request(request),))
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(user=Depends(VIEWER)):
    return user


@app.post("/api/auth/password")
def api_change_password(body: dict = Body(...), user=Depends(VIEWER)):
    u = db.one("SELECT * FROM users WHERE id=?", (user["id"],))
    if not auth.verify_password(body.get("old", ""), u["password_hash"], u["salt"]):
        raise HTTPException(400, "รหัสผ่านเดิมไม่ถูกต้อง")
    if len(body.get("new", "")) < 6:
        raise HTTPException(400, "รหัสผ่านใหม่ต้องมีอย่างน้อย 6 ตัวอักษร")
    h, s = auth.hash_password(body["new"])
    db.update("users", user["id"], {"password_hash": h, "salt": s})
    db.audit(user["username"], "change_password", "")
    return {"ok": True}


# ====================================================================== status / dashboard
@app.get("/api/status")
def api_status(user=Depends(VIEWER)):
    return system_status()


@app.get("/api/dashboard")
def api_dashboard(user=Depends(VIEWER)):
    today = datetime.now().strftime("%Y-%m-%d")
    t = db.one("SELECT COUNT(*) total, SUM(decision='granted') granted, SUM(decision='denied') denied, "
               "SUM(list_type='unknown') unknown, SUM(list_type='deny') blocked, AVG(confidence) conf, "
               "AVG(latency_ms) latency FROM events WHERE ts >= ?", (today,))
    hourly = {r["h"]: r for r in db.query(
        "SELECT CAST(substr(ts,12,2) AS INTEGER) h, COUNT(*) n, SUM(decision='granted') g, "
        "SUM(decision='denied') d FROM events WHERE ts >= ? GROUP BY h", (today,))}
    days = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        r = db.one("SELECT COUNT(*) n, SUM(decision='granted') g FROM events WHERE substr(ts,1,10)=?", (d,))
        days.append({"date": d, "n": r["n"] or 0, "g": r["g"] or 0})
    groups = db.query("SELECT g.id, g.name, g.color, g.type, COUNT(v.id) n FROM groups g "
                      "LEFT JOIN vehicles v ON v.group_id=g.id GROUP BY g.id ORDER BY g.id")
    top = db.query("SELECT plate, province, owner, COUNT(*) n, MAX(ts) last FROM events WHERE ts >= ? "
                   "GROUP BY plate ORDER BY n DESC LIMIT 6", (today,))
    recent = [event_payload(e) for e in db.query("SELECT * FROM events ORDER BY id DESC LIMIT 12")]
    by_cam = db.query("SELECT camera_id, camera, COUNT(*) n, SUM(direction='in') n_in, SUM(direction='out') n_out "
                      "FROM events WHERE ts >= ? GROUP BY camera_id ORDER BY camera_id", (today,))
    return {
        "today": {k: (t[k] or 0) for k in ("total", "granted", "denied", "unknown", "blocked")},
        "avg_conf": round(t["conf"] or 0, 3), "avg_latency": int(t["latency"] or 0),
        "hourly": [{"h": h, "n": (hourly.get(h) or {}).get("n", 0), "g": (hourly.get(h) or {}).get("g", 0),
                    "d": (hourly.get(h) or {}).get("d", 0)} for h in range(24)],
        "days": days, "groups": groups, "top": top, "recent": recent, "by_camera": by_cam,
        "vehicles": db.one("SELECT COUNT(*) n FROM vehicles")["n"],
    }


# ====================================================================== vehicles
def _vehicle_fields(body: dict):
    plate = (body.get("plate") or "").strip()
    if not plate:
        raise HTTPException(400, "กรุณาระบุทะเบียนรถ")
    fmt = db.get_settings()["recognition"].get("plate_format", "TH")
    if fmt == "TH":
        disp, key, score = thai.parse_registration(plate)
        if score < 0.5:
            disp, key = plate, thai.plate_key(plate)
    else:
        disp, key = plate.upper(), thai.plate_key(plate)
    data = {"plate": disp, "plate_key": key}
    for k in ("province", "owner", "phone", "department", "vehicle_type", "brand", "color",
              "valid_from", "valid_to", "note"):
        if k in body:
            data[k] = (body.get(k) or "").strip()
    if "group_id" in body:
        data["group_id"] = int(body["group_id"]) if body.get("group_id") else None
    if "enabled" in body:
        data["enabled"] = 1 if body["enabled"] else 0
    return data


@app.get("/api/vehicles")
def api_vehicles(q: str = "", group_id: int = 0, status: str = "", page: int = 1, size: int = 25, user=Depends(VIEWER)):
    where, args = ["1=1"], []
    if q:
        where.append("(v.plate_key LIKE ? OR v.owner LIKE ? OR v.phone LIKE ? OR v.department LIKE ? OR v.province LIKE ?)")
        k = f"%{thai.plate_key(q)}%"
        args += [k, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
    if group_id:
        where.append("v.group_id=?")
        args.append(group_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if status == "enabled":
        where.append("v.enabled=1")
    elif status == "disabled":
        where.append("v.enabled=0")
    elif status == "expired":
        where.append("v.valid_to != '' AND v.valid_to < ?")
        args.append(today)
    w = " AND ".join(where)
    total = db.one(f"SELECT COUNT(*) n FROM vehicles v WHERE {w}", args)["n"]
    rows = db.query(f"SELECT v.*, g.name group_name, g.color group_color, g.type list_type, "
                    f"(SELECT MAX(ts) FROM events e WHERE e.vehicle_id=v.id) last_seen "
                    f"FROM vehicles v LEFT JOIN groups g ON g.id=v.group_id WHERE {w} "
                    f"ORDER BY v.id DESC LIMIT ? OFFSET ?", (*args, size, (page - 1) * size))
    return {"total": total, "items": rows}


@app.post("/api/vehicles")
def api_vehicle_add(body: dict = Body(...), user=Depends(OPERATOR)):
    data = _vehicle_fields(body)
    if db.one("SELECT id FROM vehicles WHERE plate_key=? AND province=?", (data["plate_key"], data.get("province", ""))):
        raise HTTPException(409, f"ทะเบียน {data['plate']} {data.get('province', '')} มีอยู่ในระบบแล้ว")
    data["created_at"] = data["updated_at"] = db.now_iso()
    data.setdefault("enabled", 1)
    vid = db.insert("vehicles", data)
    db.audit(user["username"], "vehicle_add", f"{data['plate']} {data.get('province', '')}")
    return {"id": vid}


@app.put("/api/vehicles/{vid}")
def api_vehicle_edit(vid: int, body: dict = Body(...), user=Depends(OPERATOR)):
    data = _vehicle_fields(body)
    data["updated_at"] = db.now_iso()
    db.update("vehicles", vid, data)
    db.audit(user["username"], "vehicle_edit", data["plate"])
    return {"ok": True}


@app.delete("/api/vehicles/{vid}")
def api_vehicle_delete(vid: int, user=Depends(OPERATOR)):
    v = db.one("SELECT plate FROM vehicles WHERE id=?", (vid,))
    db.execute("DELETE FROM vehicles WHERE id=?", (vid,))
    db.audit(user["username"], "vehicle_delete", v["plate"] if v else str(vid))
    return {"ok": True}


@app.post("/api/vehicles/bulk")
def api_vehicle_bulk(body: dict = Body(...), user=Depends(OPERATOR)):
    ids = [int(i) for i in body.get("ids", [])]
    action = body.get("action")
    if not ids:
        return {"ok": True}
    marks = ",".join("?" * len(ids))
    if action == "delete":
        db.execute(f"DELETE FROM vehicles WHERE id IN ({marks})", ids)
    elif action in ("enable", "disable"):
        db.execute(f"UPDATE vehicles SET enabled=? WHERE id IN ({marks})", (1 if action == "enable" else 0, *ids))
    elif action == "group":
        db.execute(f"UPDATE vehicles SET group_id=? WHERE id IN ({marks})", (body.get("group_id"), *ids))
    db.audit(user["username"], f"vehicle_bulk_{action}", f"{len(ids)} รายการ")
    return {"ok": True}


VEHICLE_CSV = ["plate", "province", "owner", "phone", "department", "vehicle_type", "brand", "color",
               "group", "valid_from", "valid_to", "enabled", "note"]


@app.get("/api/vehicles/export")
def api_vehicle_export(user=Depends(VIEWER)):
    rows = db.query("SELECT v.*, g.name grp FROM vehicles v LEFT JOIN groups g ON g.id=v.group_id ORDER BY v.id")
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(VEHICLE_CSV)
    for r in rows:
        w.writerow([r["plate"], r["province"], r["owner"], r["phone"], r["department"], r["vehicle_type"],
                    r["brand"], r["color"], r["grp"] or "", r["valid_from"], r["valid_to"], r["enabled"], r["note"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=vehicles.csv"})


@app.post("/api/vehicles/import")
async def api_vehicle_import(file: UploadFile = File(...), user=Depends(OPERATOR)):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    groups = {g["name"]: g["id"] for g in db.query("SELECT id, name FROM groups")}
    added = updated = 0
    errors = []
    for i, row in enumerate(csv.DictReader(_io.StringIO(text)), start=2):
        try:
            body = {k: (row.get(k) or "").strip() for k in VEHICLE_CSV}
            body["group_id"] = groups.get(body.pop("group"), None)
            body["enabled"] = body.get("enabled", "1") not in ("0", "false", "no", "")
            data = _vehicle_fields(body)
            ex = db.one("SELECT id FROM vehicles WHERE plate_key=? AND province=?", (data["plate_key"], data.get("province", "")))
            data["updated_at"] = db.now_iso()
            if ex:
                db.update("vehicles", ex["id"], data)
                updated += 1
            else:
                data["created_at"] = data["updated_at"]
                db.insert("vehicles", data)
                added += 1
        except HTTPException as e:
            errors.append(f"แถว {i}: {e.detail}")
        except Exception as e:
            errors.append(f"แถว {i}: {e}")
    db.audit(user["username"], "vehicle_import", f"เพิ่ม {added} / อัปเดต {updated}")
    return {"added": added, "updated": updated, "errors": errors[:20]}


@app.get("/api/vehicles/template")
def api_vehicle_template(user=Depends(VIEWER)):
    sample = "﻿" + ",".join(VEHICLE_CSV) + "\n1กข 1234,กรุงเทพมหานคร,สมชาย ใจดี,0812345678,ฝ่ายผลิต,car,Toyota,ขาว,พนักงาน,,,1,\n"
    return Response(sample, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=vehicle_template.csv"})


# ====================================================================== groups (access lists)
@app.get("/api/groups")
def api_groups(user=Depends(VIEWER)):
    rows = db.query("SELECT g.*, (SELECT COUNT(*) FROM vehicles v WHERE v.group_id=g.id) vehicles FROM groups g ORDER BY g.id")
    for r in rows:
        r["outputs"] = json.loads(r["outputs"] or "[]")
        r["schedule"] = json.loads(r["schedule"] or "{}")
        r["cameras"] = json.loads(r.get("cameras") or "[]")
    return rows


def _group_fields(body):
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "กรุณาระบุชื่อกลุ่ม")
    if body.get("type") not in ("allow", "visitor", "deny"):
        raise HTTPException(400, "ประเภทรายการไม่ถูกต้อง")
    return {"name": body["name"].strip(), "type": body["type"], "color": body.get("color", "#3b82f6"),
            "auto_open": 1 if body.get("auto_open") else 0,
            "outputs": json.dumps([int(o) for o in body.get("outputs", [])]),
            "cameras": json.dumps([int(c) for c in body.get("cameras", [])]),
            "schedule": json.dumps(body.get("schedule") or {"days": list(range(7)), "start": "00:00", "end": "23:59"}),
            "description": body.get("description", "")}


@app.post("/api/groups")
def api_group_add(body: dict = Body(...), user=Depends(ADMIN)):
    data = _group_fields(body)
    data["created_at"] = db.now_iso()
    gid = db.insert("groups", data)
    db.audit(user["username"], "group_add", data["name"])
    return {"id": gid}


@app.put("/api/groups/{gid}")
def api_group_edit(gid: int, body: dict = Body(...), user=Depends(ADMIN)):
    data = _group_fields(body)
    db.update("groups", gid, data)
    db.audit(user["username"], "group_edit", data["name"])
    return {"ok": True}


@app.delete("/api/groups/{gid}")
def api_group_delete(gid: int, user=Depends(ADMIN)):
    g = db.one("SELECT name FROM groups WHERE id=?", (gid,))
    db.execute("DELETE FROM groups WHERE id=?", (gid,))
    db.audit(user["username"], "group_delete", g["name"] if g else str(gid))
    return {"ok": True}


# ====================================================================== events / logs
def _event_filter(frm, to, plate, decision, list_type, source, cam=0, direction=""):
    where, args = ["1=1"], []
    if frm:
        where.append("ts >= ?")
        args.append(frm.replace("T", " "))
    if to:
        where.append("ts <= ?")
        args.append(to.replace("T", " ") + (":59" if len(to) == 16 else ""))
    if plate:
        where.append("(replace(plate,' ','') LIKE ? OR owner LIKE ?)")
        args += [f"%{thai.plate_key(plate)}%", f"%{plate}%"]
    for col, val in (("decision", decision), ("list_type", list_type), ("source", source), ("direction", direction)):
        if val:
            where.append(f"{col}=?")
            args.append(val)
    if cam:
        where.append("camera_id=?")
        args.append(cam)
    return " AND ".join(where), args


@app.get("/api/events")
def api_events(frm: str = "", to: str = "", plate: str = "", decision: str = "", list_type: str = "",
               source: str = "", cam: int = 0, direction: str = "", page: int = 1, size: int = 30, user=Depends(VIEWER)):
    w, args = _event_filter(frm, to, plate, decision, list_type, source, cam, direction)
    total = db.one(f"SELECT COUNT(*) n FROM events WHERE {w}", args)["n"]
    rows = db.query(f"SELECT * FROM events WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, size, (page - 1) * size))
    return {"total": total, "items": [event_payload(r) for r in rows]}


@app.get("/api/events/export")
def api_events_export(frm: str = "", to: str = "", plate: str = "", decision: str = "", list_type: str = "",
                      source: str = "", cam: int = 0, direction: str = "", user=Depends(VIEWER)):
    w, args = _event_filter(frm, to, plate, decision, list_type, source, cam, direction)
    rows = db.query(f"SELECT * FROM events WHERE {w} ORDER BY id DESC LIMIT 100000", args)
    buf = _io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["เวลา", "ทะเบียน", "จังหวัด", "เจ้าของ", "กลุ่ม", "ประเภท", "ผลลัพธ์", "เหตุผล", "การสั่งงาน",
                 "ความแม่นยำ", "แหล่งที่มา", "กล้อง", "ทิศทาง"])
    for r in rows:
        wr.writerow([r["ts"], r["plate"], r["province"], r["owner"], r["group_name"], r["list_type"], r["decision"],
                     r["reason"], r["gate_action"], r["confidence"], r["source"], r["camera"],
                     {"in": "เข้า", "out": "ออก"}.get(r.get("direction") or "", "")])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=lpr_events.csv"})


@app.post("/api/events/{eid}/correct")
def api_event_correct(eid: int, body: dict = Body(...), user=Depends(OPERATOR)):
    """Operator fixes a misread: store the corrected plate, re-evaluate access and keep the crop for AI training."""
    e = db.one("SELECT * FROM events WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "ไม่พบเหตุการณ์")
    disp, key, _ = thai.parse_registration(body.get("plate", ""))
    prov = body.get("province", e["province"])
    d = access.decide(key, prov, camera_id=e.get("camera_id"))
    db.update("events", eid, {"plate": disp, "province": prov, "decision": d["decision"],
                              "reason": d["reason"] + " (แก้ไขโดยเจ้าหน้าที่)", "list_type": d["list_type"],
                              "group_name": d["group_name"], "group_color": d["group_color"],
                              "owner": (d["vehicle"] or {}).get("owner", ""), "vehicle_id": (d["vehicle"] or {}).get("id")})
    if key and e.get("plate_img"):
        try:
            dataset.add_from_event(eid, key, prov, thai.plate_key(e["plate"] or ""), "verified", "correction")
        except Exception as ex:
            log.warning("training sample from correction failed: %s", ex)
    db.audit(user["username"], "event_correct", f"#{eid} {e['plate']} -> {disp}")
    return event_payload(db.one("SELECT * FROM events WHERE id=?", (eid,)))


@app.get("/api/io-logs")
def api_io_logs(cam: int = 0, page: int = 1, size: int = 50, user=Depends(VIEWER)):
    w, args = ("WHERE camera_id=?", (cam,)) if cam else ("", ())
    total = db.one(f"SELECT COUNT(*) n FROM io_events {w}", args)["n"]
    return {"total": total, "items": db.query(f"SELECT * FROM io_events {w} ORDER BY id DESC LIMIT ? OFFSET ?",
                                              (*args, size, (page - 1) * size))}


@app.get("/api/audit")
def api_audit(page: int = 1, size: int = 50, user=Depends(ADMIN)):
    total = db.one("SELECT COUNT(*) n FROM audit")["n"]
    return {"total": total, "items": db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?",
                                              (size, (page - 1) * size))}


# ====================================================================== users
@app.get("/api/users")
def api_users(user=Depends(ADMIN)):
    return [auth.public_user(u) for u in db.query("SELECT * FROM users ORDER BY id")]


@app.post("/api/users")
def api_user_add(body: dict = Body(...), user=Depends(ADMIN)):
    if not body.get("username") or len(body.get("password", "")) < 6:
        raise HTTPException(400, "ต้องระบุชื่อผู้ใช้ และรหัสผ่านอย่างน้อย 6 ตัวอักษร")
    if body.get("role") not in auth.ROLE_LEVEL:
        raise HTTPException(400, "บทบาทไม่ถูกต้อง")
    if db.one("SELECT id FROM users WHERE username=?", (body["username"],)):
        raise HTTPException(409, "ชื่อผู้ใช้นี้มีอยู่แล้ว")
    uid = auth.create_user(body["username"].strip(), body["password"], body["role"], body.get("full_name", ""))
    db.audit(user["username"], "user_add", body["username"])
    return {"id": uid}


@app.put("/api/users/{uid}")
def api_user_edit(uid: int, body: dict = Body(...), user=Depends(ADMIN)):
    data = {}
    for k in ("full_name", "role"):
        if k in body:
            data[k] = body[k]
    if "enabled" in body:
        if uid == user["id"] and not body["enabled"]:
            raise HTTPException(400, "ไม่สามารถปิดบัญชีของตัวเองได้")
        data["enabled"] = 1 if body["enabled"] else 0
    if body.get("password"):
        if len(body["password"]) < 6:
            raise HTTPException(400, "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร")
        data["password_hash"], data["salt"] = auth.hash_password(body["password"])
    db.update("users", uid, data)
    db.audit(user["username"], "user_edit", str(uid))
    return {"ok": True}


@app.delete("/api/users/{uid}")
def api_user_delete(uid: int, user=Depends(ADMIN)):
    if uid == user["id"]:
        raise HTTPException(400, "ไม่สามารถลบบัญชีของตัวเองได้")
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.audit(user["username"], "user_delete", str(uid))
    return {"ok": True}


# ====================================================================== settings
@app.get("/api/settings")
def api_settings(user=Depends(VIEWER)):
    s = db.get_settings()
    s.pop("camera", None)            # legacy section — cameras live in /api/cameras
    return s


@app.put("/api/settings/{section}")
def api_settings_save(section: str, body: dict = Body(...), user=Depends(ADMIN)):
    if section not in ("recognition", "access", "io", "system", "ai"):
        raise HTTPException(404, "unknown section")
    old = db.get_settings()
    db.save_settings(section, body)
    db.audit(user["username"], f"settings_{section}", json.dumps(body, ensure_ascii=False)[:500])
    if section == "recognition" and body.get("detector_model") and body["detector_model"] != old["recognition"]["detector_model"]:
        engine.load_async(body["detector_model"])
    return db.get_settings()[section]


# ====================================================================== cameras
def _cam_public(u, admin: bool):
    r = {k: v for k, v in u.row.items() if k != "config"}
    r["config"] = u.lane()
    r["enabled"] = bool(r["enabled"])
    r["has_password"] = bool(u.row.get("password"))
    r["password"] = ""            # never echo passwords; a blank field on save keeps the stored one
    if not admin:
        r["username"] = ""
    r["device"] = u.device
    return r


@app.get("/api/cameras")
def api_cameras(user=Depends(VIEWER)):
    return [_cam_public(u, user["role"] == "admin") for u in cams.all()]


@app.get("/api/cameras/{cid}")
def api_camera(cid: int, user=Depends(VIEWER)):
    return _cam_public(unit_of(cid), user["role"] == "admin")


def _cam_body(body):
    data = {k: body[k] for k in ("name", "host", "http_port", "rtsp_port", "username", "password", "stream",
                                 "source_override", "lane", "sort", "capture", "sdk_port", "channel") if k in body}
    if "enabled" in body:
        data["enabled"] = 1 if body["enabled"] else 0
    if "lane" in data and data["lane"] not in ("entry", "exit", "both"):
        raise HTTPException(400, "ประเภทช่องทางไม่ถูกต้อง")
    if "capture" in data:
        data["capture"] = "sdk"           # video comes only from HCNetSDK (old "rtsp" values are ignored)
    if data.get("source_override") and is_network_url(data["source_override"]):
        raise HTTPException(400, "แหล่งวิดีโอทดแทนต้องเป็นไฟล์วิดีโอในเครื่อง — ภาพจากกล้องใช้ HCNetSDK เท่านั้น")
    for k in ("http_port", "rtsp_port", "sort", "sdk_port", "channel"):
        if k in data:
            data[k] = int(data[k] or 0)
    if "config" in body:
        data["config"] = body["config"]
    return data


@app.post("/api/cameras")
def api_camera_add(body: dict = Body(...), user=Depends(ADMIN)):
    data = _cam_body(body)
    if not (data.get("name") or "").strip():
        raise HTTPException(400, "กรุณาระบุชื่อกล้อง")
    if not data.get("host") and not data.get("source_override"):
        raise HTTPException(400, "กรุณาระบุ IP ของกล้อง หรือแหล่งวิดีโอ")
    u = cams.add(data)
    db.audit(user["username"], "camera_add", f"#{u.id} {u.name} {u.row.get('host', '')}")
    return _cam_public(u, True)


@app.put("/api/cameras/{cid}")
def api_camera_edit(cid: int, body: dict = Body(...), user=Depends(ADMIN)):
    unit_of(cid)
    u = cams.update(cid, _cam_body(body))
    db.audit(user["username"], "camera_edit", f"#{cid} " + json.dumps({k: v for k, v in body.items() if k != "password"},
                                                                       ensure_ascii=False)[:400])
    return _cam_public(u, True)


@app.delete("/api/cameras/{cid}")
def api_camera_delete(cid: int, user=Depends(ADMIN)):
    u = unit_of(cid)
    if len(cams.all()) <= 1:
        raise HTTPException(400, "ต้องมีกล้องอย่างน้อย 1 ตัว")
    name = u.name
    cams.remove(cid)
    db.audit(user["username"], "camera_delete", f"#{cid} {name}")
    return {"ok": True}


@app.post("/api/cameras/test")
def api_camera_test(body: dict = Body(...), user=Depends(ADMIN)):
    c = dict(body)
    if body.get("id") and not body.get("password"):
        c["password"] = unit_of(int(body["id"])).row.get("password", "")
    res = {"isapi": {}, "video": {}, "io": {}}
    cam = None
    if c.get("host"):
        cam = HikCamera(c["host"], c.get("username", "admin"), c.get("password", ""), c.get("http_port") or 80)
        t = time.time()
        try:
            info = cam.device_info()
            res["isapi"] = {"ok": True, "ms": int((time.time() - t) * 1000), "model": info.get("model"),
                            "serial": info.get("serialNumber"), "firmware": info.get("firmwareVersion")}
        except Exception as e:
            res["isapi"] = {"ok": False, "error": str(e)}
        try:
            res["io"] = {"ok": True, "outputs": len(cam.outputs()), "inputs": len(cam.inputs())}
        except Exception as e:
            res["io"] = {"ok": False, "error": str(e)}
    else:
        res["isapi"] = res["io"] = {"ok": False, "error": "ไม่ได้ระบุ IP"}
    t = time.time()
    src_file = (c.get("source_override") or "").strip()
    if src_file:                                   # local test video file (not a camera)
        ok = not is_network_url(src_file) and os.path.isfile(src_file)
        res["video"] = {"ok": ok, "ms": 0, "label": "ไฟล์วิดีโอทดสอบ",
                        "error": "" if ok else "ต้องเป็นไฟล์วิดีโอในเครื่อง (ไม่รองรับ URL เครือข่าย)"}
        return res
    if not c.get("host"):
        res["video"] = {"ok": False, "label": "HCNetSDK", "error": "ไม่ได้ระบุ IP"}
        return res
    # video test = HCNetSDK only: NET_DVR_Login_V40 on the SDK port (8000) + live stream until the first frame
    port = int(c.get("sdk_port") or 8000)
    src = HikSDKSource("sdk-test")
    src.start({"host": c["host"], "port": port, "username": c.get("username", "admin"),
               "password": c.get("password", ""), "channel": int(c.get("channel") or 1),
               "stream_type": 1 if str(c.get("stream") or "101").endswith("2") else 0})
    frame = src.read(timeout=12)
    err = src.error
    src.stop(join=True)
    ok = frame is not None
    res["video"] = {"ok": ok, "ms": int((time.time() - t) * 1000), "label": f"HCNetSDK (login port {port})",
                    "resolution": f"{frame.shape[1]}x{frame.shape[0]}" if ok else "",
                    "error": "" if ok else (err or "ไม่ได้รับภาพผ่าน HCNetSDK ภายใน 12 วินาที")}
    return res


@app.get("/api/cameras/{cid}/image")
def api_camera_image(cid: int, user=Depends(VIEWER)):
    u = unit_of(cid)
    if not u.cam:
        raise HTTPException(400, "กล้องนี้ไม่มี ISAPI")
    try:
        return {"values": u.cam.image_settings(), "presets": IMAGE_PRESETS,
                "backup": u.lane().get("image_backup")}
    except Exception as e:
        raise HTTPException(502, f"อ่านค่าภาพจากกล้องไม่สำเร็จ: {e}")


@app.put("/api/cameras/{cid}/image")
def api_camera_image_set(cid: int, body: dict = Body(...), user=Depends(ADMIN)):
    """body: {"preset": "lpr_gate"} | {"restore": true} | {"values": {...}}"""
    u = unit_of(cid)
    if not u.cam:
        raise HTTPException(400, "กล้องนี้ไม่มี ISAPI")
    try:
        cur = u.cam.image_settings()
        if body.get("restore"):
            values = u.lane().get("image_backup") or {}
            if not values:
                raise HTTPException(400, "ไม่มีค่าสำรองก่อนหน้า")
        else:
            values = IMAGE_PRESETS.get(body.get("preset")) or body.get("values") or {}
            if not u.lane().get("image_backup"):   # keep the installer's original values once
                cams.update(cid, {"config": {"image_backup": {k: cur[k]["value"] for k in values if k in cur}}})
        values = {k: v for k, v in values.items() if k in cur}
        u.cam.set_image(values)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"ตั้งค่าภาพในกล้องไม่สำเร็จ: {e}")
    db.audit(user["username"], "camera_image", f"#{cid} " + json.dumps(values, ensure_ascii=False))
    return {"values": u.cam.image_settings()}


# ====================================================================== device & I/O (per camera: ?cam=ID, default first)
@app.get("/api/device")
def api_device(cam: int = 0, user=Depends(VIEWER)):
    u = unit_of(cam)
    out = {"cam": u.id, "info": u.device, "stream": {}, "video": u.video.status()}
    if not u.cam:
        out["error"] = "ไม่มี ISAPI (ใช้แหล่งวิดีโอทดแทน)"
        return out
    try:
        out["info"] = u.device = u.cam.device_info()
        out["stream"] = u.cam.stream_info(u.row.get("stream") or "101")
        out["time"] = u.cam.device_time()
    except Exception as e:
        out["error"] = str(e)
    return out


@app.post("/api/device/test")
def api_device_test(body: dict = Body(...), user=Depends(ADMIN)):
    return api_camera_test(body, user)


@app.get("/api/device/snapshot")
def api_device_snapshot(cam: int = 0, quality: int = 92, user=Depends(VIEWER)):
    """Snapshot = the newest frame decoded from the HCNetSDK live stream (no ISAPI picture request)."""
    u = unit_of(cam)
    frame, _, ts = u.video.latest()
    if frame is None or time.time() - ts > 5:
        raise HTTPException(503, f"ยังไม่มีภาพจากกล้อง: {u.video.status().get('error') or 'กำลังเชื่อมต่อ HCNetSDK'}")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, max(40, min(100, quality))])
    if not ok:
        raise HTTPException(500, "เข้ารหัส JPEG ไม่สำเร็จ")
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/io")
def api_io(cam: int = 0, user=Depends(VIEWER)):
    u = unit_of(cam)
    return {**u.iom.snapshot(), "config": u.lane()["io"]}


@app.post("/api/io/refresh")
def api_io_refresh(cam: int = 0, user=Depends(OPERATOR)):
    u = unit_of(cam)
    u.iom.refresh_ports()
    return u.iom.snapshot()


@app.get("/api/io/linkage")
def api_io_linkage(cam: int = 0, user=Depends(VIEWER)):
    return {"conflicts": unit_of(cam).iom.check_conflicts()}


@app.post("/api/io/linkage/fix")
def api_io_linkage_fix(cam: int = 0, body: dict = Body(default={}), user=Depends(ADMIN)):
    """Remove camera-side event linkages (motion, tamper...) that fire the barrier relay by themselves."""
    try:
        left = unit_of(cam).iom.fix_conflicts(body.get("ids") or None)
    except Exception as e:
        raise HTTPException(502, f"แก้ไข Linkage ในกล้องไม่สำเร็จ: {e}")
    db.audit(user["username"], "io_linkage_fix", f"cam {cam}: " + ",".join(body.get("ids") or ["all"]))
    return {"conflicts": left}


@app.post("/api/io/output/{port}")
def api_io_output(port: int, cam: int = 0, body: dict = Body(...), user=Depends(OPERATOR)):
    u = unit_of(cam or body.get("cam") or 0)
    action = body.get("action", "pulse")
    try:
        if action == "pulse":
            u.iom.pulse(port, int(body.get("ms") or 0) or int(u.lane()["io"].get("pulse_ms", 1000)),
                        source="manual", user=user["username"])
        elif action in ("high", "low"):
            u.iom.set_output(port, action == "high", source="manual", user=user["username"])
        else:
            raise HTTPException(400, "action ไม่ถูกต้อง")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"สั่งงาน Alarm Output ไม่สำเร็จ: {e}")
    db.audit(user["username"], "io_output", f"[{u.name}] OUT{port} {action}")
    return u.iom.snapshot()


@app.put("/api/io/output/{port}/config")
def api_io_output_cfg(port: int, cam: int = 0, body: dict = Body(...), user=Depends(ADMIN)):
    u = unit_of(cam)
    try:
        u.cam.configure_output(port, body.get("name", f"Output {port}"), body.get("default_state", "low"),
                               body.get("output_state", "pulse"), int(body.get("pulse_ms", 1000)))
    except Exception as e:
        raise HTTPException(502, f"บันทึกค่า Output ไปยังกล้องไม่สำเร็จ: {e}")
    u.iom.refresh_ports()
    db.audit(user["username"], "io_output_config", f"[{u.name}] OUT{port}")
    return u.iom.snapshot()


@app.put("/api/io/input/{port}/config")
def api_io_input_cfg(port: int, cam: int = 0, body: dict = Body(...), user=Depends(ADMIN)):
    u = unit_of(cam)
    try:
        u.cam.configure_input(port, body.get("name", f"Input {port}"), bool(body.get("enabled", True)),
                              body.get("triggering", "high"))
    except Exception as e:
        raise HTTPException(502, f"บันทึกค่า Input ไปยังกล้องไม่สำเร็จ: {e}")
    inputs = u.lane()["io"].get("inputs", {})
    inputs[str(port)] = {"role": body.get("role", "none"), "target": int(body.get("target") or 0)}
    cams.update(u.id, {"config": {"io": {"inputs": inputs}}})
    u.iom.refresh_ports()
    db.audit(user["username"], "io_input_config", f"[{u.name}] IN{port} role={body.get('role')} target={body.get('target')}")
    return u.iom.snapshot()


@app.post("/api/gate/open")
def api_gate_open(body: dict = Body(default={}), user=Depends(OPERATOR)):
    u = unit_of(body.get("cam") or 0)
    try:
        port, owner = u.open_gate(body.get("output_id"), source="manual", user=user["username"], detail=f"by {user['username']}")
    except Exception as e:
        raise HTTPException(502, f"เปิดไม้กั้นไม่สำเร็จ: {e}")
    db.audit(user["username"], "gate_open", f"[{u.name}] OUT{port}")
    bus.publish("gate", {"cam": u.id, "port": port, "user": user["username"], "ts": db.now_iso()})
    return {"ok": True, "port": port}


@app.post("/api/recognition/arm")
def api_arm(body: dict = Body(default={}), user=Depends(OPERATOR)):
    u = unit_of(body.get("cam") or 0)
    if u.rec_cfg().get("trigger_mode") == "mixed":
        u.rec.on_input_trigger(0)
    else:
        u.rec.arm(float(body.get("seconds", 6)), "manual")
    return {"ok": True}


# ====================================================================== test center
@app.post("/api/test/capture")
def api_test_capture(body: dict = Body(default={}), user=Depends(OPERATOR)):
    if engine.status != "ready":
        raise HTTPException(503, "AI Engine ยังไม่พร้อมใช้งาน")
    try:
        return unit_of(body.get("cam") or 0).rec.capture_now(commit=bool(body.get("commit", False)), source="manual",
                                                            user=user["username"])
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/test/image")
async def api_test_image(file: UploadFile = File(...), commit: bool = Form(False), open_gate: bool = Form(False),
                         cam: int = Form(0), user=Depends(OPERATOR)):
    if engine.status != "ready":
        raise HTTPException(503, "AI Engine ยังไม่พร้อมใช้งาน")
    img = cv2.imdecode(np.frombuffer(await file.read(), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "ไฟล์รูปภาพไม่ถูกต้อง")
    return await asyncio.to_thread(unit_of(cam).rec.analyze_image, img, commit, open_gate)


@app.post("/api/test/simulate")
def api_test_simulate(body: dict = Body(...), user=Depends(OPERATOR)):
    if not body.get("plate"):
        raise HTTPException(400, "กรุณาระบุทะเบียน")
    db.audit(user["username"], "test_simulate", body["plate"])
    return unit_of(body.get("cam") or 0).rec.simulate(body["plate"], body.get("province", ""), bool(body.get("act", False)))


@app.post("/api/test/decide")
def api_test_decide(body: dict = Body(...), user=Depends(VIEWER)):
    disp, key, _ = thai.parse_registration(body.get("plate", ""))
    return {"plate": disp, **access.decide(key or thai.plate_key(body.get("plate", "")), body.get("province", ""),
                                           camera_id=body.get("cam") or None)}


# ====================================================================== AI training
@app.get("/api/ai/summary")
def api_ai_summary(user=Depends(VIEWER)):
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM ai_samples GROUP BY status")}
    models = db.query("SELECT * FROM ai_models ORDER BY id DESC")
    active = int(db.get_settings()["ai"].get("active_model", 0) or 0)
    chars = {}
    for r in db.query("SELECT label FROM ai_samples WHERE status IN ('verified','auto')"):
        for ch in r["label"]:
            if not ch.isdigit():
                chars[ch] = chars.get(ch, 0) + 1
    review = db.one("SELECT COUNT(*) n FROM events e WHERE e.plate_img != '' AND e.source IN ('line','video','input','manual') "
                    "AND NOT EXISTS (SELECT 1 FROM ai_samples s WHERE s.event_id=e.id)")["n"]
    return {"counts": counts, "models": models, "active": active, "ocr_model": engine.ocr_model,
            "confusion": dataset.confusion(), "letters": sorted(chars.items(), key=lambda kv: -kv[1]),
            "review_pending": review, "trainer": trainer.status(), "min_samples": 20}


@app.get("/api/ai/review")
def api_ai_review(page: int = 1, size: int = 24, order: str = "conf", cam: int = 0, user=Depends(OPERATOR)):
    """Recent reads that are not in the dataset yet (lowest confidence first = most useful to check)."""
    where = ("e.plate_img != '' AND e.source IN ('line','video','input','manual') "
             "AND NOT EXISTS (SELECT 1 FROM ai_samples s WHERE s.event_id=e.id)")
    args = []
    if cam:
        where += " AND e.camera_id=?"
        args.append(cam)
    total = db.one(f"SELECT COUNT(*) n FROM events e WHERE {where}", args)["n"]
    rows = db.query(f"SELECT * FROM events e WHERE {where} ORDER BY {'e.confidence ASC' if order == 'conf' else 'e.id DESC'} "
                    f"LIMIT ? OFFSET ?", (*args, size, (page - 1) * size))
    return {"total": total, "items": [event_payload(r) for r in rows]}


@app.post("/api/ai/review/{eid}")
def api_ai_review_save(eid: int, body: dict = Body(...), user=Depends(OPERATOR)):
    """Confirm (label == read) / correct (new label) / skip an event read."""
    e = db.one("SELECT * FROM events WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "ไม่พบเหตุการณ์")
    read_key = thai.plate_key(e["plate"] or "")
    if body.get("action") == "skip":
        sid = dataset.add_from_event(eid, read_key or "-", e["province"] or "", read_key, "excluded", "skip")
        return {"id": sid}
    disp, key, score = thai.parse_registration(body.get("plate") or e["plate"] or "")
    if not key or score < 0.5:
        raise HTTPException(400, "รูปแบบทะเบียนไม่ถูกต้อง")
    prov = body.get("province", e["province"] or "")
    try:
        sid = dataset.add_from_event(eid, key, prov, read_key, "verified", "confirm" if key == read_key else "correction")
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if key != read_key or prov != (e["province"] or ""):   # also fix the event record
        api_event_correct(eid, {"plate": disp, "province": prov}, user)
    return {"id": sid}


@app.get("/api/ai/samples")
def api_ai_samples(status: str = "", q: str = "", page: int = 1, size: int = 48, user=Depends(VIEWER)):
    where, args = ["1=1"], []
    if status:
        where.append("status=?")
        args.append(status)
    if q:
        where.append("label LIKE ?")
        args.append(f"%{thai.plate_key(q)}%")
    w = " AND ".join(where)
    total = db.one(f"SELECT COUNT(*) n FROM ai_samples WHERE {w}", args)["n"]
    rows = db.query(f"SELECT * FROM ai_samples WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, size, (page - 1) * size))
    for r in rows:
        r["image_url"] = f"/api/media/dataset/{r['image']}"
        r["label_text"] = thai.parse_registration(r["label"])[0] if r["label"] and r["label"] != "-" else r["label"]
    return {"total": total, "items": rows}


@app.put("/api/ai/samples/{sid}")
def api_ai_sample_edit(sid: int, body: dict = Body(...), user=Depends(OPERATOR)):
    data = {}
    if "plate" in body:
        _, key, score = thai.parse_registration(body["plate"])
        if not key or score < 0.5:
            raise HTTPException(400, "รูปแบบทะเบียนไม่ถูกต้อง")
        data["label"] = key
    if body.get("status") in ("verified", "auto", "excluded"):
        data["status"] = body["status"]
    if "province" in body:
        data["province"] = body["province"]
    db.update("ai_samples", sid, data)
    return {"ok": True}


@app.post("/api/ai/samples/bulk")
def api_ai_sample_bulk(body: dict = Body(...), user=Depends(OPERATOR)):
    ids = [int(i) for i in body.get("ids", [])]
    act = body.get("action")
    for i in ids:
        if act == "delete":
            dataset.delete(i)
        elif act in ("verified", "excluded", "auto"):
            db.update("ai_samples", i, {"status": act})
    return {"ok": True, "n": len(ids)}


@app.delete("/api/ai/samples/{sid}")
def api_ai_sample_delete(sid: int, user=Depends(OPERATOR)):
    dataset.delete(sid)
    return {"ok": True}


@app.post("/api/ai/samples/upload")
async def api_ai_upload(file: UploadFile = File(...), plate: str = Form(...), province: str = Form(""),
                        user=Depends(OPERATOR)):
    """Upload a plate image (close-up or full scene) with its true registration."""
    if engine.status != "ready":
        raise HTTPException(503, "AI Engine ยังไม่พร้อมใช้งาน")
    _, key, score = thai.parse_registration(plate)
    if not key or score < 0.5:
        raise HTTPException(400, "รูปแบบทะเบียนไม่ถูกต้อง")
    img = cv2.imdecode(np.frombuffer(await file.read(), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "ไฟล์รูปภาพไม่ถูกต้อง")
    crop, read_key = img, ""
    dets = await asyncio.to_thread(engine.detect, img, 0.25)
    if dets:      # full scene: keep only the plate (like the live pipeline)
        bbox, _ = max(dets, key=lambda d: (d[0][2] - d[0][0]) * d[0][3])
        if (bbox[2] - bbox[0]) < img.shape[1] * 0.85:
            crop = engine._crop(img, bbox)
    r = await asyncio.to_thread(engine.read_crop, crop, (0, 0, crop.shape[1], crop.shape[0]), 1.0,
                                db.get_settings()["recognition"])
    read_key = r.key if r else ""
    sid = dataset.add_image(crop, key, province, read_key)
    return {"id": sid, "read": read_key, "label": key, "correct": read_key == key}


@app.post("/api/ai/train")
def api_ai_train(body: dict = Body(default={}), user=Depends(ADMIN)):
    try:
        trainer.start({"epochs": int(body.get("epochs", 8)), "augment": int(body.get("augment", 3)),
                       "include_auto": bool(body.get("include_auto", True)), "activate": bool(body.get("activate", True)),
                       "lr": float(body.get("lr", 2e-4))})
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    db.audit(user["username"], "ai_train", json.dumps(body))
    return trainer.status()


@app.post("/api/ai/train/cancel")
def api_ai_train_cancel(user=Depends(ADMIN)):
    trainer.cancel()
    return {"ok": True}


@app.post("/api/ai/models/{mid}/activate")
def api_ai_model_activate(mid: int, user=Depends(ADMIN)):
    row = db.one("SELECT * FROM ai_models WHERE id=?", (mid,)) if mid else None
    if mid and not row:
        raise HTTPException(404, "ไม่พบโมเดล")
    try:
        engine.activate_model(row)
    except Exception as e:
        raise HTTPException(500, f"โหลดโมเดลไม่สำเร็จ: {e}")
    db.save_settings("ai", {"active_model": mid})
    db.audit(user["username"], "ai_model_activate", row["name"] if row else "base")
    return {"ok": True, "ocr_model": engine.ocr_model}


@app.delete("/api/ai/models/{mid}")
def api_ai_model_delete(mid: int, user=Depends(ADMIN)):
    if int(db.get_settings()["ai"].get("active_model", 0) or 0) == mid:
        raise HTTPException(400, "โมเดลนี้กำลังใช้งานอยู่ — เปลี่ยนไปใช้โมเดลอื่นก่อน")
    from .config import MODEL_DIR
    row = db.one("SELECT * FROM ai_models WHERE id=?", (mid,))
    if row:
        for f in (row["onnx"], row["weights"]):
            try:
                os.remove(MODEL_DIR / f)
            except OSError:
                pass
        db.execute("DELETE FROM ai_models WHERE id=?", (mid,))
    return {"ok": True}


# ====================================================================== media / live
@app.get("/api/media/{kind}/{path:path}")
def api_media(kind: str, path: str, user=Depends(VIEWER)):
    base = {"snapshots": SNAP_DIR, "plates": PLATE_DIR, "dataset": DATASET_DIR}.get(kind)
    if not base:
        raise HTTPException(404)
    f = (base / path).resolve()
    if base.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/live.jpg")
async def api_live_jpg(width: int = 1280, cam: int = 0, user=Depends(VIEWER)):
    u = unit_of(cam)
    data = await asyncio.to_thread(u.rec.render_live, min(width, 2688), 80)
    if not data:
        raise HTTPException(503, "ยังไม่มีภาพจากกล้อง")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/live.mjpg")
async def api_live_mjpg(request: Request, width: int = 0, fps: int = 15, cam: int = 0, user=Depends(VIEWER)):
    u = unit_of(cam)
    sysc = db.get_settings()["system"]
    width = width or sysc.get("live_width", 1280)
    quality = sysc.get("live_quality", 72)
    period = 1 / max(1, min(fps, 30))

    async def gen():
        # paced by the camera: send each new frame as soon as it exists (no duplicates, no fixed-timer jitter)
        last = -1
        while not await request.is_disconnected():
            fid = u.video.frame_id
            if fid == last:
                await asyncio.sleep(0.004)
                continue
            t = time.time()
            data = await asyncio.to_thread(u.rec.render_live, width, quality)
            last = fid
            if data:
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
            await asyncio.sleep(max(0.0, period - (time.time() - t)))
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    user = auth.user_from_token(websocket.query_params.get("token", ""))
    if not user:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    q = bus.subscribe()
    try:
        await websocket.send_text(json.dumps({"type": "status", "data": system_status()}, ensure_ascii=False, default=str))
        for u in cams.all():
            await websocket.send_text(json.dumps({"type": "io", "data": u.iom.snapshot()}, ensure_ascii=False, default=str))
        while True:
            await websocket.send_text(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


# ====================================================================== SPA
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
