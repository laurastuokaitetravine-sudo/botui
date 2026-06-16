import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- VIEŠAS KLIENTAS KAINAI (SU PROXY, BE API KEY) ---
public_exchange_config = {
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'swap'
    }
}
if os.getenv('PROXY_URL'):
    public_exchange_config['proxies'] = {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
public_exchange = ccxt.mexc(public_exchange_config)


# --- PRIVATUS KLIENTAS ORDERIAMS (SU API KEY + PROXY + LAIKO TAISYMU) ---
private_exchange_config = {
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'timeout': 30000,
    'adjustForTimeDifference': True,  # Svarbu: sutvarko parašo/laiko klaidas per proxy
    'options': {
        'defaultType': 'swap'
    }
}
if os.getenv('PROXY_URL'):
    private_exchange_config['proxies'] = {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
private_exchange = ccxt.mexc(private_exchange_config)


MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 5
MARGIN_USDT = 10.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (1x LIMIT 100%, TP TIK IŠ PLOT_1 TIESIAI Į TP_PRICE)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        
        if not data:
            try:
                raw_data = request.data.decode('utf-8').strip()
                data = json.loads(raw_data)
            except Exception:
                return {"error": "Invalid JSON format"}, 400

        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker in request"}, 400

        # Monetų tvarkymo logika
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        # --- SAUGUS KAINOS GAVIMAS ---
        ticker = None
        for attempt in range(3):
            try:
                ticker = public_exchange.fetch_ticker(symbol)
                if ticker:
                    break
            except Exception as ne:
                print(f"Klaida gaunant kainą (Bandymas {attempt + 1}/3): {ne}")
                time.sleep(2)

        if not ticker:
            return {"error": "Nepavyko gauti kainos iš MEXC. Patikrinkite Proxy."}, 400

        entry_price = float(ticker['ask'])

        # --- KAINŲ SKAITYMAS IR APVALINIMAS ---
        try:
            sl_price = float(data.get('sl_price'))
            tp_raw = data.get('tp_price')
            
            # Patikriname, ar TP kaina ateina iš TradingView
            has_tp = tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', '']
            tp_price = float(tp_raw) if has_tp else None
        except:
            return {"error": "Blogas kainų formatas iš TradingView"}, 400

        # BTC ar kitų monetų tikslus kainų apvalinimas
        entry_price = round(entry_price, 2 if "BTC" in symbol else 4)
        sl_price = round(sl_price, 2 if "BTC" in symbol else 4)
        if tp_price is not None:
            tp_price = round(tp_price, 2 if "BTC" in symbol else 4)

        # --- KIEKIO SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * DEFAULT_LEVERAGE
        raw_crypto_amount = total_value / entry_price
        
        # Kontraktų kiekis (BTC dažniausiai leidžia iki 3–4 ženklų po kablelio, pvz., 0.01 BTC)
        final_amount = round(raw_crypto_amount, 3 if "BTC" in symbol else 0)
        if final_amount <= 0:
            final_amount = 0.001 if "BTC" in symbol else 1.0

        # --- SVERTO NUSTATYMAS FONE ---
        try:
            private_exchange.set_leverage(int(DEFAULT_LEVERAGE), symbol, {'openType': 1, 'positionType': 2})
        except Exception as lev_err:
            print(f"Sverto žinutė fone: {lev_err}")

        # Atsakymo objektas, kurį grąžinsime
        response_data = {
            "status": "success",
            "symbol": symbol
        }

        # --- 1. LIMIT SHORT ORDERIS ---
        entry_order = private_exchange.create_order(
            symbol=symbol,
            type='limit',  # 1 / limit
            side='sell',
            amount=final_amount,
            price=entry_price,
            params={
                'posSide': 'SHORT',
                'openType': 1,
                'leverage': int(DEFAULT_LEVERAGE),
                'timeInForce': 'PostOnly'
            }
        )
        response_data["entry_id"] = entry_order['id']

        # --- 2. STOP LOSS ORDERIS (SIUNČIAMAS KAIP TRIGGER/MARKET) ---
        sl_order = private_exchange.create_order(
            symbol=symbol,
            type='market',  # Paverčiame į rinkos tipą, nes tai bus stabdymo trigeris
            side='buy',
            amount=final_amount,
            params={
                'posSide': 'SHORT',
                'leverage': int(DEFAULT_LEVERAGE),
                'reduceOnly': True,
                # Specialūs MEXC parametrai Stop Loss aktyvavimui:
                'stopPrice': sl_price,
                'triggerType': 'trade'  
            }
        )
        response_data["sl_id"] = sl_order['id']

        # --- 3. TAKE PROFIT ORDERIS (SIUNČIAMAS KAIP TRIGGER/MARKET) ---
        if tp_price is not None:
            tp_order = private_exchange.create_order(
                symbol=symbol,
                type='market',  # Paverčiame į rinkos tipą pelno paėmimui
                side='buy',
                amount=final_amount,
                params={
                    'posSide': 'SHORT',
                    'leverage': int(DEFAULT_LEVERAGE),
                    'reduceOnly': True,
                    # Specialūs MEXC parametrai Take Profit aktyvavimui:
                    'stopPrice': tp_price,
                    'triggerType': 'trade'
                }
            )
            response_data["tp_id"] = tp_order['id']
            print(f"SĖKMĖ: SHORT LIMIT pastatytas monetai {symbol}! SL: {sl_price} | TP: {tp_price}")
        else:
            print(f"SĖKMĖ: SHORT LIMIT pastatytas monetai {symbol}! SL: {sl_price} | TP: Nenustatytas")

        return response_data, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
