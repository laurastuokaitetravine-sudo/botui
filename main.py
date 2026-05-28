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
DEFAULT_LEVERAGE = 1
MARGIN_USDT = 5.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (TIK SHORT REŽIMAS)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # --- JSON NUSKAITYMAS ---
        data = request.get_json(force=True, silent=True)
        if not data:
            data = json.loads(request.data.decode('utf-8').strip())

        # --- SAUGUMAS ---
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        # --- TIK SHORT ---
        if str(data.get('action', '')).lower() != 'short':
            return "Ignored (SHORT only mode)", 200

        # --- TICKER ---
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = tv_ticker.upper().replace(".P", "").replace("_", "").replace("-", "")
        if clean.endswith("USDT"):
            clean = clean[:-4]

        symbol = f"{clean}/USDT:USDT"
        print(f"TV: {tv_ticker} -> CCXT: {symbol}")

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found"}, 400

        market = markets[symbol]

        # --- ENTRY KAINA ---
        order_book = exchange.fetch_order_book(symbol, limit=5)
        entry_price = float(order_book['asks'][0][0])

        # --- LEVERAGE ---
        exchange.set_leverage(DEFAULT_LEVERAGE, symbol, {
            'openType': 1,
            'positionType': 2
        })

        # --- SL/TP iš TradingView ---
        def safe_float(v):
            return float(v) if v not in [None, "", "null", "nan", "na"] else None

        sl_price  = safe_float(data.get('sl_price'))
        tp1_price = safe_float(data.get('tp1_price'))
        tp2_price = safe_float(data.get('tp2_price'))
        tp3_price = safe_float(data.get('tp3_price'))

        # --- SL PRIVALO ATEITI IŠ TV ---
        if sl_price is None:
            return {"error": "TradingView did not send SL (plot_0)"}, 400

        # --- TP fallback tik jei TV nesiunčia ---
        if tp1_price is None: tp1_price = entry_price * 0.990
        if tp2_price is None: tp2_price = entry_price * 0.980
        if tp3_price is None: tp3_price = entry_price * 0.970

        # --- PRECISION ---
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price    = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price   = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price   = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price   = float(exchange.price_to_precision(symbol, tp3_price))

        # --- KIEKIS ---
        total_value = MARGIN_USDT * DEFAULT_LEVERAGE
        raw_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_amount / contract_size

        amount = float(exchange.amount_to_precision(symbol, contracts_qty))
        if amount < float(market['limits']['amount']['min']):
            amount = float(market['limits']['amount']['min'])

        # --- ATIDARYMAS ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': DEFAULT_LEVERAGE,
            'stopLossPrice': sl_price,
            'slPrice': sl_price,
            'priceWay': 1
        }

        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=amount,
            price=None,
            params=open_params
        )

        print(f"SHORT OPEN | {symbol} | Qty={amount} | SL={sl_price}")

        # --- TP ORDERIAI ---
        amt_tp1 = float(exchange.amount_to_precision(symbol, amount * 0.70))
        amt_tp2 = float(exchange.amount_to_precision(symbol, amount * 0.20))
        amt_tp3 = float(exchange.amount_to_precision(symbol, amount * 0.10))

        leftover = float(exchange.amount_to_precision(symbol, amount - (amt_tp1 + amt_tp2)))
        if leftover > 0:
            amt_tp3 = leftover

        tp_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': DEFAULT_LEVERAGE,
            'priceWay': 1
        }

        if amt_tp1 > 0:
            exchange.create_order(symbol, 'limit', 'buy', amt_tp1, tp1_price, tp_params)
            print(f"TP1 SET | {tp1_price} | {amt_tp1}")

        if amt_tp2 > 0:
            exchange.create_order(symbol, 'limit', 'buy', amt_tp2, tp2_price, tp_params)
            print(f"TP2 SET | {tp2_price} | {amt_tp2}")

        if amt_tp3 > 0:
            exchange.create_order(symbol, 'limit', 'buy', amt_tp3, tp3_price, tp_params)
            print(f"TP3 SET | {tp3_price} | {amt_tp3}")

        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
