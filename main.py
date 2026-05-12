import os
import json
from flask import Flask, request
import ccxt
import time

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea', # Patikrinkite, ar čia teisingas Secret Key
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.5 

# PRIDĖTA: Pagrindinis puslapis, kad Render nemestų 404 klaidos
@app.route('/')
def home():
    return "Botas veikia!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Pataisyta: patikimesnis duomenų gavimas
        data = request.get_json(force=True)
        print(f"Gautas signalas: {data}")
    except Exception as e:
        print(f"JSON klaida: {e}")
        return "Invalid JSON", 400

    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') 
        sl_price = float(data.get('sl'))

        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        risk_distance = abs(entry_price - sl_price)
        
        if action == 'short' or action == 'sell':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side, pos_mode = 'sell', 'buy', 2
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side, pos_mode = 'buy', 'sell', 1

        # 1. Nustatom svertą
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # 2. Skaičiuojame kiekį
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)
        sl_price_str = exchange.price_to_precision(symbol, sl_price)
        
        # 3. ATIDAROME POZICIJĄ
        print(f"Atidarau {side}...")
        exchange.create_order(symbol, 'market', side, amount_str, params={
            'openType': 1,
            'positionMode': pos_mode
        })
        
        time.sleep(1) # Padidintas laukimas dėl MEXC API stabilumo

        # 4. STOP LOSS
        exchange.create_order(symbol, 'stop_market', close_side, amount_str, None, {
            'stopPrice': sl_price_str, 
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        # 5. TAKE PROFIT
        exchange.create_order(symbol, 'limit', close_side, amount_str, tp_price_str, {
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        return {"status": "success"}, 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    # PATAISYTA: Render reikalauja, kad port būtų skaitomas iš aplinkos kintamųjų
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
