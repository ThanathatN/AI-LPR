"""Site fine-tuning of the Thai plate OCR (runs on CPU, in the background).

Why it works with little data
  * the CNN (visual features) is kept frozen — it already "sees" Thai characters well; only the
    sequence model (BiLSTM) and the classifier that turn those features into letters are tuned.
    That is where systematic site errors such as ห→ท live (font, camera angle, IR lighting).
  * CNN features are computed once per (augmented) image, so each epoch is fast.
  * knowledge distillation from the original model keeps every character it was not shown
    (a site with no ฎ plates must not forget ฎ).
  * a held-out validation set measures plate accuracy with the real decoder; the new model is
    only switched on when it beats the base model.
"""
import copy
import logging
import random
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from .. import db
from ..bus import bus
from ..config import DATASET_DIR, MODEL_DIR
from . import thai
from .ctc import beam, export_model, posterior
from .engine import TUNED_PARTS, rank

log = logging.getLogger("lpr.trainer")
MIN_SAMPLES = 20
FT_DIR = MODEL_DIR / "ft"
FT_DIR.mkdir(parents=True, exist_ok=True)


def augment(g: np.ndarray, rng: random.Random) -> np.ndarray:
    """Camera-like degradations of one text-line image."""
    h, w = g.shape[:2]
    a = rng.uniform(-2.5, 2.5)
    s = rng.uniform(0.92, 1.06)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), a, s)
    M[0, 2] += rng.uniform(-0.03, 0.03) * w
    M[1, 2] += rng.uniform(-0.05, 0.05) * h
    g = cv2.warpAffine(g, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if rng.random() < 0.6:                                    # low resolution (far plate)
        f = rng.uniform(0.35, 0.8)
        g = cv2.resize(cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), (w, h), interpolation=cv2.INTER_LINEAR)
    r = rng.random()
    if r < 0.3:                                               # horizontal motion blur
        k = rng.choice([3, 5, 7])
        ker = np.zeros((k, k), np.float32)
        ker[k // 2, :] = 1.0 / k
        g = cv2.filter2D(g, -1, ker)
    elif r < 0.55:
        g = cv2.GaussianBlur(g, (0, 0), rng.uniform(0.5, 1.3))
    g = cv2.convertScaleAbs(g, alpha=rng.uniform(0.6, 1.3), beta=rng.uniform(-35, 35))
    if rng.random() < 0.5:
        g = np.clip(g + np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, rng.uniform(2, 9), g.shape), 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        ok, buf = cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(30, 85)])
        g = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE) if ok else g
    return g


class Trainer:
    def __init__(self, engine):
        self.engine = engine
        self.state = {"state": "idle", "progress": 0.0, "message": "", "log": [], "result": None, "started": None}
        self._cancel = False
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def status(self):
        return copy.deepcopy(self.state)

    def _set(self, **kw):
        self.state.update(kw)
        bus.publish("train", self.status())

    def _log(self, msg):
        log.info(msg)
        self.state["log"] = (self.state["log"] + [f"{datetime.now().strftime('%H:%M:%S')} {msg}"])[-60:]
        self._set(message=msg)

    def start(self, opts: dict):
        if self.running:
            raise RuntimeError("กำลังเทรนอยู่แล้ว")
        if self.engine.status != "ready":
            raise RuntimeError("AI Engine ยังไม่พร้อม")
        self._cancel = False
        self.state = {"state": "preparing", "progress": 0.0, "message": "", "log": [], "result": None,
                      "started": db.now_iso(), "opts": opts}
        self._thread = threading.Thread(target=self._safe_run, args=(opts,), daemon=True, name="trainer")
        self._thread.start()

    def cancel(self):
        self._cancel = True

    def _safe_run(self, opts):
        try:
            self._run(opts)
        except _Cancelled:
            self._log("ยกเลิกการเทรนแล้ว")
            self._set(state="cancelled")
        except Exception as e:
            log.exception("training failed")
            self._log(f"ผิดพลาด: {e}")
            self._set(state="error")

    def _check(self):
        if self._cancel:
            raise _Cancelled()

    # ------------------------------------------------------------------ main
    def _run(self, opts):
        import torch
        import torch.nn.functional as F
        eng = self.engine
        ctc = eng.ctc
        epochs = int(opts.get("epochs", 8))
        n_aug = int(opts.get("augment", 3))
        lr = float(opts.get("lr", 2e-4))
        statuses = ["verified"] + (["auto"] if opts.get("include_auto", True) else [])
        rows = db.query(f"SELECT * FROM ai_samples WHERE status IN ({','.join('?' * len(statuses))}) "
                        f"ORDER BY id DESC LIMIT 4000", statuses)
        self._log(f"พบตัวอย่าง {len(rows)} ภาพ ({' + '.join(statuses)})")

        # 1. line images -------------------------------------------------------------
        items = []
        for i, r in enumerate(rows):
            self._check()
            label = r["label"]
            if not label or any(c not in ctc.index for c in label):
                continue
            img = cv2.imread(str(DATASET_DIR / r["image"]))
            if img is None:
                continue
            with eng._ocr_lock:            # _views may use the shared CRAFT detector
                regs, _ = eng._views(img)
            if regs:
                items.append((r, regs))
            if i % 20 == 0:
                self._set(progress=0.1 * i / max(1, len(rows)))
        if len(items) < MIN_SAMPLES:
            raise RuntimeError(f"ตัวอย่างที่ใช้ได้มี {len(items)} ภาพ ต้องมีอย่างน้อย {MIN_SAMPLES} ภาพ")
        items.sort(key=lambda it: it[0]["id"])
        val = [it for k, it in enumerate(items) if k % 5 == 0]
        train = [it for k, it in enumerate(items) if k % 5 != 0]
        self._log(f"แบ่งข้อมูล: เทรน {len(train)} · ทดสอบ {len(val)} ป้าย")

        # 2. model copies ------------------------------------------------------------
        model = copy.deepcopy(eng.reader.recognizer).eval()
        model.load_state_dict(eng.base_state)
        cnn = model.FeatureExtraction
        seq, pred = model.SequenceModeling, model.Prediction
        t_seq, t_pred = copy.deepcopy(seq).eval(), copy.deepcopy(pred).eval()      # teacher = base model
        for p in list(cnn.parameters()) + list(t_seq.parameters()) + list(t_pred.parameters()):
            p.requires_grad_(False)

        def feat(gray):
            x = torch.from_numpy(ctc.prepare(gray))[None, None]
            with torch.no_grad():
                f = cnn(x).permute(0, 3, 1, 2).mean(dim=3)          # (1, T, C) == AdaptiveAvgPool((None, 1))
            return f

        def evaluate(sq, pr, feats):
            correct = 0
            sq.eval()
            with torch.no_grad():
                for label, fs in feats:
                    mats = [ctc.restrict(F.softmax(pr(sq(f.float())), dim=2)[0].numpy(), thai.REG_ALLOWLIST) for f in fs]
                    cands = set()
                    for lp, chars in mats:
                        cands.update(t for t, _ in beam(lp, chars))
                    key = posterior(rank([mats], cands))[0]
                    correct += key == label
            return correct / max(1, len(feats))

        # 3. features (CNN runs once per image variant) ------------------------------
        rng = random.Random(1234)
        val_feats = [(r["label"], [feat(g) for g in regs]) for r, regs in val]
        train_feats = []
        for i, (r, regs) in enumerate(train):
            self._check()
            tgt = torch.tensor([ctc.index[c] for c in r["label"]], dtype=torch.long)
            for g in regs:
                for v in [g] + [augment(g, rng) for _ in range(n_aug)]:
                    train_feats.append((feat(v).half(), tgt))
            if i % 10 == 0:
                self._set(progress=0.1 + 0.35 * i / max(1, len(train)))
        self._log(f"เตรียมภาพเทรน {len(train_feats)} ภาพ (augment ×{n_aug + 1} ต่อภาพ)")

        base_acc = evaluate(t_seq, t_pred, val_feats)
        self._log(f"ความแม่นยำโมเดลเดิมบนชุดทดสอบ: {base_acc * 100:.1f}%")

        # 4. fine-tune ---------------------------------------------------------------
        params = list(seq.parameters()) + list(pred.parameters())
        opt = torch.optim.Adam(params, lr=lr)
        best_acc, best_state, best_ep = base_acc, None, 0
        accum, t0 = 8, time.time()
        total_steps = epochs * len(train_feats)
        step = 0
        for ep in range(1, epochs + 1):
            seq.train()
            rng.shuffle(train_feats)
            run_loss = 0.0
            opt.zero_grad()
            for k, (f, tgt) in enumerate(train_feats):
                if k % 50 == 0:
                    self._check()
                f = f.float()
                logits = pred(seq(f))                                  # (1, T, C)
                lp = F.log_softmax(logits, dim=2)
                T = lp.shape[1]
                loss = F.ctc_loss(lp.permute(1, 0, 2), tgt[None], torch.tensor([T]), torch.tensor([len(tgt)]),
                                  blank=0, zero_infinity=True)
                with torch.no_grad():
                    teacher = F.softmax(t_pred(t_seq(f)), dim=2)
                loss = loss + 0.3 * F.kl_div(lp, teacher, reduction="batchmean") / T
                (loss / accum).backward()
                run_loss += float(loss)
                if (k + 1) % accum == 0:
                    torch.nn.utils.clip_grad_norm_(params, 5.0)
                    opt.step()
                    opt.zero_grad()
                step += 1
                if k % 40 == 0:
                    el = time.time() - t0
                    eta = el / max(1, step) * (total_steps - step)
                    self._set(state="training", progress=0.45 + 0.5 * step / max(1, total_steps),
                              message=f"รอบที่ {ep}/{epochs} · เหลือประมาณ {int(eta // 60)} นาที {int(eta % 60)} วินาที")
            opt.step()
            acc = evaluate(seq, pred, val_feats)
            self._log(f"รอบที่ {ep}/{epochs}: loss {run_loss / max(1, len(train_feats)):.3f} · ความแม่นยำ {acc * 100:.1f}%")
            if acc > best_acc or (best_state is None and acc >= best_acc):
                best_acc, best_ep = acc, ep
                best_state = {f"{n}.{k}": v.detach().clone() for n, m in (("SequenceModeling", seq), ("Prediction", pred))
                              for k, v in m.state_dict().items()}

        # 5. save + (optionally) activate ---------------------------------------------
        if best_state is None:
            self._log("โมเดลใหม่ไม่ดีกว่าเดิม — ไม่บันทึก")
            self._set(state="done", progress=1.0, result={"base_acc": base_acc, "acc": base_acc, "saved": False})
            return
        self._set(state="saving", progress=0.96, message="กำลังบันทึกและแปลงโมเดลเป็น ONNX...")
        name = f"site-{datetime.now().strftime('%Y%m%d-%H%M')}"
        torch.save(best_state, FT_DIR / f"{name}.pth")
        model.load_state_dict({**model.state_dict(), **best_state})
        export_model(model, FT_DIR / f"{name}.onnx")
        mid = db.insert("ai_models", {"created_at": db.now_iso(), "name": name, "onnx": f"ft/{name}.onnx",
                                      "weights": f"ft/{name}.pth", "samples": len(train), "val_samples": len(val),
                                      "base_acc": round(base_acc, 4), "acc": round(best_acc, 4), "epochs": best_ep,
                                      "note": f"{'+'.join(statuses)} · aug×{n_aug + 1} · lr {lr}"})
        improved = best_acc > base_acc
        activated = False
        if improved and opts.get("activate", True):
            eng.activate_model(db.one("SELECT * FROM ai_models WHERE id=?", (mid,)))
            db.save_settings("ai", {"active_model": mid})
            activated = True
        self._log(f"บันทึกโมเดล {name}: {base_acc * 100:.1f}% → {best_acc * 100:.1f}%"
                  + (" · เปิดใช้งานแล้ว" if activated else ""))
        self._set(state="done", progress=1.0, result={"base_acc": base_acc, "acc": best_acc, "saved": True,
                                                       "model_id": mid, "activated": activated, "name": name})


class _Cancelled(Exception):
    pass
