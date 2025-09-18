
import json, time, threading
from flask import Flask, request, jsonify
from paho.mqtt.client import Client as MqttClient
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

MQTT_HOST   = "10.82.74.203"     
MQTT_PORT   = 1883

INFLUX_URL  = "https://us-east-1-1.aws.cloud2.influxdata.com"
INFLUX_ORG  = "UNIBO"
INFLUX_BUCKET = "ArtWall"
INFLUX_TOKEN  = "w2UKi_6EcvG_E0o55JkWiFFWXsfZlIMWE-2VHB04GyWZ3UVq1GQ7QKORpvyErWjgnFfH1L2bb-Q3lPNQe_e2CA=="   

HTTP_HOST   = "0.0.0.0"
HTTP_PORT   = 8080

TOPIC_DATA   = "smartart/sensordata"
TOPIC_RATE   = "smartart/cmd/sampling_rate"
TOPIC_MOTION = "smartart/cmd/motion_alert"

# ==== Influx ====
influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=30000)
write_api = influx.write_api(write_options=SYNCHRONOUS)

def write_measurement(payload: dict):
    for k in ["temp","hum","light","motion"]:
        if k not in payload:
            raise ValueError(f"missing {k}")
        

    device = str(payload.get("device_id", "unknown"))
    ts_ms = payload.get("ts_ms", None)

    # Decide whether ts_ms is a real epoch (ms) or just millis since boot.
    use_custom_time = False
    if isinstance(ts_ms, (int, float)):
        ts_ms = int(ts_ms)
        # Real epoch in ms is around 1_600_000_000_000+ since 2020.
        if ts_ms >= 1_600_000_000_000:   # looks like a real UTC epoch in ms Sept 2020
            ts_ns = ts_ms * 1_000_000    # InfluxDB requires nanoseconds for custom timestamps. ms -> ns
            use_custom_time = True       # override InfluxDB’s “server time”

    p = (                                                       # InfluxDB Point object
        Point("smartart")
        .tag("device_id", device)
        .field("temp", float(payload["temp"]))
        .field("hum", float(payload["hum"]))
        .field("light", int(payload["light"]))
        .field("motion", int(payload["motion"]))
    )

    if use_custom_time:
        p = p.time(ts_ns, write_precision=WritePrecision.NS)
        # else: no .time() → Influx uses server "now"

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)


# ==== MQTT ====
mqtt = MqttClient()

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected rc={rc}")
    client.subscribe(TOPIC_DATA)
    print(f"[MQTT] Subscribed {TOPIC_DATA}")

def on_message(client, userdata, msg):
    if msg.topic == TOPIC_DATA:
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            write_measurement(data)
            print(f"[MQTT] -> Influx: {data}")
        except Exception as e:
            print(f"[ERR] write failed: {e}")

mqtt.on_connect = on_connect
mqtt.on_message = on_message

def start_mqtt():
    while True:
        try:
            mqtt.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            mqtt.loop_forever()
        except Exception as e:
            print(f"[MQTT] reconnect in 3s: {e}")
            time.sleep(3)

# optional helpers to send config back to ESP32 (or just use mosquitto_pub)
def set_sampling_rate(seconds:int): mqtt.publish(TOPIC_RATE, str(seconds))
def set_motion_alert(thr:int):      mqtt.publish(TOPIC_MOTION, str(thr))

# ==== HTTP ingest ====
app = Flask(__name__)
@app.route("/ingest", methods=["POST"])
def ingest():
    try:
        payload = request.get_json(force=True)
        write_measurement(payload)
        print(f"[HTTP] -> Influx: {payload}")
        return jsonify({"ok": True}), 200
    except Exception as e:
        print(f"[HTTP][ERR] {e}")
        return jsonify({"ok": False, "error": str(e)}), 400

def start_http():
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False)

if __name__ == "__main__":
    print(f"Proxy: MQTT={MQTT_HOST}:{MQTT_PORT}  Influx={INFLUX_URL}  Bucket={INFLUX_BUCKET}  HTTP=:{HTTP_PORT}")
    t1 = threading.Thread(target=start_mqtt, daemon=True); t1.start()
    t2 = threading.Thread(target=start_http, daemon=True); t2.start()
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt: pass
