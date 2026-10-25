"""Direct access to the OCR network's per-timestep character probabilities (CTC).

EasyOCR only returns its single greedy string. For licence plates that throws away exactly
the information we need: when ห and ท are close, the network still says *how* close.
With the full probability matrix we can
  * generate several candidate strings (prefix beam search),
  * score ANY candidate exactly — batched through torch's CTC loss,
  * add knowledge the network lacks: Thai plate grammar, the 77 province names and the
    registered vehicle list, and sum evidence over several frames of the same vehicle.
"""
import math

import cv2
import numpy as np
import torch
import torch.nn.functional as F

NEG = -1e9


class _MeanPoolLast(torch.nn.Module):
    """== AdaptiveAvgPool2d((None, 1)) but exportable with a dynamic image width."""
    def forward(self, x):
        return x.mean(dim=3, keepdim=True)


class _Softmaxed(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, x):
        return torch.softmax(self.m(x, None), dim=2)


def export_onnx(reader, path):
    """Export the (float) EasyOCR recognizer to ONNX once. ~2x faster than torch on CPU, identical output."""
    export_model(reader.recognizer, path)


def export_model(m, path):
    m = m.eval()
    m.AdaptiveAvgPool = _MeanPoolLast()
    torch.onnx.export(_Softmaxed(m).eval(), torch.randn(1, 1, 64, 256), str(path), input_names=["image"],
                      output_names=["prob"], dynamic_axes={"image": {0: "batch", 3: "width"}, "prob": {0: "batch", 1: "time"}},
                      opset_version=17, dynamo=False)


class CTCReader:
    def __init__(self, reader, onnx_path=None, threads=4):
        self.model = reader.recognizer
        self.chars = reader.converter.character          # index 0 = blank
        self.index = {c: i for i, c in enumerate(self.chars)}
        self.imgH = 64
        self.device = next(self.model.parameters()).device
        self.session = None
        if onnx_path:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])

    def _forward(self, batch: np.ndarray) -> np.ndarray:
        x = (batch.astype(np.float32) / 255.0 - 0.5) / 0.5
        if self.session is not None:
            return self.session.run(None, {"image": x[:, None]})[0]
        with torch.no_grad():
            return F.softmax(self.model(torch.from_numpy(x)[:, None].to(self.device), None), dim=2).cpu().numpy()

    def logprobs(self, gray: np.ndarray, allowlist: str):
        """gray: one text line (uint8). Returns ((T, K) log-probs over [blank]+allowlist, chars)."""
        return self.logprobs_batch([gray], [allowlist])[0]

    def logprobs_batch(self, grays: list, allowlists: list):
        """Several text lines in ONE network pass (images padded to a common width, like EasyOCR)."""
        if not grays:
            return []
        imgs = []
        for g in grays:
            h, w = g.shape[:2]
            nw = max(16, int(math.ceil(self.imgH * w / max(1, h))))
            imgs.append(cv2.resize(g, (nw, self.imgH), interpolation=cv2.INTER_CUBIC))
        W = max(i.shape[1] for i in imgs)
        widths = [i.shape[1] for i in imgs]
        batch = np.stack([np.pad(i, ((0, 0), (0, W - i.shape[1])), mode="edge") for i in imgs])
        prob = self._forward(batch)                                          # (B, T, C)
        T = prob.shape[1]
        out = []
        for b, allow in enumerate(allowlists):
            t_used = max(4, int(math.ceil(T * widths[b] / W)) + 1)          # drop timesteps of pure padding
            out.append(self.restrict(prob[b, :t_used], allow))
        return out

    def restrict(self, prob: np.ndarray, allow: str):
        """(T, C) probabilities over the full alphabet -> log-probs over [blank] + allowlist."""
        keep = [0] + [self.index[c] for c in dict.fromkeys(allow) if c in self.index]
        sub = prob[:, keep]
        sub = sub / np.clip(sub.sum(axis=1, keepdims=True), 1e-9, None)
        return np.log(np.clip(sub, 1e-12, None)).astype(np.float32), [""] + [self.chars[i] for i in keep[1:]]

    def prepare(self, gray: np.ndarray) -> np.ndarray:
        """Exactly the network input for one line image: (64, W) float32, normalised to [-1, 1]."""
        h, w = gray.shape[:2]
        nw = max(16, int(math.ceil(self.imgH * w / max(1, h))))
        img = cv2.resize(gray, (nw, self.imgH), interpolation=cv2.INTER_CUBIC)
        return (img.astype(np.float32) / 255.0 - 0.5) / 0.5


def score_many(lp: np.ndarray, chars: list, texts) -> dict:
    """log P(text | image) for many candidate texts at once (CTC forward algorithm via torch)."""
    pos = {c: i for i, c in enumerate(chars)}
    texts = [t for t in dict.fromkeys(texts) if t]
    ok = [t for t in texts if all(c in pos for c in t)]
    out = {t: NEG for t in texts}
    if not ok:
        return out
    T, K = lp.shape
    N = len(ok)
    log_probs = torch.from_numpy(lp)[:, None, :].expand(T, N, K)
    targets = torch.tensor([pos[c] for t in ok for c in t], dtype=torch.long)
    loss = F.ctc_loss(log_probs, targets, torch.full((N,), T, dtype=torch.long),
                      torch.tensor([len(t) for t in ok], dtype=torch.long),
                      blank=0, reduction="none", zero_infinity=False)
    for t, l in zip(ok, loss.tolist()):
        out[t] = -l if math.isfinite(l) else NEG
    return out


def beam(lp: np.ndarray, chars: list, width=10, top=8, max_len=9):
    """CTC prefix beam search -> [(text, logprob)] best first."""
    T, K = lp.shape
    beams = {(): (0.0, NEG)}            # prefix -> (log p ending in blank, log p ending in a char)
    cand_k = min(K - 1, 5)
    for t in range(T):
        row = lp[t]
        if row[0] > -0.02:              # near-certain blank frame: prefixes only extend by blank
            beams = {p: (np.logaddexp(pb, pnb) + row[0], NEG) for p, (pb, pnb) in beams.items()}
            continue
        top_k = np.argpartition(row[1:], -cand_k)[-cand_k:] + 1
        nb = {}
        for prefix, (pb, pnb) in beams.items():
            tot = np.logaddexp(pb, pnb)
            b, n = nb.get(prefix, (NEG, NEG))
            n2 = np.logaddexp(n, pnb + row[prefix[-1]]) if prefix else n
            nb[prefix] = (np.logaddexp(b, tot + row[0]), n2)
            if len(prefix) >= max_len:
                continue
            for k in top_k:
                k = int(k)
                np_ = prefix + (k,)
                b, n = nb.get(np_, (NEG, NEG))
                add = pb + row[k] if prefix and prefix[-1] == k else tot + row[k]
                nb[np_] = (b, np.logaddexp(n, add))
        beams = dict(sorted(nb.items(), key=lambda kv: -np.logaddexp(*kv[1]))[:width])
    out = [("".join(chars[i] for i in p), float(np.logaddexp(*v))) for p, v in beams.items() if p]
    return sorted(out, key=lambda x: -x[1])[:top]


def posterior(scores: dict):
    """Softmax over candidate log-scores -> (best, probability of best)."""
    if not scores:
        return None, 0.0
    best = max(scores, key=scores.get)
    m = scores[best]
    z = sum(math.exp(v - m) for v in scores.values() if v > NEG / 2)
    return best, 1.0 / z if z else 0.0
