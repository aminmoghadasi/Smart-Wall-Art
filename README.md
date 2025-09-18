# Smart Wall Art IoT

ESP32-based ambient sensing + data-driven visual art with adaptive color palettes powered by user feedback (Telegram bot), MQTT/HTTP streaming to InfluxDB, Grafana dashboards, and simple forecasting scripts.

> Owner: Amin • University of Bologna • Last updated: 2025-09-18

## Repo layout

```
SmartArt-IoT/
├── firmware/esp32/                # ESP32 C++ firmware (sensing, OLED, RGB, MQTT/HTTP)
├── services/data_proxy/           # Python Flask proxy (MQTT→InfluxDB, HTTP ingest)
├── services/grafana/              # Dashboards JSON + notes
├── algorithms/user_engagement/    # Epsilon-greedy + feedback aggregation
├── algorithms/forecasting/        # Simple DecisionTree lag-based forecaster
├── bots/telegram_feedback_bot/    # Telegram bot (SQLite) for 0–5 ratings
├── visuals/                       # VisualArt_auto_mode.py (shapes/colors logic)
├── storage/                       # SQLite schemas, migrations, sample data
├── docs/                          # LaTeX/Overleaf report and images
└── .github/workflows/             # CI for Python lint & tests
```
## Pic Of Project
![Demo Screenshot](https://github.com/aminmoghadasi/Smart-Wall-Art/blob/main/Untitled%20Sketch_bb.png?raw=true)

[Alt text](11.jpg)

## Quick start

1. **Clone & enter**  
   ```bash
   git clone Smart-Wall-Art-IoT
   cd SmartArt-IoT
   ```

2. **Python env (3.10+)**  
   ```bash
   python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r services/data_proxy/requirements.txt -r bots/telegram_feedback_bot/requirements.txt -r algorithms/forecasting/requirements.txt
   ```

3. **Copy env samples and edit**  
   ```bash
   cp .env.example .env
   cp services/data_proxy/.env.example services/data_proxy/.env
   cp bots/telegram_feedback_bot/.env.example bots/telegram_feedback_bot/.env
   ```

4. **Run data proxy**  
   ```bash
   cd services/data_proxy
   python app.py
   ```

5. **Run Telegram bot**  
   ```bash
   cd bots/telegram_feedback_bot
   python bot.py
   ```

6. **Run visuals (dev)**  
   ```bash
   cd visuals
   python VisualArt_auto_mode.py
   ```

7. **Forecasting demo**  
   ```bash
   cd algorithms/forecasting
   python predict_temp.py
   ```

## Tech stack

- **Device:** ESP32 + DHT11, PIR, LDR, OLED, RGB LED
- **Transport:** MQTT (Mosquitto) and/or HTTP
- **DB:** InfluxDB Cloud, bucket `ArtWall`
- **Viz:** Grafana dashboards
- **Engagement:** Telegram bot (+ SQLite), epsilon-greedy palette policy
- **Forecasting:** Scikit-learn DecisionTreeRegressor with simple cross-lag features

## Contributing

Please run linters before pushing:

```bash
ruff check .
black --check .
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
