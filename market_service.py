#!/usr/bin/env python3
"""
TRIDENT Market Intelligence Service
Provides free market data via yfinance, Finnhub, FRED
Runs on port 5002
"""
import os, json, time, datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import requests

app = Flask(__name__)
CORS(app)

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FRED_KEY = os.environ.get("FRED_API_KEY", "")
CACHE = {}
CACHE_TTL = 300  # 5 min cache

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "TRIDENT Markets"})

@app.route("/quote/<symbol>")
def quote(symbol):
    try:
        ticker = yf.Ticker(symbol.upper())
        info = ticker.fast_info
        hist = ticker.history(period="1d", interval="5m")
        current = float(info.last_price) if hasattr(info, 'last_price') else 0
        prev_close = float(info.previous_close) if hasattr(info, 'previous_close') else 0
        change = current - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0
        return jsonify({
            "symbol": symbol.upper(),
            "price": round(current, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
            "high": round(float(info.day_high), 2) if hasattr(info, 'day_high') else 0,
            "low": round(float(info.day_low), 2) if hasattr(info, 'day_low') else 0,
            "volume": int(info.last_volume) if hasattr(info, 'last_volume') else 0,
            "market_cap": int(info.market_cap) if hasattr(info, 'market_cap') else 0,
            "intraday": [{"t": str(ts), "o": round(float(row["Open"]),2), "h": round(float(row["High"]),2), "l": round(float(row["Low"]),2), "c": round(float(row["Close"]),2), "v": int(row["Volume"])} for ts, row in hist.iterrows()],
            "source": "yfinance"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/history/<symbol>")
def history(symbol):
    period = request.args.get("period", "1mo")
    interval = request.args.get("interval", "1d")
    try:
        ticker = yf.Ticker(symbol.upper())
        hist = ticker.history(period=period, interval=interval)
        data = [{"t": str(ts.date()), "o": round(float(row["Open"]),2), "h": round(float(row["High"]),2), "l": round(float(row["Low"]),2), "c": round(float(row["Close"]),2), "v": int(row["Volume"])} for ts, row in hist.iterrows()]
        return jsonify({"symbol": symbol.upper(), "period": period, "interval": interval, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/quotes")
def quotes():
    symbols = request.args.get("symbols", "SPY,QQQ,DIA,GLD,SLV,USO,BTC-USD,ETH-USD").split(",")
    results = []
    for sym in symbols[:20]:
        try:
            ticker = yf.Ticker(sym.strip().upper())
            info = ticker.fast_info
            current = float(info.last_price) if hasattr(info, 'last_price') else 0
            prev = float(info.previous_close) if hasattr(info, 'previous_close') else 0
            change_pct = ((current - prev) / prev * 100) if prev else 0
            results.append({
                "symbol": sym.strip().upper(),
                "price": round(current, 2),
                "change_pct": round(change_pct, 2),
                "change": round(current - prev, 2)
            })
        except:
            results.append({"symbol": sym.strip().upper(), "price": 0, "change_pct": 0, "change": 0})
    return jsonify({"quotes": results, "ts": datetime.datetime.utcnow().isoformat()})

@app.route("/market-overview")
def market_overview():
    watchlist = {
        "indices": ["^GSPC", "^DJI", "^IXIC", "^RUT", "^VIX"],
        "commodities": ["GC=F", "SI=F", "CL=F", "NG=F", "HG=F"],
        "crypto": ["BTC-USD", "ETH-USD", "SOL-USD"],
        "bonds": ["^TNX", "^TYX", "^IRX"],
        "fx": ["DX-Y.NYB", "EURUSD=X", "GBPUSD=X", "JPY=X"]
    }
    result = {}
    for category, symbols in watchlist.items():
        result[category] = []
        for sym in symbols:
            try:
                ticker = yf.Ticker(sym)
                info = ticker.fast_info
                current = float(info.last_price) if hasattr(info, 'last_price') else 0
                prev = float(info.previous_close) if hasattr(info, 'previous_close') else 0
                change_pct = ((current - prev) / prev * 100) if prev else 0
                result[category].append({
                    "symbol": sym, "price": round(current, 2),
                    "change_pct": round(change_pct, 2), "change": round(current - prev, 2)
                })
            except:
                result[category].append({"symbol": sym, "price": 0, "change_pct": 0, "change": 0})
    return jsonify(result)

@app.route("/news")
def news():
    symbol = request.args.get("symbol", "")
    try:
        ticker = yf.Ticker(symbol.upper() if symbol else "SPY")
        articles = ticker.news or []
        cleaned = []
        for a in articles[:20]:
            cleaned.append({
                "title": a.get("title", ""),
                "publisher": a.get("publisher", ""),
                "link": a.get("link", ""),
                "published": a.get("providerPublishTime", 0),
                "summary": a.get("summary", "")[:500] if a.get("summary") else "",
                "type": a.get("type", "STORY")
            })
        return jsonify({"news": cleaned, "symbol": symbol or "MARKET"})
    except Exception as e:
        return jsonify({"error": str(e), "news": []}), 200

@app.route("/fred/<series_id>")
def fred_data(series_id):
    if not FRED_KEY:
        return jsonify({"error": "FRED_API_KEY not set"}), 400
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {"series_id": series_id, "api_key": FRED_KEY, "file_type": "json", "sort_order": "desc", "limit": 12}
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        observations = [{"date": o["date"], "value": o["value"]} for o in data.get("observations", []) if o["value"] != "."]
        return jsonify({"series": series_id, "data": observations})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/macro")
def macro():
    if not FRED_KEY:
        return jsonify({"error": "FRED_API_KEY not set", "indicators": []}), 200
    series = [
        ("GDP", "Gross Domestic Product (Billions USD)"),
        ("UNRATE", "Unemployment Rate (%)"),
        ("CPIAUCSL", "CPI All Urban Consumers"),
        ("FEDFUNDS", "Federal Funds Rate (%)"),
        ("DGS10", "10-Year Treasury Yield (%)"),
        ("M2SL", "M2 Money Supply (Billions)"),
        ("DCOILWTICO", "WTI Crude Oil Price"),
    ]
    results = []
    for sid, label in series:
        try:
            url = "https://api.stlouisfed.org/fred/series/observations"
            params = {"series_id": sid, "api_key": FRED_KEY, "file_type": "json", "sort_order": "desc", "limit": 2}
            res = requests.get(url, params=params, timeout=8)
            data = res.json()
            obs = [o for o in data.get("observations", []) if o["value"] != "."]
            if obs:
                current = obs[0]
                prev = obs[1] if len(obs) > 1 else obs[0]
                results.append({
                    "id": sid, "label": label,
                    "value": current["value"], "date": current["date"],
                    "prev_value": prev["value"], "prev_date": prev["date"]
                })
        except:
            pass
    return jsonify({"indicators": results, "source": "FRED"})

if __name__ == "__main__":
    print("[ TRIDENT ] Market Intelligence Service starting on port 5002")
    app.run(host="127.0.0.1", port=5002, debug=False)
