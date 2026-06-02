import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Naudojant Render Environment Variables) ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25  
MARGIN_USDT = 30.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (3x TP IŠ INDIKATORIAUS)", 200

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
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        
        # --- IŠTAISYTA: Užklausą darome per stabilesnį orderbook API ---
        orderbook = exchange.fetch_order_book(symbol, 1)
        entry_price = float(orderbook['asks'][0][0])
        
        # Sverto tikrinimas
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"Monetai {symbol} taikomas svertas: {final_leverage}x")

        # --- IŠTAISYTA: Svertą nustatome prieš pradedant skaičiuoti kiekius ---
        pos_mode = 2  # SHORT fiksuotas
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # --- DUOMENŲ SKAITYMAS TIESIAI IŠ TRADINGVIEW PLOTŲ ---
        try:
            sl_price = float(data.get('sl_price'))
            
            tp1_raw = data.get('tp_price_1')
            tp2_raw = data.get('tp_price_2')
            tp3_raw = data.get('tp_price_3')
            
            tp1_price = float(tp1_raw) if tp1_raw and str(tp1_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.992
            tp2_price = float(tp2_raw) if tp2_raw and str(tp2_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.985
            tp3_price = float(tp3_raw) if tp3_raw and str(tp3_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.980
            
        except (TypeError, ValueError):
            return {"error": "Klaida: Žinutėje gauti blogi kainų formatai"}, 400

        # Suapvaliname kainas pagal tikslias biržos taisykles
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price = float(exchange.price_to_precision(symbol, tp3_price))

        # --- KIEKIO IR PROPORCIJŲ SKAIČIAVIMAS (70% / 20% / 10%) ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        
        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])

        qty_tp1 = total_contracts * 0.70
        qty_tp2 = total_contracts * 0.20
        qty_tp3 = total_contracts * 0.10

        qty_tp1 = max(qty_tp1, min_contracts)
        qty_tp2 = max(qty_tp2, min_contracts)
        qty_tp3 = max(qty_tp3, min_contracts)

        amt_tp1 = float(exchange.amount_to_precision(symbol, qty_tp1))
        amt_tp2 = float(exchange.amount_to_precision(symbol, qty_tp2))
        amt_tp3 = float(exchange.amount_to_precision(symbol, qty_tp3))

        tp_configs = [
            {"num": 1, "amt": amt_tp1, "tp": tp1_price, "pct": "70%"},
            {"num": 2, "amt": amt_tp2, "tp": tp2_price, "pct": "20%"},
            {"num": 3, "amt": amt_tp3, "tp": tp3_price, "pct": "10%"}
        ]

        order_ids = []

        # --- 3 ATSKIRŲ LIMIT ORDERIŲ PATEIKIMAS ---
        for config in tp_configs:
            params = {
                'posSide': 'SHORT',
                'openType': 1,
                'leverage': int(final_leverage),
                'stopLossPrice': sl_price,
                'takeProfitPrice': config["tp"],
                'timeInForce': 'PostOnly'  
            }

            order = exchange.create_order(
                symbol=symbol,
                type='limit',       
                side='sell',
                amount=config["amt"],
                price=entry_price,  
                params=params
            )
            order_ids.append(order['id'])
            print(f"SHORT LIMIT TP{config['num']} ({config['pct']}) pastatytas! Kiekis: {config['amt']} | SL: {sl_price} | TP: {config['tp']}")

        return {"status": "success", "symbol": symbol, "order_ids": order_ids}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
