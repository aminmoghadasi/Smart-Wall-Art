import json, math, threading, time, os, sqlite3, random
from contextlib import closing
from typing import Dict, List, Tuple, Optional

import pygame
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify

# ================= USER CONFIG =================
MQTT_HOST = "host-ip"
MQTT_PORT = 1883

TOPIC_DATA = "smartart/sensordata"
TOPIC_MODE = "smartart/cmd/mode"

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080
HTTP_DEBUG_LOG = False

# ---- Feedback DB logging ----
DB_PATH = r"DB Address"
FEEDBACK_WINDOW = 20             # number of most-recent ratings to average
FEEDBACK_PRINT_EVERY = 2.0       # seconds between logs (0 = every frame)

# ---- Engagement policy ----
THRESH = 3.0                     # <== change trigger
EPS_EXPLORE = 0.50               # when avg < THRESH
EPS_EXPLOIT = 0.10               # when avg >= THRESH
EPS_NEUTRAL = 0.20               # when no ratings yet
PALETTE_CHECK_EVERY = 2.0        # how often to consider palette change (seconds)
JITTER = 12                      # small random color jitter (+/-)

# Palettes (bg is blended with ambient light later; wave/circle used directly)
PALETTES = [
    {"bg": (18,18,40),   "wave": (30,200,150), "circle": (140,90,255)},
    {"bg": (10,28,25),   "wave": (220,120,60), "circle": (30,200,240)},
    {"bg": (24,10,18),   "wave": (255,80,140), "circle": (255,210,90)},
    {"bg": (8,16,32),    "wave": (120,220,180),"circle": (90,150,255)},
    {"bg": (28,12,40),   "wave": (90,220,255), "circle": (255,120,100)},
]
# ===============================================

# ---- Shared state ----
data_lock = threading.Lock()
data: Dict[str, float] = {"temp": 25.0, "hum": 50.0, "light": 2000, "motion": 0}
update_source = "mqtt"  # authoritative source for applying updates
last_motion_flash = 0
FLASH_MS = 350

def set_update_source(src: str):   # switch between data sources (mqtt or http). used in mqtt message handeler
    global update_source
    s = str(src).strip().lower()
    if s in ("mqtt", "http"):
        update_source = s
        print(f"[MODE] Source -> {update_source.upper()}")
    else:
        print(f"[MODE] Ignored invalid mode '{src}'")

def get_update_source() -> str:
    return update_source

# ================= MQTT =================
def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected rc={rc}; subscribing")
    client.subscribe([(TOPIC_DATA, 0), (TOPIC_MODE, 0)])

def _apply_payload(payload: dict, origin: str):   # it takes any incoming telemetry (from MQTT or HTTP), 
    global last_motion_flash                        # filters it, updates the shared sensor state, and—if motion==1—arms the flash timer.
    if get_update_source() != origin:
        if HTTP_DEBUG_LOG:
            print(f"[{origin.upper()}] Ignored payload (active={get_update_source().upper()})")
        return
    accepted = {k: payload[k] for k in ("temp", "hum", "light", "motion") if k in payload}  # Builds a dict with only recognized keys (temp, hum, light, motion).
    if not accepted:
        return
    with data_lock:
        data.update(accepted)
        try:
            if int(float(data.get("motion", 0))) == 1:
                last_motion_flash = pygame.time.get_ticks()
        except Exception:
            pass

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload_str = msg.payload.decode("utf-8").strip()
        if topic == TOPIC_MODE:
            set_update_source(payload_str)
            return
        if topic == TOPIC_DATA:
            payload = json.loads(payload_str)
            if not isinstance(payload, dict):
                raise ValueError("Telemetry must be a JSON object")
            _apply_payload(payload, origin="mqtt")
    except Exception as e:
        print("[MQTT] Error:", e)

m = mqtt.Client()
m.on_connect = on_connect
m.on_message = on_message
m.reconnect_delay_set(min_delay=1, max_delay=5)

def start_mqtt():
    try:
        print(f"[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT} ...")
        m.connect(MQTT_HOST, MQTT_PORT, 30)
        m.loop_start()
    except Exception as e:
        print("[MQTT] Connection error:", e)

# ================= HTTP SERVER =================
app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify({"ok": True, "mode": get_update_source()})

@app.post("/update")
@app.post("/ingest")
def ingest():
    try:
        payload = request.get_json(force=True, silent=False)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "err": "JSON object required"}), 400
        _apply_payload(payload, origin="http")
        return jsonify({"ok": True, "mode": get_update_source(),
                        "applied": get_update_source() == "http"})
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 400

def run_http_server():
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, threaded=True, use_reloader=False)

# ================= FEEDBACK / AVERAGE LOGGING =================
def read_recent_feedback(db_path: str, window: int) -> Tuple[List[float], Optional[float], Optional[str]]:
    """
    Returns (ratings_list_newest_first, avg_or_None, error_msg_or_None).
    Tries created_at ordering first; falls back to rowid if created_at is missing.
    """
    if not os.path.exists(db_path):
        return [], None, f"DB file not found: {db_path}"
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback'"
            ).fetchone()
            if not has_tbl:
                return [], None, "Table 'feedback' not found"
            # Prefer created_at, else rowid
            try:
                rows = conn.execute(
                    "SELECT rating FROM feedback ORDER BY datetime(created_at) DESC LIMIT ?",
                    (window,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    "SELECT rating FROM feedback ORDER BY rowid DESC LIMIT ?",
                    (window,),
                ).fetchall()
            ratings = [float(r["rating"]) for r in rows if r["rating"] is not None]
            avg = (sum(ratings) / len(ratings)) if ratings else None
            return ratings, avg, None
    except Exception as ex:
        return [], None, f"DB read error: {ex}"

# ================= VISUALS (pygame) =================
def clamp(v, lo, hi): return max(lo, min(hi, v))
def map01(x, lo, hi):
    if hi == lo: return 0.0
    return clamp((x - lo) / float(hi - lo), 0.0, 1.0)

def _clamp8(x): return max(0, min(255, int(x)))
def _jitter(rgb, spread=JITTER):
    r,g,b = rgb
    return (_clamp8(r + random.randint(-spread, spread)),
            _clamp8(g + random.randint(-spread, spread)),
            _clamp8(b + random.randint(-spread, spread)))

def _choose_palette(current, eps) -> Tuple[dict, bool]:
    """ε-greedy: with prob eps pick a new palette; else keep current. Returns (palette, changed?)."""
    changed = False
    if current is None or random.random() < eps: #first time
        base = random.choice(PALETTES)
        # avoid picking the exact same palette when exploring
        tries = 0
        while current is not None and base == current and tries < 5:
            base = random.choice(PALETTES); tries += 1
        current = base
        changed = True
    # jitter to keep things lively
    pal = {"bg": _jitter(current["bg"]),
           "wave": _jitter(current["wave"]),
           "circle": _jitter(current["circle"])}
    return pal, changed

def main():
    global last_motion_flash
    start_mqtt()
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"[HTTP] Listening on http://{HTTP_HOST}:{HTTP_PORT}/ingest (and /update)")
    print("[INFO] Source follows MQTT topic 'smartart/cmd/mode' (mqtt/http)")

    # Pygame setup
    pygame.init()
    W, H = 1000, 700
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("SmartArt — Mode 0 Only (with threshold & epsilon)")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 20)

    # feedback logging cadence
    last_fb_log = 0.0

    # palette / epsilon cadence
    last_pal_tick = 0.0
    current_palette = None  # dict with bg, wave, circle

    t = 0
    running = True

    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                running = False

        # ---- Feedback logging & policy evaluation ----
        now = time.time()
        do_fb_log = (FEEDBACK_PRINT_EVERY == 0) or ((now - last_fb_log) >= FEEDBACK_PRINT_EVERY)
        do_palette_tick = (now - last_pal_tick) >= PALETTE_CHECK_EVERY or current_palette is None

        if do_fb_log or do_palette_tick:
            ratings, avg, err = read_recent_feedback(DB_PATH, FEEDBACK_WINDOW)
            if err:
                print(f"[FEEDBACK] {err}")
                eps = EPS_NEUTRAL
                mode = "NEUTRAL"
            else:
                if ratings:
                    print(f"[FEEDBACK] n={len(ratings)} last{FEEDBACK_WINDOW}={ratings}  avg={(sum(ratings)/len(ratings)):.3f}")
                else:
                    print("[FEEDBACK] No ratings yet")
                if avg is None:
                    eps = EPS_NEUTRAL; mode = "NEUTRAL"
                elif avg < THRESH:
                    eps = EPS_EXPLORE; mode = "EXPLORE"
                else:
                    eps = EPS_EXPLOIT; mode = "EXPLOIT"

            if do_palette_tick:
                current_palette, changed = _choose_palette(current_palette, eps)
                print(f"[POLICY] avg={('n/a' if avg is None else f'{avg:.2f}')}  THRESH={THRESH:.2f}  "
                      f"epsilon={eps:.2f}  mode={mode}  palette_changed={changed}")
                last_pal_tick = now

            if do_fb_log:
                last_fb_log = now

        # ---- Visuals ----
        t += 1
        with data_lock:
            temp = float(data.get("temp", 25.0))
            hum = float(data.get("hum", 50.0))
            light = int(float(data.get("light", 2000)))
            motion = int(float(data.get("motion", 0)))

        # Background combines ambient light + palette base
        light_norm = map01(light, 0, 4095)
        env_bg = (int(light_norm*85), int(light_norm*128), int(light_norm*255))
        base_bg = current_palette["bg"] if current_palette else (18,18,40)
        # blend 65% palette, 35% ambient
        bg_col = (
            _clamp8(base_bg[0]*0.65 + env_bg[0]*0.35),
            _clamp8(base_bg[1]*0.65 + env_bg[1]*0.35),
            _clamp8(base_bg[2]*0.65 + env_bg[2]*0.35),
        )
        screen.fill(bg_col)

        # Mode 0: rings (temperature) + waves (humidity)
        rings = int(clamp(3 + (temp - 15) / 5, 1, 10))
        base_circle = current_palette["circle"] if current_palette else (140,90,255)
        for i in range(rings):
            r = 40 + i * 26 + 12 * math.sin((t / 30) + i * 0.6)
            col = (
                _clamp8(base_circle[0] + i*8),
                _clamp8(base_circle[1] + i*6),
                _clamp8(base_circle[2] - i*6),
            )
            pygame.draw.circle(screen, col, (W // 2, H // 2), int(r), 2)

        amp = int(8 + hum / 3)
        step_y = 30
        wave_color = current_palette["wave"] if current_palette else (30,200,150)
        for y in range(120, H - 120, step_y):
            pts = []
            for x in range(0, W, 18):
                yy = y + int(amp * math.sin((x + t * 2) / 80.0))
                pts.append((x, yy))
            pygame.draw.lines(screen, wave_color, False, pts, 2)

        # Motion flash overlay
        if pygame.time.get_ticks() - last_motion_flash < FLASH_MS:
            overlay = pygame.Surface((W, H), pygame.SRCALPHA)
            overlay.fill((255, 255, 255, 120))
            screen.blit(overlay, (0, 0))

        # HUD
        hud = f"Mode0 | Source:{get_update_source().upper()} | TH={THRESH:.1f}"
        screen.blit(font.render(hud, True, (255, 255, 255)), (10, 10))

        pygame.display.flip()
        clock.tick(60)

    try:
        m.loop_stop(); m.disconnect()
    except Exception:
        pass
    pygame.quit()

if __name__ == "__main__":
    main()
