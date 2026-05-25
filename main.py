import os
import json
import traceback
from flask import Flask, request
import ccxt

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
MARGIN_USDT = 5.0 

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

        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        action = str(data.get('action', '')).lower()
        if action not in ['long', 'short']:
            return f"Ignored (Invalid action: {action})", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker in request"}, 400

        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]

        # --- TIKSLUS TICKERIS BE NetworkError ---
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
        
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)

        try:
            exchange.set_leverage(int(final_leverage), symbol, {
                'openType': 1,
                'positionType': pos_mode
            })
        except Exception:
            pass

        raw_sl = data.get('sl_price')
        sl_price = None
        
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        # --- TAVO ORIGINALI TP/SL LOGIKA (NEKEISTA) ---
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

        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        params = {
            'posSide': pos_side,
            'openType': 1,
            'leverage': int(final_leverage),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price,
            'tpPrice': tp_price,
            'slPrice': sl_price,
            'priceWay': 1
        }

        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=side,
            amount=amount,
            price=None,
            params=params
        )

        print(f"{pos_side} MARKET sandoris įvykdytas | {symbol} | SL={sl_price} | TP={tp_price}")

        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
