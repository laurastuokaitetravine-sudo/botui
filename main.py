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
MARGIN_USDT = 9.0  # Rizikuojama suma

@app.route('/')
def home():
    return "SHORT BOTAS VEIKIA!", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. JSON gavimas
    try:
        data = request.get_json(force=True)
        print(f"Gautas signalas: {data}")
    except Exception as e:
        print(f"JSON klaida: {e}")
        return "Invalid JSON", 400

    # 2. Slaptažodis
    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    # 3. Tik SHORT signalai
    if data.get("action") != "short":
        return "Klaida: šis botas priima tik SHORT signalus", 400

    try:
        symbol = 'BTC/USDT'
        sl_price_raw = float(data.get('sl'))

        # 4. Futures markPrice gavimas
        ticker = exchange.fapiPublicGetTicker({'symbol': 'BTC_USDT'})

        if 'markPrice' not in ticker:
            print(f"Ticker info be markPrice: {ticker}")
            return "Klaida: negauta markPrice iš MEXC futures", 400

        entry_price = float(ticker['markPrice'])
        print(f"Naudojama entry kaina (markPrice): {entry_price}")

        if entry_price <= 0:
            return "Klaida: negauta teisinga BTC kaina", 400

        # 5. SL logika – SHORT → SL turi būti aukščiau kainos
        if sl_price_raw <= entry_price:
            return "Klaida: SL turi būti aukščiau dabartinės kainos SHORT pozicijai!", 400

        # 6. TP skaičiavimas (RR 1:2)
        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        # 7. Leverage nustatymas
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except Exception as e:
            print(f"Leverage klaida (tęsiam be jos): {e}")

        # 8. Kiekio skaičiavimas
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        if amount <= 0:
            print(f"Blogas amount: {amount}, entry_price: {entry_price}")
            return "Klaida: amount <= 0", 400

        amount_str = exchange.amount_to_precision(symbol, amount)
        sl_price_str = exchange.price_to_precision(symbol, sl_price_raw)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)

        amount_f = float(amount_str)
        sl_f = float(sl_price_str)
        tp_f = float(tp_price_str)

        print(f"Skaičiuojamas amount: {amount_f}, SL: {sl_f}, TP: {tp_f}")

        # 9. SHORT atidarymas – MARKET SELL
        print(f"Atidarau SHORT užsakymą: {amount_str} BTC...")
        order_open = exchange.create_order(
            symbol,
            'market',
            'sell',
            amount_f,
            None,
            {
                'openType': 1,
                'positionMode': 2
            }
        )
        print(f"SHORT atidarytas: {order_open}")

        time.sleep(1.5)

        # 10. STOP LOSS – MEXC trigger order (SHORT → BUY kai kaina KYLA)
        print(f"Nustatau SL (trigger): {sl_price_str}")
        sl_order = exchange.create_order(
            symbol,
            'trigger',
            'buy',
            amount_f,
            None,
            {
                'triggerPrice': sl_f,
                'triggerDirection': 1,  # 1 = trigger when price rises
                'reduceOnly': True,
                'positionMode': 2
            }
        )
        print(f"SL orderis: {sl_order}")

        # 11. TAKE PROFIT – MEXC trigger limit (SHORT → BUY kai kaina KRENTA)
        print(f"Nustatau TP (trigger limit): {tp_price_str}")
        tp_order = exchange.create_order(
            symbol,
            'trigger',
            'buy',
            amount_f,
            tp_f,
            {
                'triggerPrice': tp_f,
                'triggerDirection': 2,  # 2 = trigger when price falls
                'reduceOnly': True,
                'positionMode': 2
            }
        )
        print(f"TP orderis: {tp_order}")

        return {"status": "success"}, 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
