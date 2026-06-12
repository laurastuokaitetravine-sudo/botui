import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- SAUGI KEITYKLOS KONFIGŪRACIJA ---
exchange_config = {
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {
        'defaultType': 'swap'
    },
    'timeout': 30000,
    'enableRateLimit': True,
    'adjustForTimeDifference': True 
}

if os.getenv('PROXY_URL'):
    exchange_config['proxies'] = {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }

exchange = ccxt.mexc(exchange_config)

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 50.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (FUTURES ONLY - FIXED NO-SPOT)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        
        if not data:
            try:
                raw_data = request.data.decode('utf-8').strip()
                data = json.loads(raw_data)
            except Exception as json_err:
                return {"error": "Invalid JSON format"}, 400

        if not data or data.get('passphrase') != MY_PASSWORD:
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

        # --- VIEŠAS KAINOS GAVIMAS (BE RAKTU IR BE PARAŠŲ KLAIDŲ) ---
        ticker = None
        for attempt in range(3):
            try:
                # Naudojame viešą užklausą, kurios MEXC niekada neatmeta dėl raktų teisių
                ticker = exchange.fetch_ticker(symbol)
                if ticker:
                    break
            except Exception as ne:
                print(f"Klaida gaunant kainą (Bandymas {attempt + 1}/3): {ne}")
                time.sleep(2)

        if not ticker:
            return {"error": "Nepavyko gauti kainos iš MEXC. Patikrinkite Proxy."}, 400

        entry_price = float(ticker['ask'])

        # --- KAINŲ SKAITYMAS IŠ TRADINGVIEW ---
        try:
            sl_price = float(data.get('sl_price'))
            tp_raw = data.get('tp_price_1')
            tp_price = float(tp_raw) if tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.50
        except (TypeError, ValueError):
            return {"error": "Blogas kainų formatas iš TradingView"}, 400

        # Kadangi išjungėme load_markets, suapvaliname kainas programiškai (4 skaičiai po kablelio dažniausiai tinka viskam)
        entry_price = round(entry_price, 4)
        sl_price = round(sl_price, 4)
        tp_price = round(tp_price, 4)

        # --- KIEKIO SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * DEFAULT_LEVERAGE
        raw_crypto_amount = total_value / entry_price
        
        # Rankiniu būdu suapvaliname kontraktų kiekį iki sveikojo skaičiaus (Mexc Futures dažniausiai priima sveikus skaičius)
        final_amount = round(raw_crypto_amount, 0)
        if final_amount < 1:
            final_amount = 1.0

        # Nustatome svertą biržoje (Ši užklausa naudoja Futures teises, todėl suveiks)
        try:
            exchange.set_leverage(int(DEFAULT_LEVERAGE), symbol, {'openType': 1, 'positionType': 2})
        except Exception as lev_err:
            print(f"Sverto nustatymo pranešimas (gali būti jau nustatytas): {lev_err}")

        # --- LIMIT ORDERIO PATEIKIMAS ---
        params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(DEFAULT_LEVERAGE),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price,
            'timeInForce': 'PostOnly'  
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',       
            side='sell',
            amount=final_amount,
            price=entry_price,  
            params=params
        )

        print(f"SĖKMĖ: SHORT LIMIT pastatytas monetai {symbol}! Kiekis: {final_amount} | Kaina: {entry_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
