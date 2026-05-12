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
    return "BOTAS GYVAS! Paruošta SHORT signalams.", 200

# Patikros nuoroda: https://onrender.com
@app.route('/test')
def test_connection():
    try:
        response = exchange.contractPublicGetTicker({'symbol': 'BTC_USDT'})
        price = response['data']['fairPrice']
        return f"Ryšys su MEXC geras. BTC kaina: {price}", 200
    except Exception as e:
        return f"MEXC ryšio klaida: {str(e)}", 500

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. SAUGUS DUOMENŲ GAVIMAS
    raw_body = request.get_data(as_text=True)
    print(f"--- GAUTAS SIGNALAS ---")
    print(f"Raw data: '{raw_body}'")

    try:
        data = json.loads(raw_body)
    except Exception as e:
        print(f"JSON klaida: {str(e)}")
        return "Invalid JSON format", 400

    # 2. PATIKROS
    if data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    if data.get('action') != 'short':
        return "Tik SHORT signalai priimami", 400

    try:
        symbol = 'BTC/USDT'
        sl_price_raw = float(data.get('sl'))

        # 3. KAINOS GAVIMAS
        response = exchange.contractPublicGetTicker({'symbol': 'BTC_USDT'})
        entry_price = float(response['data']['fairPrice'])
        print(f"Entry kaina: {entry_price}")

        if sl_price_raw <= entry_price:
            return f"Klaida: SL turi buti virs kainos!", 400

        # 4. KIEKIAI IR APVALINIMAS
        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        # Nustatom svertą
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except: pass

        amount_str = exchange.amount_to_precision(symbol, (MARGIN_USDT * LEVERAGE) / entry_price)
        sl_str = exchange.price_to_precision(symbol, sl_price_raw)
        tp_str = exchange.price_to_precision(symbol, tp_price)

        amount_f = float(amount_str)
        sl_f = float(sl_str)
        tp_f = float(tp_str)

        # 5. ATIDARYMAS (MARKET SELL)
        print(f"Vykdomas SHORT atidarymas: {amount_f} BTC...")
        order_open = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=amount_f,
            price=None,
            params={
                'openType': 1,      # Isolated
                'positionMode': 2   # Short
            }
        )
        print(f"Sėkmingai atidaryta: {order_open.get('id', 'OK')}")
        
        time.sleep(2)

        # 6. STOP LOSS (STOP MARKET)
        print(f"Nustatau SL: {sl_f}")
        exchange.create_order(
            symbol=symbol,
            type='stop_market',
            side='buy',
            amount=amount_f,
            price=None,
            params={
                'stopPrice': sl_f,
                'reduceOnly': True,
                'positionMode': 2
            }
        )

        # 7. TAKE PROFIT (LIMIT)
        print(f"Nustatau TP: {tp_f}")
        exchange.create_order(
            symbol=symbol,
            type='limit',
            side='buy',
            amount=amount_f,
            price=tp_f,
            params={
                'reduceOnly': True,
                'positionMode': 2
            }
        )

        print("SĖKMĖ: Visi užsakymai sukurti!")
        return {"status": "success"}, 200

    except Exception as e:
        print("--- KRITINĖ KLAIDA ---")
        # Išsamus klaidos išvedimas į logus
        print(traceback.format_exc())
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
