import os
import json
from flask import Flask, request
import ccxt
import time

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.5 

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

        # 0. Gauname dabartinę kainą
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        if sl_price_raw <= entry_price:
            return f"Klaida: SL ({sl_price_raw}) turi buti AUKSCIAU kainos ({entry_price})!", 400

        # Skaičiuojame TP (2:1 santykis)
        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        # 1. Sverto nustatymas (PositionMode 2 = Short)
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except:
            pass

        # 2. Tikslumo nustatymai (MEXC reikalauja specifinio apvalinimo)
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)
        sl_price_str = exchange.price_to_precision(symbol, sl_price_raw)
        
        # 3. ATIDAROME SHORT POZICIJĄ
        print(f"Atidarau SHORT užsakymą: {amount_str} BTC...")
        # Naudojame float() konvertavimą, kad išvengtume "invalid type" klaidų
        exchange.create_order(symbol, 'market', 'sell', float(amount_str), float(entry_price), {
            'openType': 1,
            'positionMode': 2
        })
        
        time.sleep(1.5) 

        # 4. STOP LOSS (uždarymas su 'buy')
        print(f"Nustatau SL ties {sl_price_str}")
        exchange.create_order(symbol, 'stop_market', 'buy', float(amount_str), float(sl_price_str), {
            'stopPrice': float(sl_price_str), 
            'reduceOnly': True,
            'positionMode': 2
        })

        # 5. TAKE PROFIT (uždarymas su 'buy' limit)
        print(f"Nustatau TP ties {tp_price_str}")
        exchange.create_order(symbol, 'limit', 'buy', float(amount_str), float(tp_price_str), {
            'reduceOnly': True,
            'positionMode': 2
        })

        return {"status": "success", "msg": "Pozicija, SL ir TP sukurti sėkmingai"}, 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
