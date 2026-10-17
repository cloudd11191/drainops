"""Drain photo -> tier, health 0-100, debris mix, confidence.
Uses models/drain_model.onnx if present (produced by training/train.py).
Otherwise falls back to a crude heuristic, flagged as demo mode. NOT a real classifier."""
import io, os
import numpy as np
from PIL import Image, ImageFilter

MODEL_PATH = os.getenv("MODEL_PATH", "models/drain_model.onnx")
TIERS = ["Clear", "Moderate", "Critical"]
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_session = None
if os.path.exists(MODEL_PATH):
    import onnxruntime as ort
    _session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])

def _softmax(x):
    e = np.exp(x - x.max()); return e / e.sum()

def quality_check(img: Image.Image) -> list:
    issues = []
    if np.asarray(img.convert("L"), dtype=np.float32).mean() < 40:
        issues.append("too_dark")
    edges = np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    if edges.var() < 30:
        issues.append("blurry_or_featureless")
    return issues

def predict(data: bytes) -> dict:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    issues = quality_check(img)
    x = np.asarray(img.resize((224, 224)), dtype=np.float32) / 255.0
    if _session:
        x = ((x - _MEAN) / _STD).transpose(2, 0, 1)[None].astype(np.float32)
        tier_logits, health, debris = _session.run(None, {"image": x})
        p = _softmax(tier_logits[0]); i = int(p.argmax())
        d = 1 / (1 + np.exp(-debris[0]))
        mode, conf, h = "onnx", float(p[i]), float(np.clip(health[0][0], 0, 100))
    else:  # DEMO ONLY
        g = x.mean(axis=2); texture = float(np.abs(np.diff(g, axis=0)).mean())
        h = float(np.clip(100 - 60 * (1 - g.mean()) - 400 * texture, 0, 100))
        i = 0 if h >= 66 else 1 if h >= 33 else 2
        d = np.array([texture * 5, 1 - g.mean(), 0.3]).clip(0, 1)
        mode, conf = "demo_heuristic", 0.5
    tot = float(d.sum()) or 1.0
    return {"tier": TIERS[i], "health": round(h, 1), "confidence": round(conf, 3),
            "debris": {"plastic": round(float(d[0]) / tot, 2), "silt": round(float(d[1]) / tot, 2),
                       "organic": round(float(d[2]) / tot, 2)},
            "model_mode": mode, "quality_issues": issues,
            "needs_review": conf < 0.6 or bool(issues)}
