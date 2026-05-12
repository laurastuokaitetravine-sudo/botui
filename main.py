import os
import json
from flask import Flask, request
import ccxt
import time

app = Flask(__name__)

exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.0 # Šiek tiek sumažinau saugumui

@app.route('/')
def home():
    return "SHORT BOTAS VEIKIA!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        print(f"Gautas signalas: {data}")
    except:
        return "Invalid JSON", 400

    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        sl_price_raw = float(data.get('sl'))

        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        if sl_price_raw <= entry_price:
            return f"Klaida: SL turi buti auksciau kainos!", 400

        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except: pass

        # Skaičiuojame kiekį
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        sl_price_str = exchange.price_to_precision(symbol, sl_price_raw)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)
        
        # 3. ATIDARYMAS
        print(f"Atidarau SHORT: {amount_str} BTC...")
        exchange.create_order(symbol, 'market', 'sell', float(amount_str), None, {
            'openType': 1,
            'positionMode': 2
        })
        
        time.sleep(1.5) 

        # 4. STOP LOSS
        print(f"SL: {sl_price_str}")
        exchange.create_order(symbol, 'stop_market', 'buy', float(amount_str), None, {
            'stopPrice': float(sl_price_str),
            'reduceOnly': True,
            'positionMode': 2
        })

        # 5. TAKE PROFIT
        print(f"TP: {tp_price_str}")
        exchange.create_order(symbol, 'limit', 'buy', float(amount_str), float(tp_price_str), {
            'reduceOnly': True,
            'positionMode': 2
        })

        return {"status": "success"}, 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
