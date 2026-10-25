"""Paths and default settings."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LPR_DATA_DIR", BASE_DIR / "data"))
SNAP_DIR = DATA_DIR / "snapshots"
PLATE_DIR = DATA_DIR / "plates"
DATASET_DIR = DATA_DIR / "dataset"          # verified plate crops for AI training (not deleted by retention)
MODEL_DIR = DATA_DIR / "models"
DB_PATH = DATA_DIR / "lpr.db"
WEB_DIR = BASE_DIR / "web"

for d in (DATA_DIR, SNAP_DIR, PLATE_DIR, DATASET_DIR, MODEL_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Per-camera ("lane") settings: every camera has its own copy in cameras.config
LANE_DEFAULTS = {
    "recognition": {
        "enabled": True,
        "trigger_mode": "line",     # line | video | input (loop arms) | mixed (line OR loop)
        "trigger_line": {"x1": 0.15, "y1": 0.62, "x2": 0.85, "y2": 0.62, "direction": "any"},
        # detection area polygon (normalised). Only plates whose centre is inside are tracked.
        "area": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        "motion": "any",            # video/input modes: any | approach (towards camera) | leave
        "post_cross_wait": 0.8,     # seconds to collect more OCR votes after the line is crossed
        "arm_seconds": 6,           # how long recognition stays armed after a loop-detector input
        "process_fps": 8,           # detection / tracking rate
        "min_plate_width": 70,      # px in the source frame — smaller plates are tracked, not read
        "max_plate_width": 900,     # px — bigger detections are ignored (false detections, too close)
    },
    "io": {
        "barrier_output": 1,        # 0 = none
        "barrier_camera": 0,        # 0 = this camera's relay, otherwise the id of the camera owning the relay
        "alarm_output": 0,
        "pulse_ms": 1000,
        "inputs": {},               # {"1": {"role": "trigger|safety|none", "target": 0}}
    },
}

# Every key here is editable from the Settings page; values in the DB override these.
DEFAULT_SETTINGS = {
    "camera": {                     # legacy single-camera settings — migrated into the cameras table
        "name": "Gate 1 - Entrance",
        "host": "192.168.1.123",
        "http_port": 80,
        "rtsp_port": 554,
        "username": "admin",
        "password": "abc2026!",
        "stream": "101",
        "source_override": "",
    },
    "recognition": {                # global (all cameras)
        "plate_format": "TH",       # TH = Thai plate rules, ANY = generic alphanumeric
        "detector_model": "yolo-v9-t-640-license-plate-end2end",
        "min_det_conf": 0.40,
        "min_conf": 0.55,           # final confidence needed to emit an event
        "min_votes": 2,             # agreeing OCR reads before emitting
        "dedupe_seconds": 25,       # same plate is not re-logged within this window (per camera)
        "split_ratio": 0.64,
        "list_bias": 1.0,           # OCR bonus (log-prob) for plates registered in the system
        "overlay": True,            # burn camera / time / plate / direction into evidence snapshots
    },
    "access": {
        "unknown_action": "deny",   # deny | open
        "fuzzy_match": False,
        "blocklist_alarm": True,
        "auto_open": True,
    },
    "io": {
        "poll_ms": 300,
    },
    "ai": {
        "active_model": 0,          # 0 = base model, otherwise ai_models.id
        "auto_samples": True,       # high-confidence reads of registered plates become training samples
    },
    "system": {
        "site_name": "AI-LPR Command Center",
        "retention_days": 90,
        "live_width": 1280,
        "live_quality": 72,
    },
}
