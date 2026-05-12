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
MARGIN_USDT = 9.0 

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

    if data.get("action") != "short":
        return "Klaida: šis botas priima tik SHORT signalus", 400

    try:
        symbol = 'BTC/USDT'
        sl_price_raw = float(data.get('sl'))

        # 4. PATAISYTA: Gauname fairPrice iš data objekto
        response = exchange.contractPublicGetTicker({'symbol': 'BTC_USDT'})
        if 'data' not in response or 'fairPrice' not in response['data']:
            return "Klaida: negauta kaina is MEXC", 400

        entry_price = float(response['data']['fairPrice'])
        print(f"Naudojama kaina: {entry_price}")

        if sl_price_raw <= entry_price:
            return f"Klaida: SL turi buti virs kainos ({entry_price})!", 400

        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except: pass

        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        sl_price_str = exchange.price_to_precision(symbol, sl_price_raw)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)

        amount_f = float(amount_str)
        sl_f = float(sl_price_str)
        tp_f = float(tp_price_str)

        # 9. ATIDARYMAS
        print(f"Atidarau SHORT: {amount_f} BTC...")
        exchange.create_order(symbol, 'market', 'sell', amount_f, entry_price, {
            'openType': 1,
            'positionMode': 2
        })
        
        time.sleep(2)

        # 10. STOP LOSS
        print(f"Nustatau SL: {sl_f}")
        exchange.create_order(symbol, 'trigger', 'buy', amount_f, None, {
            'triggerPrice': sl_f,
            'triggerDirection': 1, 
            'reduceOnly': True,
            'positionMode': 2
        })

        # 11. TAKE PROFIT
        print(f"Nustatau TP: {tp_f}")
        exchange.create_order(symbol, 'trigger', 'buy', amount_f, tp_f, {
            'triggerPrice': tp_f,
            'triggerDirection': 2, 
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
