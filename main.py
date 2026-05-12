import os
import json
from flask import Flask, request
import ccxt
import time
import traceback

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92a7d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.0 

@app.route('/')
def home():
    return "SHORT BOTAS VEIKIA (FUTURES API)!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    raw_body = request.get_data(as_text=True)
    try:
        data = json.loads(raw_body)
        print(f"--- GAUTAS SIGNALAS ---")
        print(data)
    except:
        return "Invalid JSON", 400

    if data.get('passphrase') != MY_PASSWORD or data.get('action') != 'short':
        return "Unauthorized", 403

    try:
        symbol = 'BTC_USDT'
        sl_price_raw = float(data.get('sl'))

        # 1. GAUNAME KAINĄ (Pataisyta struktūra)
        ticker_response = exchange.contractPublicGetTicker({'symbol': symbol})
        
        # MEXC API duomenys visada sėdi po 'data' raktu
        if 'data' in ticker_response:
            entry_price = float(ticker_response['data']['fairPrice'])
        else:
            entry_price = float(ticker_response['fairPrice'])

        print(f"Entry kaina: {entry_price}")

        # 2. SKAIČIAVIMAI
        risk_distance = sl_price_raw - entry_price
        tp_price = round(entry_price - (risk_distance * 2), 1)
        amount = round((MARGIN_USDT * LEVERAGE) / entry_price, 4)

        # 3. SHORT ATIDARYMAS
        print(f"Atidarau SHORT: {amount} BTC...")
        order_open = exchange.contractPrivatePostOrderSubmit({
            'symbol': symbol,
            'side': 2,        # 2 = Open Short
            'vol': amount,
            'leverage': LEVERAGE,
            'openType': 1,    # 1 = Isolated
            'orderType': 2    # 2 = Market
        })
        print(f"SHORT užsakymas išsiųstas: {order_open}")

        time.sleep(2)

        # 4. STOP LOSS
        print(f"Nustatau SL ties {sl_price_raw}")
        exchange.contractPrivatePostPlanorderSubmit({
            'symbol': symbol,
            'side': 4,               # 4 = Close Short
            'vol': amount,
            'triggerPrice': sl_price_raw,
            'triggerType': 1,        # 1 = Last Price
            'executeCycle': 1,       # 1 = Lasting
            'orderType': 2,          # 2 = Market
            'trend': 1               # 1 = Up
        })

        # 5. TAKE PROFIT
        print(f"Nustatau TP ties {tp_price}")
        exchange.contractPrivatePostPlanorderSubmit({
            'symbol': symbol,
            'side': 4,               # 4 = Close Short
            'vol': amount,
            'triggerPrice': tp_price,
            'triggerType': 1,        # 1 = Last Price
            'executeCycle': 1,       # 1 = Lasting
            'orderType': 2,          # 2 = Market
            'trend': 2               # 2 = Down
        })

        return {"status": "success"}, 200

    except Exception as e:
        print("--- KRITINĖ KLAIDA ---")
        print(traceback.format_exc())
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
