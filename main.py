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
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.0 

@app.route('/')
def home():
    return "SHORT BOTAS VEIKIA (DIRECT FUTURES API)!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    raw_body = request.get_data(as_text=True)
    try:
        data = json.loads(raw_body)
        print(f"--- GAUTAS SIGNALAS ---")
        print(f"Raw data: {data}")
    except:
        return "Invalid JSON", 400

    if data.get('passphrase') != MY_PASSWORD or data.get('action') != 'short':
        return "Unauthorized", 403

    try:
        symbol = 'BTC_USDT'
        sl_price_raw = float(data.get('sl'))

        # 1. TEISINGA: Gauname fairPrice iš FUTURES API
        response = exchange.contractPublicGetTicker({'symbol': symbol})
        entry_price = float(response['fairPrice'])

        print(f"Entry kaina (fairPrice): {entry_price}")

        # 2. Skaičiavimai
        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)
        amount = round((MARGIN_USDT * LEVERAGE) / entry_price, 4)

        # 3. ATIDAROME SHORT
        print(f"Vykdomas SHORT atidarymas: {amount} BTC...")
        order_open = exchange.contractPrivatePostOrder({
            'symbol': symbol,
            'side': 2,       # 2 = Open Short
            'orderType': 2,  # 2 = Market
            'vol': amount,
            'openType': 1,   # 1 = Isolated
            'leverage': LEVERAGE
        })
        print(f"Sėkmė! Atidaryta.")

        time.sleep(1.5)

        # 4. STOP LOSS
        print(f"Nustatau SL ties {round(sl_price_raw, 1)}")
        exchange.contractPrivatePostPlanOrder({
            'symbol': symbol,
            'side': 4,            # 4 = Close Short
            'vol': amount,
            'triggerPrice': round(sl_price_raw, 1),
            'triggerType': 1,
            'executeCycle': 1,
            'orderType': 2,
            'trend': 1            # 1 = Up
        })

        # 5. TAKE PROFIT
        print(f"Nustatau TP ties {round(tp_price, 1)}")
        exchange.contractPrivatePostPlanOrder({
            'symbol': symbol,
            'side': 4,
            'vol': amount,
            'triggerPrice': round(tp_price, 1),
            'triggerType': 1,
            'executeCycle': 1,
            'orderType': 2,
            'trend': 2            # 2 = Down
        })

        return {"status": "success"}, 200

    except Exception as e:
        print("--- KRITINĖ KLAIDA ---")
        print(traceback.format_exc())
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
