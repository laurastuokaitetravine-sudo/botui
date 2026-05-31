import os
import json
import time
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
        'defaultMarket': 'swap',
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 1.0  # Jūsų bandomoji 1 USDT marža (25 USDT bendra pozicija)

@app.route('/')
def home():
    return "BOTAS ONLINE (SUTVARKYTAS MEXC TP/SL)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # --- JSON NUSKAITYMAS SU APSAUGA ---
        raw_data = request.data.decode('utf-8').strip()
        try:
            data = json.loads(raw_data)
        except Exception as je:
            print(f"KRITINĖ KLAIDA: Sugadintas JSON iš TradingView! Gautas tekstas: {raw_data}")
            return {"error": "Invalid JSON format"}, 400

        # --- SAUGUMAS ---
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        # --- TIK SHORT ---
        if str(data.get('action', '')).lower() != 'short':
            return "Ignored (SHORT only mode)", 200

        # --- TICKER (PAVADINIMŲ VALYMAS) ---
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = tv_ticker.upper().replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        
        # Saugiklis pigioms monetoms
        if clean == "PEPE":
            clean = "10000PEPE"
            
        symbol = f"{clean}/USDT:USDT"

        # --- ŽAIBIŠKAS RINKŲ UŽKROVIMAS ---
        markets = exchange.load_markets()
        if symbol not in markets:
            print(f"KLAIDA: Moneta {symbol} nerasta MEXC Futures sąraše.")
            return {"error": f"Symbol {symbol} not found"}, 400

        market = markets[symbol]

        # --- TINKLO KLAIDŲ APSAUGA KAINAI GAUTI ---
        ticker = None
        for _ in range(3):
            try:
                ticker = exchange.fetch_ticker(symbol)
                break
            except ccxt.NetworkError as ne:
                print(f"Laikinai nepavyko pasiekti MEXC tinklo, bandoma vėl... {ne}")
                time.sleep(1)
        
        if not ticker:
            return {"error": "Nepavyko gauti kainos iš MEXC dėl tinklo sutrikimų."}, 502

        # Paimame einamąją ASK (pardavimo) kainą iš fjučerių rinkos užsakymui
        entry_price = float(ticker['ask'])

        # --- SVERTO APRIBOROJIMAS ---
        max_exchange_leverage = DEFAULT_LEVERAGE  
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_exchange_leverage = float(market['limits']['leverage']['max'])

        active_leverage = int(min(DEFAULT_LEVERAGE, max_exchange_leverage))

        # Nustatome svertą biržoje
        try:
            exchange.set_leverage(active_leverage, symbol, {
                'openType': 1,   # Isolated
                'positionType': 2 # Short
            })
        except:
            pass

        # --- SL/TP PARSISIUNTIMAS IŠ INDIKATORIAUS ---
        def safe_float(v):
            return float(v) if v not in [None, "", "null", "nan", "na", "NaN"] else None

        sl_price  = safe_float(data.get('sl_price'))
        tp1_price = safe_float(data.get('tp1_price'))
        tp2_price = safe_float(data.get('tp2_price'))
        tp3_price = safe_float(data.get('tp3_price'))

        if sl_price is None:
            print("KLAIDA: TradingView neatsiuntė Stop Loss kainos!")
            return {"error": "TradingView did not send SL (plot_0)"}, 400

        # Fallback procentiniai tikslai, jei TV netyčia atsiųstų tuščius TP (NaN)
        if tp1_price is None: tp1_price = entry_price * 0.992
        if tp2_price is None: tp2_price = entry_price * 0.985
        if tp3_price is None: tp3_price = entry_price * 0.980

        # Suvaliname skaičius pagal biržos taisykles (Precision)
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price    = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price   = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price   = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price   = float(exchange.price_to_precision(symbol, tp3_price))

        # --- KIEKIO MATEMATIKA ---
        total_value = MARGIN_USDT * active_leverage
        raw_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_amount / contract_size

        # Priverstinai paverčiame į sveikąjį skaičių (integer) dėl fjučerių kontraktų taisyklių
        amount = int(float(exchange.amount_to_precision(symbol, contracts_qty)))
        min_amount = int(float(market['limits']['amount']['min'])) if market['limits']['amount']['min'] is not None else 1
        
        if amount < min_amount:
            amount = min_amount

        # --- 1. POZICIJOS ATIDARYMAS (MARKET ORDER - SHORT) ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,              # Isolated
            'leverage': int(active_leverage)
        }

        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=amount,
            price=None,
            params=open_params
        )
        print(f"SHORT MARKET OPENED | {symbol} | Qty={amount} | Svertas={active_leverage}x")

        # --- 2. APSAUGINIŲ ORDERIŲ KIEKIŲ PADALINIMAS (70%, 20%, 10%) ---
        min_amount = int(min_amount)
        amt_tp1 = int(amount * 0.70)
        amt_tp2 = int(amount * 0.20)
        
        if amt_tp1 < min_amount:
            amt_tp1 = amount
            amt_tp2 = 0
            amt_tp3 = 0
        elif amt_tp2 < min_amount:
            amt_tp2 = 0
            amt_tp3 = amount - amt_tp1
        else:
            amt_tp3 = amount - (amt_tp1 + amt_tp2)
            if amt_tp3 < min_amount:
                amt_tp2 += amt_tp3
                amt_tp3 = 0

        # --- 3. FIX: TIKSLUS TP/SL REGISTRAVIMAS MEXC FUTURES API v2 ---
        
        # --- A. STOP LOSS TRIGGERIS (Trigger-Market uždarymas) ---
        sl_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'openClose': 'CLOSE',  # Privaloma: nurodo pozicijos uždarymą
            'orderType': 3,        # 3 = Sąlyginis/Trigger Market užsakymas MEXC API
            'triggerType': 1,      # 1 = Aktyvuojama pagal Mark Price
            'stopPrice': sl_price
        }
        # Naudojame 'market' tipą, nes 'spotMarketOrder' MEXC fjučeriuose yra nevalidus
        exchange.create_order(symbol, 'market', 'buy', amount, None, sl_params)
        print(f"SL TRIGGER SET | Price={sl_price} | Qty={amount}")

        # --- B. TAKE PROFIT TRIGGERIAI (Trigger-Limit uždarymas nemokamiems mokesčiams) ---
        tp_limit_base = {
            'posSide': 'SHORT',
            'openType': 1,
            'openClose': 'CLOSE',
            'orderType': 4,        # 4 = Sąlyginis/Trigger Limit užsakymas MEXC API (Maker)
            'triggerType': 1,      # 1 = Mark Price
        }

        if amt_tp1 > 0:
            p1 = tp_limit_base.copy()
            p1['stopPrice'] = tp1_price
            # Siunčiame 'limit', perduodame vykdymo kainą tp1_price ir parametrus
            exchange.create_order(symbol, 'limit', 'buy', amt_tp1, tp1_price, p1)
            print(f"TP1 LIMIT SET (MAKER) | Trigger/Price={tp1_price} | Qty={amt_tp1} (70%)")

        if amt_tp2 > 0:
            p2 = tp_limit_base.copy()
            p2['stopPrice'] = tp2_price
            exchange.create_order(symbol, 'limit', 'buy', amt_tp2, tp2_price, p2)
            print(f"TP2 LIMIT SET (MAKER) | Trigger/Price={tp2_price} | Qty={amt_tp2} (20%)")

        if amt_tp3 > 0:
            p3 = tp_limit_base.copy()
            p3['stopPrice'] = tp3_price
            exchange.create_order(symbol, 'limit', 'buy', amt_tp3, tp3_price, p3)
            print(f"TP3 LIMIT SET (MAKER) | Trigger/Price={tp3_price} | Qty={amt_tp3} (Likutis)")

        print("Visi prekybos lygiai sėkmingai suregistruoti biržoje.")
        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
