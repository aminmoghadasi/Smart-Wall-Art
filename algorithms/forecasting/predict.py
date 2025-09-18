
import pandas as pd
import matplotlib.pyplot as plt
from influxdb_client import InfluxDBClient
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from pathlib import Path
import os

# -------- Influx settings --------
INFLUX_URL    = "INFLUX_URL"
INFLUX_ORG    = "UNIBO"
INFLUX_BUCKET = "ArtWall"
INFLUX_TOKEN  = "INFLUX_TOKEN"
RANGE         = "-24h"

# ---- Output directory = folder of this script ----
OUTPUT_DIR = Path(__file__).parent.resolve()

def fetch_df():
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    q = f'''
    from(bucket:"{INFLUX_BUCKET}")
      |> range(start: {RANGE})
      |> filter(fn: (r) => r._measurement == "smartart")
      |> filter(fn: (r) => r._field == "temp" or r._field == "hum" or r._field == "light")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> keep(columns: ["_time","temp","hum","light"])
      |> sort(columns: ["_time"])
    '''
    df = client.query_api().query_data_frame(q)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True)
    df = df.rename(columns={"_time":"time"})
    df = df.dropna(subset=["temp","hum","light"]).reset_index(drop=True)
    return df       # returns a dataframe ( ye jadval be chand hezar radif va sish soton)

def add_lags(df, cols=("temp","hum","light"), k=2):
    for c in cols:
        for i in range(1, k+1):
            df[f"{c}_lag{i}"] = df[c].shift(i) # shift by i and make new matrix
    lag_cols = [f"{c}_lag{i}" for c in cols for i in range(1, k+1)]    # make a list of colum lags for example ["temp_lag1","temp_lag2","hum_lag1","hum_lag2","light_lag1","light_lag2"]
    return df.dropna(subset=lag_cols).reset_index(drop=True)

def feature_cols_for_target(target, all_vars=("temp","hum","light"), k=2):
    return [f"{v}_lag{i}" for v in all_vars for i in range(1, k+1)]   # decide which input columns to use for a prediction.

def train_one(df, target, k=2, max_depth=4):   # train and evaluate one predictor (temp, hum, light)
    X = df[feature_cols_for_target(target, k=k)].values
    y = df[target].values
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, shuffle=False)
    reg = DecisionTreeRegressor(max_depth=max_depth, random_state=12).fit(Xtr, ytr)
    pred = reg.predict(Xte) # Uses the trained tree to make predictions on the unseen test set
    mae = mean_absolute_error(yte, pred)
    mse = mean_squared_error(yte, pred)
    out = pd.DataFrame({
        "time": df["time"].iloc[-len(yte):].values,
        f"actual_{target}": yte,
        f"pred_{target}": pred
    })
    return mae, mse, out

def plot_series(out_df, target):
    plt.figure()
    plt.plot(out_df["time"], out_df[f"actual_{target}"], label="Actual")
    plt.plot(out_df["time"], out_df[f"pred_{target}"], label="Predicted")
    plt.xlabel("Time"); plt.ylabel(target.capitalize())
    plt.title(f"{target.capitalize()} forecast (k=2 lags + cross-lags, Decision Tree)")
    plt.legend(); plt.tight_layout()
    save_path = OUTPUT_DIR / f"{target}_pred_timeseries.png"
    plt.savefig(save_path)
    plt.close()
    print(f"Saved plot: {save_path}")

def main():
 

    df = fetch_df()
    if df.empty:
        print("No data from Influx (ArtWall).")
        return

    df_lag = add_lags(df, cols=("temp","hum","light"), k=2)

    combined = None
    for target in ("temp","hum","light"):
        mae, mse, out = train_one(df_lag, target, k=2, max_depth=4)
        combined = out if combined is None else combined.merge(out, on="time", how="inner")
        plot_series(out, target)
        print(f"{target.upper()}: MAE={mae:.3f}  MSE={mse:.3f}")

    csv_path = OUTPUT_DIR / "predictions_all.csv"
    combined.to_csv(csv_path, index=False)
    print(f"Saved CSV: {csv_path}")

if __name__ == "__main__":
    main()
