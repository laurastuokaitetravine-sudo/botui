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
    'adjustForTimeDifference': True,
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
MARGIN_USDT = 15.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (FIXED SINGLE-ORDER SL/TP SYSTEM)", 200

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
            
            has_tp = tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', '']
            tp_price = float(tp_raw) if has_tp else None
        except:
            return {"error": "Blogas kainų formatas iš TradingView"}, 400

        entry_price = round(entry_price, 2 if "BTC" in symbol else 4)
        sl_price = round(sl_price, 2 if "BTC" in symbol else 4)
        if tp_price is not None:
            tp_price = round(tp_price, 2 if "BTC" in symbol else 4)

        # --- KIEKIO SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * DEFAULT_LEVERAGE
        raw_crypto_amount = total_value / entry_price
        
        final_amount = int(round(raw_crypto_amount, 0))
        if final_amount <= 0:
            final_amount = 1

        # --- SVERTO NUSTATYMAS FONE ---
        try:
            private_exchange.set_leverage(int(DEFAULT_LEVERAGE), symbol, {'openType': 1, 'positionType': 2})
        except Exception as lev_err:
            print(f"Sverto žinutė fone: {lev_err}")

        # Suformuojame bazinius užsakymo parametrus
        order_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(DEFAULT_LEVERAGE),
            'timeInForce': 'PostOnly'
        }

        # Svarbu: Perduodame SL ir TP tiesiai į pirminį užsakymą pagal CCXT standartą
        if sl_price:
            order_params['stopLossPrice'] = sl_price
        if tp_price:
            order_params['takeProfitPrice'] = tp_price

        # --- VIENAS BENDRAS ORDERIS (ĮĖJIMAS + SL + TP KARTU) ---
        entry_order = private_exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=final_amount,
            price=entry_price,
            params=order_params
        )

        print(f"SĖKMĖ: SHORT LIMIT pastatytas monetai {symbol}! Kiekis: {final_amount} | SL: {sl_price} | TP: {tp_price}")
        return {
            "status": "success",
            "symbol": symbol,
            "order_id": entry_order['id']
        }, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
