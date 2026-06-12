import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- VIEŠAS KLIENTAS KAINAI (SU PROXY, BE API KEY) ---
public_exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap'
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})

# --- PRIVATUS KLIENTAS ORDERIAMS (SU API KEY + PROXY) ---
private_exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'swap'
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 50.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (FIXED FUTURES SYSTEM)", 200

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

        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        # --- KAINA IŠ VIEŠO KLIENTO (NEBEKVIEČIA SPOT WALLET) ---
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

        try:
            sl_price = float(data.get('sl_price'))
            tp_raw = data.get('tp_price_1')
            tp_price = float(tp_raw) if tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.50
        except:
            return {"error": "Blogas kainų formatas iš TradingView"}, 400

        entry_price = round(entry_price, 4)
        sl_price = round(sl_price, 4)
        tp_price = round(tp_price, 4)

        total_value = MARGIN_USDT * DEFAULT_LEVERAGE
        raw_crypto_amount = total_value / entry_price
        final_amount = round(raw_crypto_amount, 0)
        if final_amount < 1:
            final_amount = 1.0

        # --- LEVERAGE ---
        try:
            private_exchange.set_leverage(int(DEFAULT_LEVERAGE), symbol, {'openType': 1, 'positionType': 2})
        except Exception as lev_err:
            print(f"Sverto žinutė: {lev_err}")

        # --- LIMIT SHORT ORDERIS ---
        entry_order = private_exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=final_amount,
            price=entry_price,
            params={
                'posSide': 'SHORT',
                'openType': 1,
                'timeInForce': 'PostOnly'
            }
        )

        # --- STOP LOSS (trigger market) ---
        sl_order = private_exchange.create_order(
            symbol=symbol,
            type='stop_market',
            side='buy',
            amount=final_amount,
            params={
                'stopPrice': sl_price,
                'triggerPrice': sl_price,
                'posSide': 'SHORT',
                'reduceOnly': True
            }
        )

        # --- TAKE PROFIT (trigger market) ---
        tp_order = private_exchange.create_order(
            symbol=symbol,
            type='take_profit_market',
            side='buy',
            amount=final_amount,
            params={
                'stopPrice': tp_price,
                'triggerPrice': tp_price,
                'posSide': 'SHORT',
                'reduceOnly': True
            }
        )

        print(f"SĖKMĖ: SHORT LIMIT pastatytas {symbol}! Kiekis: {final_amount} | Kaina: {entry_price}")
        return {
            "status": "success",
            "symbol": symbol,
            "entry_id": entry_order['id'],
            "sl_id": sl_order['id'],
            "tp_id": tp_order['id']
        }, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
