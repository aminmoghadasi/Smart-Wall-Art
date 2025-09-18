import json, math, threading
from typing import Dict

import pygame
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify

# ================= USER CONFIG =================
MQTT_HOST = "host ip"
MQTT_PORT = 1883

TOPIC_DATA = "smartart/sensordata"
TOPIC_MODE = "smartart/cmd/mode"

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080          
HTTP_DEBUG_LOG = False
# ===============================================

# ---- Shared state ----
data_lock = threading.Lock()
data: Dict[str, float] = {"temp": 25.0, "hum": 50.0, "light": 2000, "motion": 0}
update_source = "mqtt"  # authoritative source for applying updates
last_motion_flash = 0
FLASH_MS = 2500           # Duration of the flash effect (milliseconds).

def set_update_source(src: str):
    global update_source
    src_l = str(src).strip().lower()
    if src_l in ("mqtt", "http"):
        update_source = src_l
        print(f"[MODE] Updated authoritative source -> {update_source.upper()}")
    else:
        print(f"[MODE] Ignored invalid mode '{src}' (expect 'mqtt' or 'http')")

def get_update_source() -> str:
    return update_source

# ================= MQTT =================
def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected rc={rc}; subscribing to topics")
    client.subscribe([(TOPIC_DATA, 0), (TOPIC_MODE, 0)])

def _apply_payload(payload: dict, origin: str):
    global last_motion_flash
    if get_update_source() != origin:
        if HTTP_DEBUG_LOG:
            print(f"[{origin.upper()}] Ignored payload (active source is {get_update_source().upper()})")
        return

    accepted = {k: payload[k] for k in ("temp", "hum", "light", "motion") if k in payload}
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
            if HTTP_DEBUG_LOG:
                print("[MQTT] Applied" if get_update_source()=="mqtt" else "[MQTT] Received (inactive)", payload)
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

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "mode": get_update_source()})

@app.route("/update", methods=["POST"])
@app.route("/ingest", methods=["POST"])
def ingest():
    try:
        payload = request.get_json(force=True, silent=False)
        if HTTP_DEBUG_LOG: print("[HTTP] payload:", payload)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "err": "JSON object required"}), 400
        _apply_payload(payload, origin="http")
        applied = (get_update_source() == "http")
        return jsonify({"ok": True, "mode": get_update_source(), "applied": applied})
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 400

def run_http_server():
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, threaded=True, use_reloader=False)

# ================= VISUALS (pygame) =================
def clamp(v, lo, hi): 
    return max(lo, min(hi, v))

def map01(x, lo, hi):
    if hi == lo: return 0.0
    return clamp((x - lo) / float(hi - lo), 0.0, 1.0)

def main():
    global last_motion_flash
    start_mqtt()
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"[HTTP] Listening on http://{HTTP_HOST}:{HTTP_PORT}/ingest (and /update)")
    print("[INFO] Mode will follow MQTT on topic 'smartart/cmd/mode' (payload: mqtt/http)")

    # Pygame setup
    pygame.init()
    W, H = 1000, 700
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("SmartArt – Mode 0 Only")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 20)

    t = 0
    running = True

    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE: 
                    running = False

        t += 1
        with data_lock:
            temp  = float(data.get("temp",25.0))
            hum   = float(data.get("hum",50.0))
            light = int(float(data.get("light",2000)))
            motion= int(float(data.get("motion",0)))

        # Background from light
        bg = int(map01(light, 0, 4095) * 255)
        screen.fill((bg//3, bg//2, bg))

        # === Mode 0: rings (temperature) + waves (humidity) ===
        rings = int(clamp(3 + (temp - 15)/5, 1, 10))
        for i in range(rings):
            r = 40 + i*26 + 12*math.sin((t/30) + i*0.6)
            col = (100 + i*15, 40 + i*12, 220 - i*18)
            pygame.draw.circle(screen, col, (W//2, H//2), int(r), 2)

        amp = int(8 + hum/3)
        step_y = 30
        for y in range(120, H-120, step_y):
            pts = []
            for x in range(0, W, 18):
                yy = y + int(amp * math.sin((x + t*2)/80.0))
                pts.append((x, yy))
            pygame.draw.lines(screen, (30,200,150), False, pts, 2)

        # Motion flash overlay
        if pygame.time.get_ticks() - last_motion_flash < FLASH_MS:
            overlay = pygame.Surface((W,H), pygame.SRCALPHA)
            overlay.fill((255,255,255,120))
            screen.blit(overlay, (0,0))

        # HUD
        hud = f"T={temp:.1f}C  H={hum:.1f}%  L={light}  M={'YES' if motion else 'NO'}   Source:{get_update_source().upper()}"
        txt = font.render(hud, True, (255,255,255))
        screen.blit(txt, (10, 10))

        pygame.display.flip()
        clock.tick(60)

    try:
        m.loop_stop(); m.disconnect()
    except Exception:
        pass
    pygame.quit()

if __name__ == "__main__":
    main()
