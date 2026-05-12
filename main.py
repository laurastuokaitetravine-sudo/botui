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
MARGIN_USDT = 9.0  # Suma vienam sandoriui

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

    # 2. Saugumo patikra
    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    # 3. Krypties patikra (TradingView pranešime PRIVALO būti "action": "short")
    if data.get("action") != "short":
        return "Klaida: šis botas priima tik SHORT signalus", 400

    try:
        symbol = 'BTC/USDT'
        sl_price_raw = float(data.get('sl'))

        # 4. Gauname Mark Price (tiksliausia kaina Futures rinkoje)
        ticker = exchange.contractPublicGetTicker({'symbol': 'BTC_USDT'})
        if 'markPrice' not in ticker:
            return "Klaida: negauta kaina iš MEXC", 400

        entry_price = float(ticker['markPrice'])
        print(f"Naudojama entry kaina: {entry_price}")

        # 5. SHORT logika: SL privalo būti aukščiau kainos
        if sl_price_raw <= entry_price:
            return f"Klaida: SL ({sl_price_raw}) turi buti virs kainos ({entry_price})!", 400

        # 6. TP skaičiavimas (RR 1:2)
        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        # 7. Sverto nustatymas
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except:
            pass

        # 8. Kiekių ir tikslumo nustatymai
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        sl_price_str = exchange.price_to_precision(symbol, sl_price_raw)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)

        amount_f = float(amount_str)
        sl_f = float(sl_price_str)
        tp_f = float(tp_price_str)

        # 9. SHORT ATIDARYMAS (MARKET SELL)
        print(f"Atidarau SHORT: {amount_f} BTC...")
        # Svarbu: perduodame entry_price, kad MEXC negrąžintų Mandatory Parameter klaidos
        order_open = exchange.create_order(
            symbol, 'market', 'sell', amount_f, entry_price, 
            {
                'openType': 1,      # Isolated
                'positionMode': 2   # Short rėžimas
            }
        )
        print(f"Pozicija atidaryta: {order_open.get('id', 'OK')}")

        time.sleep(2) # Palaukiame, kol birža užfiksuos poziciją

        # 10. STOP LOSS (Trigger kai kaina kyla)
        print(f"Nustatau SL ties {sl_f}")
        exchange.create_order(
            symbol, 'trigger', 'buy', amount_f, None,
            {
                'triggerPrice': sl_f,
                'triggerDirection': 1, # Trigger kai kaina kyla (tiksliai SHORT'ui)
                'reduceOnly': True,
                'positionMode': 2
            }
        )

        # 11. TAKE PROFIT (Trigger kai kaina krenta)
        print(f"Nustatau TP ties {tp_f}")
        exchange.create_order(
            symbol, 'trigger', 'buy', amount_f, tp_f,
            {
                'triggerPrice': tp_f,
                'triggerDirection': 2, # Trigger kai kaina krenta
                'reduceOnly': True,
                'positionMode': 2
            }
        )

        return {"status": "success", "message": "SHORT pozicija, SL ir TP sukurti"}, 200

    except Exception as e:
        error_msg = f"Klaida vykdant operacijas: {str(e)}"
        print(error_msg)
        return error_msg, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
