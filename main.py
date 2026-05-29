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
        'defaultType': 'swap',                 # Naudojame Futures (USDT-M)
        'createMarketBuyOrderRequiresPrice': False,
        'defaultMarket': 'swap',               # Užtikrina, kad CCXT nenaudos spot API
        'defaultType': 'swap',
    }
})


MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 5
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

        # --- DINAMINIS SVERTO APRIBOROJIMAS ---
        max_exchange_leverage = 20  
        if 'limits' in market and 'leverage' in market['limits']:
            max_exchange_leverage = float(market['limits']['leverage'].get('max', 20))
        elif 'info' in market and 'maxLeverage' in market['info']:
            max_exchange_leverage = float(market['info']['maxLeverage'])

        active_leverage = int(min(DEFAULT_LEVERAGE, max_exchange_leverage))
        print(f"Pasirinktas svertas {symbol}: {active_leverage}x (Maksimalus biržos limitas: {max_exchange_leverage}x)")

        # --- ENTRY KAINA ---
        order_book = exchange.fetch_order_book(symbol, limit=5)
        entry_price = float(order_book['bids'][0][0])

        # --- LEVERAGE NUSTATYMAS ---
        try:
            exchange.set_leverage(active_leverage, symbol, {
                'openType': 1,   # 1 = Isolated
                'positionType': 2 # 2 = Short
            })
        except Exception as le:
            print(f"Leverage nustatymo įspėjimas: {le}")

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

        # --- KIEKIS (Naudoja aktyvų svertą) ---
        total_value = MARGIN_USDT * active_leverage
        raw_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_amount / contract_size

        # Paverčiame bendrą kiekį į sveikąjį skaičių
        amount = int(float(exchange.amount_to_precision(symbol, contracts_qty)))
        min_amount = int(float(market['limits']['amount']['min'])) if market['limits']['amount']['min'] is not None else 1
        if amount < min_amount:
            amount = min_amount

        # --- ATIDARYMAS ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': active_leverage,  
            'stopLossPrice': sl_price,     
            'slPrice': sl_price,
            'priceWay': 1
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=amount,
            price=entry_price,
            params=open_params
        )

        print(f"SHORT LIMIT OPEN | {symbol} | Price={entry_price} | Qty={amount} | SL={sl_price}")

        # --- SUTVARKYTI TP1, TP2, TP3 TRIGGER ORDERIAI ---
        # 1. Preliminarus padalinimas dalimis
        amt_tp1 = int(amount * 0.70)
        amt_tp2 = int(amount * 0.20)
        amt_tp3 = amount - (amt_tp1 + amt_tp2)

        # 2. MECHANIZMAS NUO 'QUANTITY ERROR' KLAIDOS:
        # Užtikriname, kad nei vienas TP užsakymas nebūtų mažesnis už biržos minimalų leistiną kontraktų kiekį
        if amt_tp1 < min_amount and amt_tp1 > 0: amt_tp1 = min_amount
        if amt_tp2 < min_amount and amt_tp2 > 0: amt_tp2 = min_amount
        if amt_tp3 < min_amount and amt_tp3 > 0: amt_tp3 = min_amount

        tp_trigger_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': active_leverage,
            'priceWay': 1,
            'orderType': 3,       # 3 = Sąlyginis/Trigger užsakymas MEXC API
            'openClose': 'CLOSE'  # Nurodo biržai tik uždaryti SHORT poziciją
        }

        # Išsiunčiame TP tik tada, kai apskaičiuotas kiekis yra saugus ir didesnis už 0
        if amt_tp1 > 0:
            p1 = tp_trigger_params.copy()
            p1['triggerPrice'] = tp1_price
            exchange.create_order(symbol, 'market', 'buy', amt_tp1, None, p1)
            print(f"TP1 TRIGGER SET | {tp1_price} | Qty={amt_tp1} (70%)")

        if amt_tp2 > 0:
            p2 = tp_trigger_params.copy()
            p2['triggerPrice'] = tp2_price
            exchange.create_order(symbol, 'market', 'buy', amt_tp2, None, p2)
            print(f"TP2 TRIGGER SET | {tp2_price} | Qty={amt_tp2} (20%)")

        if amt_tp3 > 0:
            p3 = tp_trigger_params.copy()
            p3['triggerPrice'] = tp3_price
            exchange.create_order(symbol, 'market', 'buy', amt_tp3, None, p3)
            print(f"TP3 TRIGGER SET | {tp3_price} | Qty={amt_tp3} (10%)")

        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
