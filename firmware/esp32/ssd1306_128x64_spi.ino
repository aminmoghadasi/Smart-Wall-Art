#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <HTTPClient.h>   

// ==== Wi-Fi & MQTT ====
const char* ssid        = "aAmina";
const char* password    = "87654321";
const char* mqtt_server = "10.82.74.203";   // Broker IP

// ==== Pins ====
#define DHTPIN    4
#define PIR_PIN   15
#define LDR_PIN   34
#define RED_PIN   27
#define GREEN_PIN 26
#define BLUE_PIN  25

// ==== RGB type ====
#define COMMON_ANODE false  // set true if your RGB LED is common anode

// ==== OLED ====
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
#define SCREEN_ADDRESS 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ==== DHT ====
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// ==== MQTT ====
WiFiClient espClient;
PubSubClient client(espClient);    // Used the wi-fi TCP connection as its communication channel

// ==== Communication mode ====
enum CommMode { MODE_MQTT, MODE_HTTP };
CommMode commMode = MODE_MQTT;  // default

// HTTP endpoint
const char* http_url = "http://10.21.127.203:8080/ingest";   //HTTP endpoint

// Runtime config
unsigned long sampleDelayMs = 2000; //time between complete sensor scans
int motionThreshold = 1;   //how many consecutive PIR detections
int motionCount = 0;  
unsigned long lastSample = 0;

void setRGB(uint8_t r, uint8_t g, uint8_t b){
  if (COMMON_ANODE) { r = 255 - r; g = 255 - g; b = 255 - b; }
  ledcWrite(0, r);
  ledcWrite(1, g);
  ledcWrite(2, b);
}

void setup_wifi() {
  Serial.printf("Connecting to %s\n", ssid);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.println("\n WiFi connected");
  Serial.print("IP: "); Serial.println(WiFi.localIP());
}

// —— Telemetry send helpers ——
bool sendViaMQTT(const String& json) {
  return client.publish("smartart/sensordata", json.c_str()); // Publisher MQTT on the topic of smartart/sensordata .publish(topic, message) = sends a message to the MQTT broker.
}

bool sendViaHTTP(const String& json) {
  HTTPClient http;
  http.begin(http_url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST((uint8_t*)json.c_str(), json.length());
  http.end();
  return (code >= 200 && code < 300);
}


// message handler
void onCmd(char* topic, byte* payload, unsigned int length) {
  String t = topic, msg;
  for (unsigned i=0;i<length;i++) msg += (char)payload[i];
  msg.trim();
  Serial.print("CMD "); Serial.print(t); Serial.print(" = "); Serial.println(msg);

  if (t == "smartart/cmd/sampling_rate") {
    long s = msg.toInt();
    if (s > 0 && s <= 3600) { sampleDelayMs = (unsigned long)s * 1000UL; Serial.printf("→ sampling_rate=%lds\n", s); } // valid range and covert it to milsecond
  } else if (t == "smartart/cmd/motion_alert") {
    int thr = msg.toInt(); if (thr < 1) thr = 1; if (thr > 10) thr = 10;
    motionThreshold = thr; Serial.printf("→ motion_alert=%d\n", thr);
  } else if (t == "smartart/cmd/mode") {   
    msg.toLowerCase();
    if      (msg == "mqtt") { commMode = MODE_MQTT; Serial.println("→ mode=MQTT"); }
    else if (msg == "http") { commMode = MODE_HTTP; Serial.println("→ mode=HTTP"); }
    else { Serial.println("→ mode=UNKNOWN (ignoring)"); }
  }
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");
    if (client.connect("ESP32SmartArt")) {
      Serial.println("connected");
      client.subscribe("smartart/cmd/sampling_rate");     // subscribe of MQTT
      client.subscribe("smartart/cmd/motion_alert");    // subscribe of MQTT
      client.subscribe("smartart/cmd/mode");           // subscribe of MQTT
    } else {
      Serial.print("failed, rc="); Serial.print(client.state()); Serial.println(" retry in 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);

  // I2C for OLED
  Wire.begin(21, 22); // SDA, SCL

  // Sensors
  dht.begin();
  pinMode(PIR_PIN, INPUT);

  // OLED init
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F(" OLED not found (check address and wiring)"));
    while (true) { delay(1000); }
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);
  display.println("SmartArt booting...");
  display.display();

  // RGB PWM
  ledcSetup(0, 5000, 8); ledcAttachPin(RED_PIN, 0);
  ledcSetup(1, 5000, 8); ledcAttachPin(GREEN_PIN, 1);
  ledcSetup(2, 5000, 8); ledcAttachPin(BLUE_PIN, 2);
  setRGB(0,0,0);

  // Wi-Fi & MQTT
  setup_wifi();
  client.setServer(mqtt_server, 1883);   //Configures MQTT broker address
  client.setCallback(onCmd);
}

void loop() {
  // Maintain MQTT service (even if not the chosen telemetry mode, we still use MQTT for commands)
  if (!client.connected()) reconnect();    //Keep MQTT service alive
  client.loop();

  unsigned long now = millis();
  if (now - lastSample < sampleDelayMs) return;
  lastSample = now;

  float t = dht.readTemperature();
  float h = dht.readHumidity();
  int   lightVal = analogRead(LDR_PIN);
  int   pir      = digitalRead(PIR_PIN);

  // Motion filter
  motionCount = (pir == HIGH) ? (motionCount + 1) : 0;
  int motion = (motionCount >= motionThreshold) ? 1 : 0;

  if (isnan(t) || isnan(h)) {
    Serial.println("DHT read failed"); 
    return;
  }

  // RGB based on light (tweak thresholds for your LDR divider)
  if (lightVal > 3000)      setRGB(0,0,255);   
  else if (lightVal > 1500) setRGB(0,255,0);   
  else                      setRGB(255,0,0);   

  // OLED output
  display.clearDisplay();
  display.setCursor(0,0);
  display.printf("Temp: %.1f C\n", t);
  display.printf("Hum : %.1f %%\n", h);
  display.printf("Light: %d\n", lightVal);
  display.printf("Motion: %s\n", motion ? "YES" : "NO");
  display.display();

  // Build JSON
  String payload = "{";
  payload += "\"device_id\":\"esp32-smartart-01\",";
  payload += "\"ts_ms\":" + String(millis()) + ",";
  payload += "\"temp\":"   + String(t,1) + ",";
  payload += "\"hum\":"    + String(h,1)  + ",";
  payload += "\"light\":"  + String(lightVal) + ",";
  payload += "\"motion\":" + String(motion);
  payload += "}";

  // Send according to selected mode
  bool ok = false;
  switch (commMode) {
    case MODE_MQTT: ok = sendViaMQTT(payload); break;
    case MODE_HTTP: ok = sendViaHTTP(payload); break;
  }
  Serial.println(ok ? "Telemetry sent" : "Telemetry send failed");
}
