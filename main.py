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

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        print(f"Gautas signalas: {data}")
    except Exception as e:
        return "Invalid JSON", 400

    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') # 'buy' arba 'short'
        sl_price = float(data.get('sl'))

        # Gauname dabartinę kainą
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        risk_distance = abs(entry_price - sl_price)
        
        # Nustatymai pagal kryptį
        if action == 'short' or action == 'sell':
            tp_price = entry_price - (risk_distance * 2)
            side = 'sell'
            close_side = 'buy'
            pos_mode = 2  # Short pozicija
        else:
            tp_price = entry_price + (risk_distance * 2)
            side = 'buy'
            close_side = 'sell'
            pos_mode = 1  # Long pozicija

        # 1. Nustatom svertą (Isolated)
        try:
            # openType: 1 - Isolated, 2 - Cross
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': pos_mode})
        except Exception as e:
            print(f"Sverto nustatymo klaida (galbūt jau nustatytas): {e}")

        # 2. Skaičiuojame kiekį
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)
        sl_price_str = exchange.price_to_precision(symbol, sl_price)
        
        # 3. ATIDAROME POZICIJĄ
        print(f"Vykdomas {side} užsakymas: {amount_str} BTC...")
        order = exchange.create_order(symbol, 'market', side, amount_str, params={
            'openType': 1, # Open
            'positionMode': pos_mode
        })
        
        # Šiek tiek palaukiame, kol birža užregistruos poziciją prieš siunčiant SL/TP
        time.sleep(0.5)

        # 4. STOP LOSS (MEXC naudoja specifinius parametrus stop_market)
        print(f"Nustatomas SL: {sl_price_str}")
        exchange.create_order(symbol, 'stop_market', close_side, amount_str, None, {
            'stopPrice': sl_price_str, 
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        # 5. TAKE PROFIT
        print(f"Nustatomas TP: {tp_price_str}")
        exchange.create_order(symbol, 'limit', close_side, amount_str, tp_price_str, {
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        return {"status": "success", "message": f"{action} pozicija atidaryta"}, 200

    except Exception as e:
        error_msg = f"Klaida vykdant sandorį: {str(e)}"
        print(error_msg)
        return error_msg, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
