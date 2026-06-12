import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 50.0

# ============================================================
# VIEŠAS KLIENTAS KAINAI (SU PROXY, BE API KEY, TIK FUTURES)
# ============================================================

public_exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
        'types': {'swap': True},
        'fetchMarkets': ['swap']
    },
    'urls': {
        'api': {
            'public': 'https://contract.mexc.com',
            'private': 'https://contract.mexc.com'
        }
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})

# ============================================================
# PRIVATUS KLIENTAS ORDERIAMS (SU API KEY + PROXY, TIK FUTURES)
# ============================================================

private_exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'swap',
        'types': {'swap': True},
        'fetchMarkets': ['swap']
    },
    'urls': {
        'api': {
            'public': 'https://contract.mexc.com',
            'private': 'https://contract.mexc.com'
        }
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})

@app.route('/')
def home():
    return "BOTAS ONLINE (FUTURES ONLY, FIXED)", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return {"error": "Invalid JSON"}, 400

        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        if str(data.get('action', '')).lower() != 'short':
            return "Ignored (only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean == "PEPE":
            clean = "10000PEPE"

        symbol = f"{clean}/USDT:USDT"

        # ============================================================
        # GAUNAME KAINĄ IŠ VIEŠO FUTURES KLIENTO (NEBEKVIEČIA SPOT)
        # ============================================================

        ticker = None
        for attempt in range(3):
            try:
                ticker = public_exchange.fetch_ticker(symbol)
                break
            except Exception as e:
                print(f"Klaida gaunant kainą (bandymas {attempt+1}/3): {e}")
                time.sleep(1)

        if not ticker:
            return {"error": "Nepavyko gauti kainos"}, 400

        entry_price = float(ticker['ask'])

        sl_price = float(data.get('sl_price'))
        tp_raw = data.get('tp_price_1')
        tp_price = float(tp_raw) if tp_raw else entry_price * 0.5

        entry_price = round(entry_price, 4)
        sl_price = round(sl_price, 4)
        tp_price = round(tp_price, 4)

        # ============================================================
        # KIEKIO SKAIČIAVIMAS
        # ============================================================

        total_value = MARGIN_USDT * DEFAULT_LEVERAGE
        amount = round(total_value / entry_price, 0)
        if amount < 1:
            amount = 1.0

        # ============================================================
        # LEVERAGE
        # ============================================================

        try:
            private_exchange.set_leverage(
                int(DEFAULT_LEVERAGE),
                symbol,
                {'openType': 1, 'positionType': 2}
            )
        except Exception as e:
            print(f"Sverto klaida: {e}")

        # ============================================================
        # LIMIT SHORT ORDERIS
        # ============================================================

        entry_order = private_exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=amount,
            price=entry_price,
            params={
                'posSide': 'SHORT',
                'openType': 1,
                'timeInForce': 'PostOnly'
            }
        )

        # ============================================================
        # STOP LOSS (trigger market)
        # ============================================================

        sl_order = private_exchange.create_order(
            symbol=symbol,
            type='stop_market',
            side='buy',
            amount=amount,
            params={
                'stopPrice': sl_price,
                'triggerPrice': sl_price,
                'posSide': 'SHORT',
                'reduceOnly': True
            }
        )

        # ============================================================
        # TAKE PROFIT (trigger market)
        # ============================================================

        tp_order = private_exchange.create_order(
            symbol=symbol,
            type='take_profit_market',
            side='buy',
            amount=amount,
            params={
                'stopPrice': tp_price,
                'triggerPrice': tp_price,
                'posSide': 'SHORT',
                'reduceOnly': True
            }
        )

        return {
            "status": "success",
            "symbol": symbol,
            "entry_id": entry_order['id'],
            "sl_id": sl_order['id'],
            "tp_id": tp_order['id']
        }, 200

    except Exception as e:
        print(traceback.format_exc())
        return {"error": str(e)}, 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
