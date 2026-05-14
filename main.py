import os
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 10.0   # <<< ČIA TAVO 10 USDT
SYMBOL = 'BTC/USDT:USDT'

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        action = str(data.get('action')).lower()
        if action not in ['long', 'short']:
            return "Ignored", 200

        # Anti-duplicate
        if os.path.exists("/tmp/last_order.lock"):
            return "Duplicate", 200
        open("/tmp/last_order.lock", "w").write("1")

        # Market data
        markets = exchange.load_markets()
        market = markets[SYMBOL]
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])

        # --- TEISINGAS MINIMALUS ORDERIS ---
        min_cost = float(market['limits']['cost']['min'])   # minimalus USDT dydis
        min_qty = min_cost / entry_price                    # konvertuojam į BTC

        # --- TAVO ORDERIO DYDIS ---
        raw_qty = MARGIN_USDT / entry_price                 # 10 USDT / BTC kaina
        final_qty = max(raw_qty, min_qty)                   # turi būti >= minCost
        amount = float(exchange.amount_to_precision(SYMBOL, final_qty))

        # Leverage
        try:
            exchange.set_leverage(LEVERAGE, SYMBOL)
        except:
            pass

        # Orderio kryptis
        side = 'buy' if action == 'long' else 'sell'
        pos_side = 'LONG' if action == 'long' else 'SHORT'

        # Orderis
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=amount,
            params={
                'positionSide': pos_side,
                'leverage': LEVERAGE
            }
        )

        os.remove("/tmp/last_order.lock")

        print(f"{action.upper()} atidarytas: {order['id']}")
        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        try:
            os.remove("/tmp/last_order.lock")
        except:
            pass
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
