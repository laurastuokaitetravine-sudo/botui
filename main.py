import os
import json
import traceback
from flask import Flask, request
import ccxt
import time

app = Flask(__name__)

# --- MEXC KONFIGŪRACIJA SU TEISINGAIS ENDPOINTAIS ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    },
    'urls': {
        'api': {
            'public': 'https://contract.mexc.com/api',
            'private': 'https://contract.mexc.com/api'
        }
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 10.0


@app.route('/')
def home():
    return "BOTAS ONLINE", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            raw_data = request.data.decode('utf-8').strip()
            data = json.loads(raw_data)

        # Slaptažodis
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        # LONG / SHORT
        action = str(data.get('action', '')).lower()
        if action not in ['long', 'short']:
            return "Ignored", 200

        # Ticker
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        symbol = f"{clean}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found"}, 400

        market = markets[symbol]

        # --- KAINA (naudojame naują endpointą) ---
        ticker = exchange.fetch_ticker(symbol)

        if action == 'long':
            entry_price = float(ticker['bid'])
            side = 'buy'
            pos_side = 'LONG'
            pos_mode = 1
        else:
            entry_price = float(ticker['ask'])
            side = 'sell'
            pos_side = 'SHORT'
            pos_mode = 2

        # --- LEVERAGE ---
        max_lev = DEFAULT_LEVERAGE
        if market.get('limits', {}).get('leverage', {}).get('max'):
            max_lev = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_lev)

        try:
            exchange.set_leverage(final_leverage, symbol, {
                'openType': 1,
                'positionType': pos_mode
            })
        except:
            pass

        # --- SL / TP ---
        raw_sl = data.get('sl_price')
        sl_price = None

        if raw_sl and str(raw_sl).lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except:
                sl_price = None

        if action == 'long':
            tp_price = entry_price * 1.008
            if not sl_price or sl_price >= entry_price:
                sl_price = entry_price * 0.99
            sl_price = entry_price - ((entry_price - sl_price) / 2)
        else:
            tp_price = entry_price * 0.992
            if not sl_price or sl_price <= entry_price:
                sl_price = entry_price * 1.01
            sl_price = entry_price + ((sl_price - entry_price) / 2)

        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # --- KONTRAKTŲ SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto = total_value / entry_price

        contract_size = float(market.get('contractSize', 1))
        contracts = raw_crypto / contract_size

        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts, min_contracts)

        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # --- MARKET ORDERIS ---
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=side,
            amount=amount,
            params={
                'posSide': pos_side,
                'openType': 1,
                'leverage': final_leverage
            }
        )

        print(f"Pozicija atidaryta: {symbol} | {pos_side} | amount={amount}")

        time.sleep(0.5)

        # --- SL TRIGGER ORDERIS ---
        sl_order = exchange.create_order(
            symbol=symbol,
            type='trigger',
            side='buy' if action == 'short' else 'sell',
            amount=amount,
            params={
                'triggerPrice': sl_price,
                'price': sl_price,
                'posSide': pos_side,
                'openType': 1
            }
        )

        # --- TP TRIGGER ORDERIS ---
        tp_order = exchange.create_order(
            symbol=symbol,
            type='trigger',
            side='buy' if action == 'short' else 'sell',
            amount=amount,
            params={
                'triggerPrice': tp_price,
                'price': tp_price,
                'posSide': pos_side,
                'openType': 1
            }
        )

        print(f"SL/TP uždėti: SL={sl_price} | TP={tp_price}")

        return {
            "status": "success",
            "symbol": symbol,
            "order_id": order['id'],
            "sl_id": sl_order['id'],
            "tp_id": tp_order['id']
        }, 200

    except Exception as e:
        print(traceback.format_exc())
        return {"error": str(e)}, 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
