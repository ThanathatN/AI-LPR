"""Plate detection (YOLOv9 ONNX) + Thai plate OCR.

Thai plate reading pipeline (per crop):
  1. deskew the plate (projection-profile search)            -> fixes tilted plates
  2. CRAFT text detector finds the registration + province lines
  3. two image views of each line (high-res raw, CLAHE+padding)
  4. the OCR network's full CTC probability matrix for each view
  5. candidates from prefix beam search, scored EXACTLY on every view, plus
       - Thai plate grammar prior (2 consonants + digits is most likely)
       - small bonus for plates registered in the system (helps ห/ท, ธ/5 ...)
  6. province = best of the 77 real province names scored on the province line
Several reads of the same vehicle are combined by summing their log-probabilities (consensus()).
"""
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import MODEL_DIR
from . import thai
from .ctc import NEG, CTCReader, beam, export_onnx, posterior, score_many

log = logging.getLogger("lpr.engine")
TUNED_PARTS = ("SequenceModeling", "Prediction")     # layers changed by site fine-tuning (see trainer.py)


@dataclass
class PlateRead:
    bbox: tuple              # x1, y1, x2, y2 in source-frame pixels
    det_conf: float
    text: str = ""           # display text, e.g. "1กข 1234"
    key: str = ""            # matching key, e.g. "1กข1234"
    province: str = ""
    raw: str = ""
    ocr_conf: float = 0.0
    format_score: float = 0.0
    conf: float = 0.0        # combined confidence 0..1
    crop: np.ndarray = field(default=None, repr=False)
    ms: int = 0
    mats: list = field(default_factory=list, repr=False)       # registration-line CTC matrices
    prov_mats: list = field(default_factory=list, repr=False)  # province-line CTC matrices
    cands: list = field(default_factory=list, repr=False)      # candidate keys from this read

    def to_dict(self):
        return {"bbox": list(map(int, self.bbox)), "det_conf": round(self.det_conf, 3), "text": self.text,
                "key": self.key, "province": self.province, "raw": self.raw,
                "ocr_conf": round(self.ocr_conf, 3), "format_score": round(self.format_score, 2),
                "conf": round(self.conf, 3), "ms": self.ms}


# ---------------------------------------------------------------------------------- scoring helpers
def _near_hints(cands, hints, max_dist=2):
    if not hints:
        return set()
    top = sorted(cands)[:12]
    return {h for h in hints if any(abs(len(h) - len(c)) <= 1 and thai.edit_distance(h, c) <= max_dist for c in top)}


def rank(mats_per_read, cands, hints=frozenset(), bias=1.0) -> dict:
    """Sum over reads of the mean log P(candidate | views of that read) + grammar prior + list bias."""
    cands = [c for c in cands if thai.plate_prior(c) is not None]
    if not cands:
        return {}
    total = {c: 0.0 for c in cands}
    for mats in mats_per_read:
        if not mats:
            continue
        acc = {c: 0.0 for c in cands}
        for lp, chars in mats:
            for c, v in score_many(lp, chars, cands).items():
                acc[c] += v
        for c in cands:
            total[c] += acc[c] / len(mats)
    for c in cands:
        total[c] += thai.plate_prior(c) + (bias if c in hints else 0.0)
    return {c: v for c, v in total.items() if v > NEG / 2}


def rank_province(prov_mats_per_read):
    """Best real province name for the province line(s); '' when the evidence is too weak."""
    total = {p: 0.0 for p in thai.PROVINCES}
    n = 0
    for mats in prov_mats_per_read:
        if not mats:
            continue
        n += 1
        acc = {p: 0.0 for p in thai.PROVINCES}
        for lp, chars in mats:
            for p, v in score_many(lp, chars, thai.PROVINCES).items():
                acc[p] += v
        for p in total:
            total[p] += acc[p] / len(mats)
    if not n:
        return "", 0.0
    best, post = posterior({p: v for p, v in total.items() if v > NEG / 2})
    if not best or post < 0.35 or total[best] / n < -40:
        return "", post
    return best, post


def consensus(reads: list, hints=frozenset(), bias=1.0):
    """Combine several reads of the SAME vehicle.

    Thai reads carry their CTC matrices: every candidate any read proposed is re-scored on every
    read and the log-probabilities are summed — a frame that saw ห clearly outweighs two blurry
    frames that leaned to ท. Returns (key, posterior 0..1, support, best_read) or None.
    """
    rs = [r for r in reads if r.mats]
    if not rs:
        return _char_vote(reads)
    cands = set()
    for r in rs:
        cands.update(r.cands)
    cands |= _near_hints(cands, hints)
    scores = rank([r.mats for r in rs], cands, hints, bias)
    key, post = posterior(scores)
    if not key:
        return None
    support = sum(1 for r in rs if r.key == key)
    best = max(rs, key=lambda r: (r.key == key, r.conf))
    return key, post, support, best


def province_consensus(reads: list) -> str:
    return rank_province([r.prov_mats for r in reads if r.prov_mats])[0]


def _char_vote(reads):
    """Fallback for non-Thai plates: per-position vote inside the most common digit/letter shape."""
    pool = [r for r in reads if r.format_score >= 0.7] or [r for r in reads if r.key]
    if not pool:
        return None
    shape = lambda k: "".join("D" if c.isdigit() else "L" for c in k)
    groups = {}
    for r in pool:
        groups.setdefault(shape(r.key), []).append(r)
    g = max(groups.values(), key=lambda rs: sum(0.3 + r.ocr_conf * r.format_score for r in rs))
    chars, agree = [], []
    for i in range(len(g[0].key)):
        w = {}
        for r in g:
            w[r.key[i]] = w.get(r.key[i], 0) + 0.2 + r.ocr_conf
        c = max(w, key=w.get)
        chars.append(c)
        agree.append(w[c] / sum(w.values()))
    key = "".join(chars)
    return key, float(np.mean(agree)), len(g), max(g, key=lambda r: (r.key == key, r.conf))


# ---------------------------------------------------------------------------------- engine
class LPREngine:
    def __init__(self):
        self.status = "idle"      # idle | loading | ready | error
        self.error = ""
        self.detector = None
        self.reader = None
        self.ctc = None
        self.model_name = ""
        self.ocr_model = "base"        # name of the active OCR weights
        self.base_state = None         # pristine recognizer weights (fine-tuning always starts here)
        self.hint_keys = frozenset()   # registered plate keys (set by the recognition service)
        self.list_bias = 1.0           # log-prob bonus for registered plates
        self._lock = threading.Lock()        # detector (ONNX)
        self._ocr_lock = threading.Lock()    # OCR (torch) — separate so tracking never waits on OCR
        self.stats = {"det_ms": 0, "ocr_ms": 0}

    # ------------------------------------------------------------ lifecycle
    def load_async(self, model_name: str):
        threading.Thread(target=self.load, args=(model_name,), daemon=True, name="lpr-load").start()

    def load(self, model_name: str):
        self.status, self.error = "loading", ""
        try:
            from open_image_models import create_detector
            import easyocr
            import onnxruntime as ort
            import torch
            # Detector and OCR run in parallel threads: split the cores instead of letting both
            # libraries grab all of them (oversubscription made OCR ~3x slower).
            cores = max(2, (os.cpu_count() or 4))
            torch.set_num_threads(max(2, cores // 3))
            try:
                torch.set_num_interop_threads(1)
            except RuntimeError:
                pass
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(2, cores // 4)
            so.inter_op_num_threads = 1
            t = time.time()
            det = create_detector(model_name, conf_thresh=0.25, sess_options=so, providers=["CPUExecutionProvider"])
            reader = easyocr.Reader(["th", "en"], gpu=False, verbose=False, quantize=False)
            onnx_path = MODEL_DIR / "thai_rec_float.onnx"
            if not onnx_path.exists():
                log.info("exporting Thai recognizer to ONNX (one time)...")
                try:
                    export_onnx(reader, onnx_path)
                except Exception:
                    log.exception("ONNX export failed — using torch (slower)")
                    onnx_path.unlink(missing_ok=True)
            import copy
            self.base_state = copy.deepcopy(reader.recognizer.state_dict())
            self._threads = max(2, cores // 3)
            with self._lock:
                self.detector, self.reader, self.model_name = det, reader, model_name
                self.ctc = CTCReader(reader, onnx_path if onnx_path.exists() else None, self._threads)
            try:
                from .. import db
                active = int(db.get_settings()["ai"].get("active_model", 0) or 0)
                if active:
                    self.activate_model(db.one("SELECT * FROM ai_models WHERE id=?", (active,)))
            except Exception:
                log.exception("could not load the fine-tuned OCR model — using the base model")
            # warm-up so the first real vehicle is not slowed by lazy torch/onnx initialisation
            dummy = np.full((150, 340, 3), 235, np.uint8)
            cv2.putText(dummy, "1234", (60, 90), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (10, 10, 10), 6)
            det.predict(np.zeros((480, 640, 3), np.uint8))
            for _ in range(2):
                self.read_plate(dummy)
            self.status = "ready"
            log.info("LPR engine ready in %.1fs (%s)", time.time() - t, model_name)
        except Exception as e:  # pragma: no cover - surfaced in UI
            log.exception("engine load failed")
            self.status, self.error = "error", str(e)

    def activate_model(self, row):
        """Switch the OCR to fine-tuned weights (row from ai_models) or back to the base model (None)."""
        import torch
        rec = self.reader.recognizer
        if row:
            state = torch.load(MODEL_DIR / row["weights"], map_location="cpu")
            onnx = MODEL_DIR / row["onnx"]
        else:
            state = {k: v for k, v in self.base_state.items() if k.split(".")[0] in TUNED_PARTS}
            onnx = MODEL_DIR / "thai_rec_float.onnx"
        with self._ocr_lock:
            rec.load_state_dict({**rec.state_dict(), **state})
            self.ctc = CTCReader(self.reader, onnx if onnx.exists() else None, getattr(self, "_threads", 4))
            self.ocr_model = row["name"] if row else "base"
        log.info("OCR model: %s", self.ocr_model)

    # ------------------------------------------------------------ detection
    def detect(self, frame: np.ndarray, min_conf: float):
        t0 = time.time()
        with self._lock:
            res = self.detector.predict(frame)
        self.stats["det_ms"] = int((time.time() - t0) * 1000)
        out = []
        for r in res:
            if r.confidence < min_conf:
                continue
            b = r.bounding_box
            out.append(((b.x1, b.y1, b.x2, b.y2), float(r.confidence)))
        return out

    @staticmethod
    def _crop(frame, bbox, pad_x=0.03, pad_y=0.12):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        bw, bh = x2 - x1, y2 - y1
        x1 = max(0, int(x1 - bw * pad_x)); x2 = min(w, int(x2 + bw * pad_x))
        y1 = max(0, int(y1 - bh * pad_y)); y2 = min(h, int(y2 + bh * pad_y))
        return frame[y1:y2, x1:x2].copy()

    # ------------------------------------------------------------ image preparation
    @staticmethod
    def _prep(crop, target_h=110, clahe=True):
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        scale = target_h / max(1, gray.shape[0])
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
        if not clahe:   # unsharp mask variant — helps motion-blurred / low-contrast plates
            blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
            return cv2.addWeighted(gray, 1.6, blur, -0.6, 0)
        return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 8)).apply(gray)

    @staticmethod
    def _skew(gray):
        """Plate rotation in degrees: the angle that makes text rows sharpest (projection variance)."""
        s = 48 / max(1, gray.shape[0])
        g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        c = (bw.shape[1] / 2, bw.shape[0] / 2)

        def score(a):
            r = cv2.warpAffine(bw, cv2.getRotationMatrix2D(c, a, 1), (bw.shape[1], bw.shape[0]))
            return float(np.var(r.sum(axis=1)))
        # coarse ±36° (hand-held / steep camera angles), then refine around the best angle
        coarse = max(np.arange(-36, 37, 3.0), key=score)
        return float(max(np.arange(coarse - 2.5, coarse + 2.75, 0.5), key=score))

    @staticmethod
    def _rotate(img, a):
        if abs(a) < 0.5:
            return img
        M = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), a, 1)
        return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)

    @staticmethod
    def _lines_cc(gray):
        """Fast line finder: character blobs -> registration line + province line ([x1, x2, y1, y2]).

        Registration characters are the tallest group of similar-height blobs on one row; the plate
        frame / phone edge are excluded because they are far wider than a character.
        """
        H, W = gray.shape[:2]
        block = max(15, (H // 5) | 1)
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 10)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
        comps = [tuple(stats[i][:5]) for i in range(1, n)]
        chars = [c for c in comps if 0.22 * H <= c[3] <= 0.8 * H and 0.015 * W <= c[2] <= 0.3 * W
                 and c[4] >= 0.12 * c[2] * c[3]]
        if len(chars) < 3:
            return None, None
        best = []
        for x, y, w, h, _ in chars:
            cy = y + h / 2
            grp = [c for c in chars if abs((c[1] + c[3] / 2) - cy) < 0.14 * H and abs(c[3] - h) < 0.4 * h]
            if len(grp) > len(best) or (len(grp) == len(best) and sum(g[3] for g in grp) > sum(g[3] for g in best)):
                best = grp
        if len(best) < 3:
            return None, None
        x1 = min(c[0] for c in best); x2 = max(c[0] + c[2] for c in best)
        y1 = min(c[1] for c in best); y2 = max(c[1] + c[3] for c in best)
        span = x2 - x1
        if span < 0.3 * W:
            return None, None
        # a character touching a screw / frame edge becomes one taller blob: it is not in the
        # similar-height group, but it still sits on the row — widen the line to keep it
        rh = y2 - y1
        for x, y, w, h, _ in comps:
            if 0.015 * W <= w <= 0.3 * W and h <= 1.9 * rh and w / max(1, h) >= 0.25 \
                    and min(y + h, y2) - max(y, y1) >= 0.6 * rh \
                    and x + w >= x1 - 0.2 * span and x <= x2 + 0.2 * span:
                x1, x2 = min(x1, x), max(x2, x + w)
        span = x2 - x1
        mx, my = int(0.03 * span), int(0.06 * (y2 - y1))
        reg = [max(0, x1 - mx), min(W, x2 + mx), max(0, y1 - my), min(H, y2 + my)]
        small = [c for c in comps if c[1] >= y2 - 0.05 * H and 0.04 * H <= c[3] <= 0.35 * H and c[2] <= 0.3 * W
                 and c[0] >= x1 - 0.15 * span and c[0] + c[2] <= x2 + 0.15 * span]
        prov = None
        if small:
            py1 = min(c[1] for c in small); py2 = max(c[1] + c[3] for c in small)
            px1 = min(c[0] for c in small); px2 = max(c[0] + c[2] for c in small)
            prov = [max(0, px1 - mx), min(W, px2 + mx), max(0, py1 - 4), min(H, py2 + 4)]
        return reg, prov

    def _text_lines(self, gray):
        """CRAFT text boxes -> registration line box and province line box ([x1, x2, y1, y2])."""
        try:
            hl, _ = self.reader.detect(gray, min_size=6, text_threshold=0.6, low_text=0.35,
                                       link_threshold=0.3, width_ths=1.2, add_margin=0.06)
            boxes = [b for b in hl[0] if (b[1] - b[0]) > 4 and (b[3] - b[2]) > 4]
        except Exception:
            return None, None
        if not boxes:
            return None, None
        tall = max(boxes, key=lambda b: b[3] - b[2])
        ty1, ty2 = tall[2], tall[3]
        # every box whose vertical centre lies inside the tallest box belongs to the registration line
        reg_boxes = [b for b in boxes if ty1 - 2 <= (b[2] + b[3]) / 2 <= ty2 + 2]
        reg = [min(b[0] for b in reg_boxes), max(b[1] for b in reg_boxes),
               min(b[2] for b in reg_boxes), max(b[3] for b in reg_boxes)]
        below = [b for b in boxes if b not in reg_boxes and (b[2] + b[3]) / 2 > reg[3] - 2]
        prov = None
        if below:
            prov = [min(b[0] for b in below), max(b[1] for b in below),
                    min(b[2] for b in below), max(b[3] for b in below)]
        H, W = gray.shape[:2]
        clip = lambda b: [max(0, int(b[0])), min(W, int(b[1])), max(0, int(b[2])), min(H, int(b[3]))]
        return clip(reg), (clip(prov) if prov else None)

    def _views(self, crop, split=0.64):
        """Deskewed line images. Registration: 3 views, province: 2 views.

        Chosen by measurement on real plates (ห/ท confusion): high-res raw + the same padded +
        CLAHE padded read 6/7 frames correctly versus 1-2/7 for the old single CLAHE view.
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        rg = self._rotate(gray, self._skew(gray))
        hi = cv2.resize(rg, (max(8, int(rg.shape[1] * 160 / max(1, rg.shape[0]))), 160), interpolation=cv2.INTER_CUBIC)
        base = self._prep(rg)                                   # CLAHE, h=110
        H, W = hi.shape[:2]
        reg, prov = self._lines_cc(hi)
        if not reg:                                             # unusual plate: ask the CRAFT detector
            reg, prov = self._text_lines(hi)
        if not reg:
            reg = [0, W, 0, int(H * split)]
            prov = [0, W, int(H * split) - 6, H]
        if not prov and reg[3] < H - 14:
            prov = [0, W, reg[3], H]
        k = base.shape[0] / H

        def cut(img, box, s=1.0):
            x1, x2, y1, y2 = box
            ih, iw = img.shape[:2]
            return img[max(0, int(y1 * s)):min(ih, int(y2 * s)), max(0, int(x1 * s)):min(iw, int(x2 * s))]

        pad = lambda img, y, x: cv2.copyMakeBorder(img, y, y, x, x, cv2.BORDER_REPLICATE) if img.size else img
        ok = lambda vs: [v for v in vs if v.size and v.shape[0] >= 8 and v.shape[1] >= 8]
        # measured on real plates: hi-res padded + CLAHE padded = 6/7 frames correct (old view: 1-2/7)
        regs = ok([pad(cut(hi, reg), 8, 16), pad(cut(base, reg, k), 6, 12)])
        provs = ok([pad(cut(hi, prov), 6, 12), pad(cut(base, prov, k), 6, 12)]) if prov else []
        return regs, provs

    # ------------------------------------------------------------ OCR
    def _recognize(self, gray, allowlist, box=None):
        hl = [box] if box else None
        res = self.reader.recognize(gray, horizontal_list=hl, free_list=[] if box else None,
                                    allowlist=allowlist, detail=1, paragraph=False)
        if not res:
            return "", 0.0
        res = sorted(res, key=lambda r: r[0][0][0])
        return "".join(r[1] for r in res), float(np.mean([r[2] for r in res]))

    def read_plate(self, crop: np.ndarray, plate_format="TH", split=0.64, with_province=True):
        if plate_format != "TH":
            text, conf = self._recognize(self._prep(crop), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            key = thai.plate_key(text)
            return {"text": text, "key": key, "raw": text, "province": "", "ocr_conf": conf,
                    "format_score": 1.0 if len(key) >= 4 else 0.3}
        reg_imgs, prov_imgs = self._views(crop, split)
        if not reg_imgs:
            return {"text": "", "key": "", "raw": "", "province": "", "ocr_conf": 0.0, "format_score": 0.0}
        prov_imgs = prov_imgs if with_province else []      # province is read once per vehicle, at report time
        # pass 1: first registration view (+ province views) in one network call
        first = self.ctc.logprobs_batch(reg_imgs[:1] + prov_imgs, [thai.REG_ALLOWLIST] + [thai.THAI_ALL] * len(prov_imgs))
        mats, pmats = first[:1], first[1:]

        def decide(ms):
            cs = set()
            for lp, chars in ms:
                cs.update(t for t, _ in beam(lp, chars))
            cs |= _near_hints(cs, self.hint_keys)
            sc = rank([ms], cs, self.hint_keys, self.list_bias)
            return sc, posterior(sc)
        scores, (key, post) = decide(mats)
        # pass 2: only when the first view is not convincing
        if len(reg_imgs) > 1 and post < 0.9:
            mats = mats + self.ctc.logprobs_batch(reg_imgs[1:], [thai.REG_ALLOWLIST] * (len(reg_imgs) - 1))
            scores, (key, post) = decide(mats)
        if not key:
            return {"text": "", "key": "", "raw": "", "province": "", "ocr_conf": 0.0, "format_score": 0.0}
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:6]
        disp, _, fmt = thai.parse_registration(key)
        province = rank_province([pmats])[0] if with_province else ""
        raw = " ".join(f"{c}:{v:.1f}" for c, v in top[:3])
        return {"text": disp, "key": key, "raw": raw, "province": province, "ocr_conf": post,
                "format_score": fmt, "mats": mats, "prov_mats": pmats, "cands": [c for c, _ in top]}

    def read_province(self, crop: np.ndarray, split=0.64) -> str:
        """Province line only — used when a read was made without it."""
        if self.status != "ready" or crop is None:
            return ""
        with self._ocr_lock:
            _, prov_imgs = self._views(crop, split)
            return rank_province([self.ctc.logprobs_batch(prov_imgs, [thai.THAI_ALL] * len(prov_imgs))])[0]

    def read_province_multi(self, crops, split=0.64) -> str:
        """Province from several crops of the same vehicle, scored together (one network call)."""
        crops = [c for c in crops if c is not None and c.size]
        if self.status != "ready" or not crops:
            return ""
        with self._ocr_lock:
            per = [self._views(c, split)[1] for c in crops]
            flat = [v for vs in per for v in vs]
            mats = self.ctc.logprobs_batch(flat, [thai.THAI_ALL] * len(flat))
            out, i = [], 0
            for vs in per:
                out.append(mats[i:i + len(vs)])
                i += len(vs)
            return rank_province(out)[0]

    def read_crop(self, crop, bbox, det_conf, cfg: dict, with_province=False):
        """OCR one detected plate crop -> PlateRead (or None)."""
        if self.status != "ready" or crop is None or crop.size == 0:
            return None
        t1 = time.time()
        with self._ocr_lock:
            r = self.read_plate(crop, cfg.get("plate_format", "TH"), cfg.get("split_ratio", 0.64), with_province)
        if not r.get("key"):
            return None
        pr = PlateRead(bbox=tuple(bbox), det_conf=det_conf, crop=crop, **r)
        pr.conf = round(min(1.0, 0.25 * det_conf + 0.45 * pr.ocr_conf + 0.30 * pr.format_score), 3)
        pr.ms = int((time.time() - t1) * 1000)
        self.stats["ocr_ms"] = pr.ms
        return pr

    def recognize(self, frame: np.ndarray, cfg: dict, with_province=False, offset=(0, 0)):
        """Detect + read every plate in `frame` (single shot, used by tests / manual capture)."""
        if self.status != "ready":
            return []
        reads = []
        ox, oy = offset
        for bbox, dconf in self.detect(frame, cfg.get("min_det_conf", 0.4))[:4]:
            if bbox[2] - bbox[0] < cfg.get("min_plate_width", 60):
                continue
            pr = self.read_crop(self._crop(frame, bbox), (bbox[0] + ox, bbox[1] + oy, bbox[2] + ox, bbox[3] + oy),
                                dconf, cfg, with_province)
            if pr:
                reads.append(pr)
        return sorted(reads, key=lambda r: r.conf, reverse=True)
