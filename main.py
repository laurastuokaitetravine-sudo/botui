import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- MEXC KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 10
MARGIN_USDT = 1.0


@app.route('/')
def home():
    return "BOTAS ONLINE", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            data = json.loads(request.data.decode('utf-8').strip())

        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        action = str(data.get('action', '')).lower()
        if action not in ['long', 'short']:
            return f"Ignored (Invalid action: {action})", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found"}, 400

        market = markets[symbol]

        # --- TICKER ---
        ticker = None
        for _ in range(3):
            try:
                ticker = exchange.fetch_ticker(symbol)
                break
            except ccxt.NetworkError:
                exchange.sleep(1000)

        if not ticker:
            return {"error": "MEXC network error"}, 502

        if action == 'long':
            entry_price = float(ticker['ask'])
            side = 'buy'
            close_side = 'sell'
            pos_side = 'LONG'
            pos_mode = 1
        else:
            entry_price = float(ticker['bid'])
            side = 'sell'
            close_side = 'buy'
            pos_side = 'SHORT'
            pos_mode = 2

        # --- LEVERAGE ---
        max_lev = DEFAULT_LEVERAGE
        if market['limits']['leverage']['max']:
            max_lev = min(DEFAULT_LEVERAGE, int(market['limits']['leverage']['max']))

        try:
            exchange.set_leverage(max_lev, symbol, {
                'openType': 1,
                'positionType': pos_mode
            })
        except Exception:
            pass

        # --- SL iš TV ---
        raw_sl = data.get('sl_price')
        sl_price = None
        if raw_sl and str(raw_sl).lower() not in ['nan', 'null', 'na', '']:
            try:
                sl_price = float(raw_sl)
            except:
                sl_price = None

        # --- TP/SL LOGIKA ---
        if action == 'long':
            tp_price = entry_price * 1.008
            if sl_price is None or sl_price >= entry_price:
                sl_price = entry_price * 0.99
            sl_price = entry_price - ((entry_price - sl_price) / 2)
        else:
            tp_price = entry_price * 0.992
            if sl_price is None or sl_price <= entry_price:
                sl_price = entry_price * 1.01
            sl_price = entry_price + ((sl_price - entry_price) / 2)

        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # --- AMOUNT ---
        total_value = MARGIN_USDT * max_lev
        raw_crypto = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        contracts = raw_crypto / contract_size
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts, min_contracts)
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # --- 1) MARKET ENTRY ---
        entry_order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=side,
            amount=amount,
            params={
                'posSide': pos_side,
                'openType': 1,
                'leverage': max_lev
            }
        )

        # --- 2) STOP LOSS TRIGGER ---
        sl_order = exchange.create_order(
            symbol=symbol,
            type='trigger',
            side=close_side,
            amount=amount,
            params={
                'triggerPrice': sl_price,
                'orderType': 1,
                'posSide': pos_side
            }
        )

        # --- 3) TAKE PROFIT TRIGGER ---
        tp_order = exchange.create_order(
            symbol=symbol,
            type='trigger',
            side=close_side,
            amount=amount,
            params={
                'triggerPrice': tp_price,
                'orderType': 1,
                'posSide': pos_side
            }
        )

        print(f"{pos_side} OPENED | {symbol} | ENTRY={entry_price} | SL={sl_price} | TP={tp_price}")

        return {
            "status": "success",
            "symbol": symbol,
            "entry_order": entry_order['id'],
            "sl_order": sl_order['id'],
            "tp_order": tp_order['id']
        }, 200

    except Exception as e:
        print(traceback.format_exc())
        return {"error": str(e)}, 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
