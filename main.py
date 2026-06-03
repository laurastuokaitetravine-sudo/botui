import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Naudojant Render Environment Variables) ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,  # <--- SAUGIKLIS: Neleidžia botui siųsti užklausų per greitai
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 20  
MARGIN_USDT = 45.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (STARTER PLAN + RETRY LOGIC)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        
        if not data:
            try:
                raw_data = request.data.decode('utf-8').strip()
                data = json.loads(raw_data)
            except Exception as json_err:
                print(f"Nepavyko konvertuoti teksto į JSON: {json_err}")
                return {"error": "Invalid JSON format"}, 400

        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            print("Klaida: Žinutėje negautas 'ticker' kintamasis")
            return {"error": "Missing ticker in request"}, 400

        # Universali monetų tvarkymo logika
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        
        is_pepe = False
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            is_pepe = True
            
        symbol = f"{clean_ticker}/USDT:USDT"

        # --- TINKLO KLAIDŲ SAUGIKLIS (RETRY LOGIC) ---
        markets = None
        ticker = None
        
        for retry in range(3):
            try:
                if not markets:
                    markets = exchange.load_markets()
                ticker = exchange.fetch_ticker(symbol)
                break  # Jei viskas gerai, išeiname iš šio ciklo
            except ccxt.NetworkError as ne:
                print(f"MEXC Tinklo klaida (Bandymas {retry + 1}/3): {ne}. Bandome vėl po 1.5s...")
                time.sleep(1.5)
        
        if not ticker or not markets:
            print("KLAIDA: Nepavyko pasiekti MEXC API po 3 bandymų dėl NetworkError.")
            return {"error": "MEXC API unreachable due to network errors"}, 502

        if symbol not in markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        entry_price = float(ticker['ask']) 

        # Sverto tikrinimas
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': 2})
        except Exception as lev_err:
            print(f"Sverto nustatymo pranešimas (ignoruojama): {lev_err}")

        # --- DUOMENŲ SKAITYMAS IR KOREKCIJA ---
        try:
            sl_price = float(data.get('sl_price'))
            tp1_raw = data.get('tp_price_1')
            tp2_raw = data.get('tp_price_2')
            tp3_raw = data.get('tp_price_3')
            
            if is_pepe:
                sl_price *= 10000
                tp1_raw = float(tp1_raw) * 10000 if tp1_raw and str(tp1_raw).lower() not in ['nan', 'na', ''] else None
                tp2_raw = float(tp2_raw) * 10000 if tp2_raw and str(tp2_raw).lower() not in ['nan', 'na', ''] else None
                tp3_raw = float(tp3_raw) * 10000 if tp3_raw and str(tp3_raw).lower() not in ['nan', 'na', ''] else None

            tp1_price = float(tp1_raw) if tp1_raw else entry_price * 0.992
            tp2_price = float(tp2_raw) if tp2_raw else entry_price * 0.985
            tp3_price = float(tp3_raw) if tp3_raw else entry_price * 0.980
            
        except (TypeError, ValueError) as price_err:
            return {"error": f"Klaida parsing kainas: {str(price_err)}"}, 400

        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price = float(exchange.price_to_precision(symbol, tp3_price))

        # --- KIEKIO SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        
        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])

        qty_tp1 = max(total_contracts * 0.70, min_contracts)
        qty_tp2 = max(total_contracts * 0.20, min_contracts)
        qty_tp3 = max(total_contracts * 0.10, min_contracts)

        amt_tp1 = float(exchange.amount_to_precision(symbol, qty_tp1))
        amt_tp2 = float(exchange.amount_to_precision(symbol, qty_tp2))
        amt_tp3 = float(exchange.amount_to_precision(symbol, qty_tp3))
        
        total_open_amt = amt_tp1 + amt_tp2 + amt_tp3

        # --- 1. ATIDAROME SHORT POZICIJA (MARKET) ---
        print(f"Atsidaro SHORT pozicija rinkos kaina... Kiekis: {total_open_amt}")
        open_params = {'posSide': 'SHORT', 'openType': 1}
        
        main_order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=total_open_amt,
            params=open_params
        )

        # --- 2. STOP LOSS VISAI POZICIJAI ---
        sl_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'stopLossPrice': sl_price,
            'type': 'stop_loss'
        }
        try:
            exchange.create_order(
                symbol=symbol,
                type='market',
                side='buy',
                amount=total_open_amt,
                price=sl_price,
                params=sl_params
            )
            print(f"Bendra Stop Loss uždėtas ties: {sl_price}")
        except Exception as sl_err:
            print(f"Kritinė klaida dedant Stop Loss: {sl_err}")

        # --- 3. 3 ATSKIRI TAKE PROFIT LIMIT ORDERIAI ---
        tp_configs = [
            {"num": 1, "amt": amt_tp1, "price": tp1_price, "pct": "70%"},
            {"num": 2, "amt": amt_tp2, "price": tp2_price, "pct": "20%"},
            {"num": 3, "amt": amt_tp3, "price": tp3_price, "pct": "10%"}
        ]

        tp_order_ids = []
        for config in tp_configs:
            tp_params = {
                'posSide': 'SHORT',
                'openType': 1,
                'takeProfitPrice': config["price"],
                'timeInForce': 'PostOnly'
            }
            try:
                tp_order = exchange.create_order(
                    symbol=symbol,
                    type='limit',  
                    side='buy',    
                    amount=config["amt"],
                    price=config["price"],
                    params=tp_params
                )
                tp_order_ids.append(tp_order['id'])
                print(f"TP{config['num']} LIMIT ({config['pct']}) pastatytas ties {config['price']} | Kiekis: {config['amt']}")
            except Exception as tp_err:
                print(f"Klaida statant TP{config['num']}: {tp_err}")

        return {
            "status": "success", 
            "symbol": symbol, 
            "entry_order_id": main_order['id'],
            "tp_order_ids": tp_order_ids
        }, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
