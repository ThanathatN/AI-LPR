"""Training dataset of real plate crops from this site (for fine-tuning the Thai OCR).

Samples come from
  * operator corrections of events            -> status 'verified' (the most valuable data)
  * operator confirmation in the review queue -> 'verified'
  * high-confidence exact matches of registered plates -> 'auto' (optional in training)
  * uploaded images                           -> 'verified'
Crops are copied into data/dataset so they survive the event retention clean-up.
"""
import os
import shutil
from datetime import datetime

import cv2

from .. import db
from ..config import DATASET_DIR, PLATE_DIR


def _new_path(ext=".jpg"):
    day = datetime.now().strftime("%Y%m%d")
    os.makedirs(DATASET_DIR / day, exist_ok=True)
    return f"{day}/{datetime.now().strftime('%H%M%S_%f')}{ext}"


def add_from_event(event_id: int, label: str, province: str = "", read_key: str = "", status="verified",
                   source="confirm"):
    e = db.one("SELECT * FROM events WHERE id=?", (event_id,))
    if not e or not e.get("plate_img"):
        raise ValueError("เหตุการณ์นี้ไม่มีภาพป้ายทะเบียน")
    ex = db.one("SELECT * FROM ai_samples WHERE event_id=?", (event_id,))
    if ex:
        if ex["status"] == "verified" and status == "auto":
            return ex["id"]
        db.update("ai_samples", ex["id"], {"label": label, "province": province, "status": status, "source": source})
        return ex["id"]
    src = PLATE_DIR / e["plate_img"]
    if not src.is_file():
        raise ValueError("ไม่พบไฟล์ภาพป้าย (อาจถูกลบตามระยะเวลาเก็บข้อมูล)")
    rel = _new_path()
    shutil.copyfile(src, DATASET_DIR / rel)
    if not read_key:
        from . import thai
        read_key = thai.plate_key(e["plate"] or "")
    return db.insert("ai_samples", {"created_at": db.now_iso(), "image": rel, "label": label, "province": province,
                                    "read_key": read_key, "status": status, "source": source,
                                    "event_id": event_id, "camera_id": e.get("camera_id")})


def add_image(img, label: str, province: str = "", read_key: str = "", source="upload", status="verified"):
    rel = _new_path()
    cv2.imwrite(str(DATASET_DIR / rel), img, [cv2.IMWRITE_JPEG_QUALITY, 94])
    return db.insert("ai_samples", {"created_at": db.now_iso(), "image": rel, "label": label, "province": province,
                                    "read_key": read_key, "status": status, "source": source})


def delete(sample_id: int):
    s = db.one("SELECT image FROM ai_samples WHERE id=?", (sample_id,))
    if s:
        try:
            os.remove(DATASET_DIR / s["image"])
        except OSError:
            pass
    db.execute("DELETE FROM ai_samples WHERE id=?", (sample_id,))


def _subs(a: str, b: str):
    """Character substitutions/insertions/deletions turning read `a` into truth `b` (Levenshtein backtrace)."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    out, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            if a[i - 1] != b[j - 1]:
                out.append((a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            out.append((a[i - 1], "∅"))
            i -= 1
        else:
            out.append(("∅", b[j - 1]))
            j -= 1
    return out


def confusion(limit=24):
    """Most frequent misreads among verified samples: [{read, truth, n}]."""
    counts = {}
    for s in db.query("SELECT read_key, label FROM ai_samples WHERE status='verified' AND read_key != '' AND read_key != label"):
        for pair in _subs(s["read_key"], s["label"]):
            counts[pair] = counts.get(pair, 0) + 1
    return [{"read": a, "truth": b, "n": n} for (a, b), n in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]
