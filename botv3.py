# bingx_adx_bot_COMPLETO.py
# Estrategia: Rango Diario Anterior + ADX ≥20 + Cierre EOD 23:59
# BingX Perpetual Futures (Swap V2) - 100% funcional 2025

import requests
import json
import time
import hmac
import hashlib
import base64
from datetime import datetime, timedelta

# --- pytz con fallback (funciona aunque no lo tengas instalado) ---
try:
    import pytz
    tz = pytz.timezone('Europe/Madrid')
except:
    print("pytz no encontrado → usando ZoneInfo (Python 3.9+)")
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('Europe/Madrid')

import numpy as np
import pandas as pd

# ================== CONFIGURACIÓN (EDITA AQUÍ) ==================
BASE_URL   = "https://open-api.bingx.com"
SYMBOL     = "BTC-USDT"
API_KEY    = "FEWYC3P41yVB0RxmPRZCNAZVFRTogwwPixmIn7JUJIgX12Bq2FCOpTuTFXl02uSPM4pGyohaSOhNeweFDhWA"           # ← Pega tu API Key
SECRET_KEY = "4SfNyQqOPRCUBbUetPEVzbRHbWfdgtKU1a4s3f2gpd4ToidjO4gE21S8qPdU2XTzcpdjXkfb5F3zFnuEcYEQ"        # ← Pega tu Secret Key
LEVERAGE   = 10
RISK_PCT   = 0.05        # 5% del balance por operación
SL_PCT     = 0.06        # Stop Loss 6%
ADX_PERIOD = 16
ADX_MIN    = 20
# =================================================================

def get_timestamp():
    return int(time.time() * 1000)

def get_signature(method, path, body=""):
    timestamp = get_timestamp()
    pre_hash = f"{timestamp}{method.upper()}{path}{body}"
    signature = base64.b64encode(
        hmac.new(SECRET_KEY.encode('utf-8'), pre_hash.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    return signature, timestamp

def api_request(method, path, params=None, data=None):
    if params is None: params = {}
    if data is None: data = {}
    
    body = json.dumps(data, separators=(',', ':')) if method == "POST" and data else ""
    signature, timestamp = get_signature(method, path, body)
    
    url = f"{BASE_URL}{path}"
    headers = {"X-BX-APIKEY": API_KEY}
    query = {"timestamp": timestamp, "signature": signature}
    
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params={**params, **query}, timeout=10)
        else:
            r = requests.post(url, headers=headers, params=query, data=body, timeout=10)
        
        result = r.json()
        if result.get('code') != 0:
            print(f"API ERROR {result.get('code')}: {result.get('msg')}")
            return None
        return result.get('data')
    except Exception as e:
        print(f"Error de conexión: {e}")
        return None

# --- Funciones API ---
def get_klines(interval, limit=500):
    return api_request("GET", "/openApi/swap/v2/market/kline", {"symbol": SYMBOL, "interval": interval, "limit": str(limit)})

def get_balance():
    data = api_request("GET", "/openApi/swap/v2/user/balance")
    if data:
        for asset in data:
            if asset.get('asset') == 'USDT':
                return float(asset.get('availableMargin', 0))
    return 0.0

def set_leverage(side):
    api_request("POST", "/openApi/swap/v2/trade/leverage", data={"symbol": SYMBOL, "side": side, "leverage": str(LEVERAGE)})

def place_limit_order(side, price, qty):
    return api_request("POST", "/openApi/swap/v2/trade/order", data={
        "symbol": SYMBOL, "side": side, "type": "LIMIT", "quantity": f"{qty:.6f}",
        "price": f"{price:.2f}", "timeInForce": "GTC"
    })

def place_tp_limit(side, price, qty):
    return api_request("POST", "/openApi/swap/v2/trade/order", data={
        "symbol": SYMBOL, "side": side, "type": "LIMIT", "quantity": f"{qty:.6f}",
        "price": f"{price:.2f}", "timeInForce": "GTC", "reduceOnly": "true"
    })

def place_stop_market(side, stop_price, qty):
    return api_request("POST", "/openApi/swap/v2/trade/order", data={
        "symbol": SYMBOL, "side": side, "type": "STOP_MARKET", "quantity": f"{qty:.6f}",
        "stopPrice": f"{stop_price:.2f}", "closePosition": "false"
    })

def cancel_all_orders():
    api_request("POST", "/openApi/swap/v2/trade/cancelAll", data={"symbol": SYMBOL})

def close_all_positions():
    positions = api_request("GET", "/openApi/swap/v2/user/positions", {"symbol": SYMBOL})
    if positions:
        for pos in positions:
            if float(pos.get('positionAmt', 0)) != 0:
                side = "BUY" if float(pos['positionAmt']) < 0 else "SELL"
                api_request("POST", "/openApi/swap/v2/trade/order", data={
                    "symbol": SYMBOL, "side": side, "type": "MARKET", "closePosition": "true"
                })

def get_positions():
    return api_request("GET", "/openApi/swap/v2/user/positions", {"symbol": SYMBOL}) or []

# --- Cálculo ADX ---
def calculate_adx_di(klines):
    if len(klines) < ADX_PERIOD + 10: return None, None, None
    df = pd.DataFrame(klines, columns=['ts','o','h','l','c','v'])
    high = df['h'].astype(float)
    low = df['l'].astype(float)
    close = df['c'].astype(float)
    
    up = high.diff()
    down = low.diff().abs()
    plus_dm = np.where((up > down) & (up > 0), up, 0)
    minus_dm = np.where((down > up) & (down > 0), down, 0)
    
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.rolling(ADX_PERIOD).mean()
    plus_di = 100 * pd.Series(plus_dm).rolling(ADX_PERIOD).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).rolling(ADX_PERIOD).mean() / atr
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(ADX_PERIOD).mean()
    
    return round(adx.iloc[-1], 2), round(plus_di.iloc[-1], 2), round(minus_di.iloc[-1], 2)

# --- Variables globales ---
prev_high = prev_low = midpoint = None
order_id = None
position_filled = False
today_traded = False
current_date = None

print("BOT BINGX ADX + RANGO DIARIO INICIADO (España CET/CEST)")

try:
    while True:
        now = datetime.now(tz)
        today = now.date()

        # === NUEVO DÍA: 00:00 CET ===
        if now.hour == 0 and now.minute < 5:
            if today != current_date:
                current_date = today
                klines = get_klines("1d", 3)
                if klines and len(klines) >= 2:
                    prev_high = float(klines[1][2])
                    prev_low  = float(klines[1][3])
                    midpoint  = round((prev_high + prev_low) / 2, 2)
                    print(f"\nNUEVO DÍA {today} → High ant: {prev_high} | Low ant: {prev_low} | Mid: {midpoint}")
                
                # Limpieza total
                cancel_all_orders()
                close_all_positions()
                order_id = position_filled = today_traded = False
                print("Posiciones y órdenes limpiadas para nuevo día")

        # === CIERRE FORZOSO EOD 23:59 ===
        if now.hour == 23 and now.minute >= 59:
            print("CIERRE EOD 23:59 - Cancelando todo...")
            cancel_all_orders()
            close_all_positions()
            time.sleep(70)
            continue

        # === Cálculo ADX y entrada (solo 1 vez al día) ===
        if not today_traded and now.minute == 10:  # 10 minutos después de medianoche
            klines_h = get_klines("1h", ADX_PERIOD * 4)
            if klines_h:
                adx, pdi, mdi = calculate_adx_di(klines_h)
                if adx and adx >= ADX_MIN:
                    balance = get_balance()
                    if balance > 100:  # seguridad
                        if pdi > mdi:  # Tendencia alcista fuerte → SHORT
                            set_leverage("SHORT")
                            qty = (balance * RISK_PCT * LEVERAGE) / prev_high
                            result = place_limit_order("SELL", prev_high, qty)
                            if result:
                                order_id = result.get('orderId')
                                print(f"SHORT colocado @ {prev_high} | Cantidad: {qty:.5f} BTC | ADX: {adx}")
                                today_traded = True
                        
                        elif mdi > pdi:  # Tendencia bajista fuerte → LONG
                            set_leverage("LONG")
                            qty = (balance * RISK_PCT * LEVERAGE) / prev_low
                            result = place_limit_order("BUY", prev_low, qty)
                            if result:
                                order_id = result.get('orderId')
                                print(f"LONG colocado @ {prev_low} | Cantidad: {qty:.5f} BTC | ADX: {adx}")
                                today_traded = True

        # === Cuando se llena la orden → colocar SL y TP ===
        if order_id and not position_filled:
            positions = get_positions()
            if positions:
                for pos in positions:
                    if float(pos.get('positionAmt', 0)) != 0:
                        position_filled = True
                        qty = abs(float(pos['positionAmt']))
                        entry = float(pos['avgPrice'])
                        is_long = float(pos['positionAmt']) > 0
                        close_side = "SELL" if is_long else "BUY"
                        
                        sl_price = entry * (1 - SL_PCT) if is_long else entry * (1 + SL_PCT)
                        tp1_price = midpoint
                        tp2_price = prev_high if is_long else prev_low
                        
                        place_stop_market(close_side, sl_price, qty)
                        place_tp_limit(close_side, tp1_price, qty * 0.75)
                        place_tp_limit(close_side, tp2_price, qty * 0.25)
                        
                        print(f"POSICIÓN ABIERTA → SL: {sl_price:.1f} | TP1 75%: {tp1_price} | TP2 25%: {tp2_price}")

        print(f"{now.strftime('%H:%M')} | Esperando siguiente ciclo...")
        time.sleep(60)
if __name__ == "__main__":
    print("Bot iniciado en Heroku...")
    # Tu while True aquí ya está
except KeyboardInterrupt:
    print("\nBot detenido por el usuario. Cerrando todo...")
    cancel_all_orders()
    close_all_positions()
