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
    'enableRateLimit': True,                 # Apsaugo nuo IP blokavimo
    'options': {
        'defaultType': 'swap',                 # Naudojame Futures (USDT-M)
        'createMarketBuyOrderRequiresPrice': False,
        'defaultMarket': 'swap',               # Užtikrina, kad CCXT nenaudos spot API
    }
})

# OPTIMIZACIJA: Užkrauname rinkas į atmintį TIK VIENĄ KARTĄ paleidžiant serverį,
# kad botas negaištų laiko gavęs realų TradingView signalą.
try:
    print("Kraunamos MEXC rinkos taisyklės...")
    exchange.load_markets()
    print("Rinkos sėkmingai užkrautos!")
except Exception as me:
    print(f"ĮSPĖJIMAS: Nepavyko iš anksto užkrauti rinkų, bus bandoma vėliau: {me}")

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25                        # Jūsų nustatytas svertas (25x)
MARGIN_USDT = 5.0                            # Jūsų nustatyta marža (5 USDT)

@app.route('/')
def home():
    return "BOTAS ONLINE (SUTVARKYTAS SVERTAS + MARKET ĮĖJIMAS)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # --- JSON NUSKAITYMAS SU ATSARGINE TEKSTO APDOROJIMO LOGIKA ---
        raw_data = request.data.decode('utf-8').strip()
        try:
            data = json.loads(raw_data)
        except Exception as je:
            print(f"KRITINĖ KLAIDA: Sugadintas JSON formatas iš TradingView! Gautas tekstas: {raw_data}")
            return {"error": "Invalid JSON format. Check quotation marks."}, 400

        # --- SAUGUMAS ---
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        # --- TIK SHORT ---
        if str(data.get('action', '')).lower() != 'short':
            return "Ignored (SHORT only mode)", 200

        # --- TICKER (SAUGUS IR UNIVERSALUS PAVADINIMŲ TVARKYMAS) ---
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = tv_ticker.upper().replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        
        # Saugiklis pigioms monetoms
        if clean == "PEPE":
            clean = "10000PEPE"
            
        symbol = f"{clean}/USDT:USDT"

        # Naudojame jau atmintyje esančias rinkas (žaibiškas greitis)
        if symbol not in exchange.markets:
            # Jei nerandame, bandom atnaujinti atsargai
            exchange.load_markets()
            if symbol not in exchange.markets:
                print(f"KLAIDA: Moneta {symbol} nerasta MEXC Futures sąraše.")
                return {"error": f"Symbol {symbol} not found"}, 400

        market = exchange.markets[symbol]

        # --- TICKERIO GAVIMAS SU ATSARGINE TINKLO KLAIDŲ APSAUGA ---
        ticker = None
        for _ in range(3):  # Jei įvyks tinklo sutrikimas, bandys iki 3 kartų
            try:
                ticker = exchange.fetch_ticker(symbol)
                break
            except ccxt.NetworkError as ne:
                print(f"Laikinai nepavyko pasiekti MEXC tinklo, bandoma vėl... {ne}")
                time.sleep(1)
        
        if not ticker:
            return {"error": "Nepavyko gauti kainos iš MEXC dėl tinklo sutrikimų."}, 502

        # Paimame einamąją ASK (pardavimo) kainą iš orderbook rinkos užsakymui
        entry_price = float(ticker['ask'])

        # --- SVERTO APRIBOROJIMAS ---
        max_exchange_leverage = DEFAULT_LEVERAGE  
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_exchange_leverage = float(market['limits']['leverage']['max'])

        active_leverage = int(min(DEFAULT_LEVERAGE, max_exchange_leverage))

        # --- PRELIMINARUS LEVERAGE NUSTATYMAS SERVERYJE ---
        try:
            exchange.set_leverage(active_leverage, symbol, {
                'openType': 1,   # 1 = Isolated
                'positionType': 2 # 2 = Short
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

        # Fallback procentiniai tikslai tik jei TV netyčia atsiųstų tuščius TP (NaN)
        if tp1_price is None: tp1_price = entry_price * 0.990
        if tp2_price is None: tp2_price = entry_price * 0.980
        if tp3_price is None: tp3_price = entry_price * 0.970

        # --- PRECISION (SUVALINIMAS PAGAL BIRŽOS TAISYKLĖS) ---
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price    = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price   = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price   = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price   = float(exchange.price_to_precision(symbol, tp3_price))

        # --- KIEKIS IR KONTRAKTAI ---
        total_value = MARGIN_USDT * active_leverage
        raw_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_amount / contract_size

        amount = int(float(exchange.amount_to_precision(symbol, contracts_qty)))
        min_amount = int(float(market['limits']['amount']['min'])) if market['limits']['amount']['min'] is not None else 1
        
        # Apsauga nuo 0 kontraktų klaidos
        if amount < min_amount:
            amount = min_amount

        # --- ATIDARYMAS (MARKET ORDER) ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,              # 1 = Isolated
            'leverage': int(active_leverage)  # Griežtai reikalaujama MEXC API
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

        # --- APRAŠOME SL IR TP TRIGGERIUS ---
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

        # --- 1. STOP LOSS TRIGGERIS (TAKER) ---
        sl_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'openClose': 'CLOSE',
            'triggerType': 1,      # Mark Price
            'stopPrice': sl_price
        }
        exchange.create_order(symbol, 'spotMarketOrder', 'buy', amount, None, sl_params)
        print(f"SL TRIGGER SET (TAKER) | Price={sl_price} | Qty={amount}")

        # --- 2. TAKE PROFIT TRIGGER-LIMIT (MAKER) ---
        tp_limit_base = {
            'posSide': 'SHORT',
            'openType': 1,
            'openClose': 'CLOSE',
            'triggerType': 1,      # Mark Price
        }

        if amt_tp1 > 0:
            p1 = tp_limit_base.copy()
            p1['stopPrice'] = tp1_price
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
