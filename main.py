import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- SAUGI KEITYKLOS KONFIGŪRACIJA ---
exchange_config = {
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    },
    'timeout': 30000  # Padidiname laukimo laiką iki 30 sekundžių
}

# Automatiškai prijungiame proxy, jei jis įvestas Render nustatymuose
if os.getenv('PROXY_URL'):
    exchange_config['proxies'] = {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }

exchange = ccxt.mexc(exchange_config)

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 50.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (1x LIMIT 100%, 1x TP 100% IŠ PLOT_1, AUTO TP 50%)", 200

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

        # Monetų tvarkymo logika
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        # --- SAUGUS DUOMENŲ KROVIMAS SU TRIS KARTUS KARTAS (RETRIES) ---
        markets = None
        ticker = None
        
        for attempt in range(3):
            try:
                if not markets:
                    markets = exchange.load_markets()
                ticker = exchange.fetch_ticker(symbol)
                break  # Jei pavyko, stabdome ciklą
            except ccxt.NetworkError as ne:
                print(f"Tinklo klaida su MEXC (Bandymas {attempt + 1}/3): {ne}")
                if attempt < 2:
                    time.sleep(3)  # Palaukiame 3 sekundes prieš bandant vėl
            except Exception as e:
                print(f"Kita API klaida: {e}")
                break

        if not markets or symbol not in markets or not ticker:
            print("KLAIDA: Nepavyko susisiekti su MEXC. Patikrinkite PROXY_URL nustatymus.")
            return {"error": "MEXC API blocked. Check proxy settings."}, 400

        market = markets[symbol]
        entry_price = float(ticker['ask'])
        
        # Sverto tikrinimas
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)

        # --- KAINŲ SKAITYMAS IR APVALINIMAS ---
        try:
            sl_price = float(data.get('sl_price'))
            tp_raw = data.get('tp_price_1')
            
            # PAKEISTA: Jei TP reikšmė tuščia arba nan, nustatome 50% žemesnę kainą nuo įėjimo taško
            tp_price = float(tp_raw) if tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.50
        except (TypeError, ValueError):
            return {"error": "Blogas kainų formatas iš TradingView"}, 400

        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # --- KIEKIO SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        
        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])
        total_contracts = max(total_contracts, min_contracts)
        final_amount = float(exchange.amount_to_precision(symbol, total_contracts))

        # Nustatome svertą biržoje
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': 2})
        except:
            pass

        # --- LIMIT ORDERIO PATEIKIMAS ---
        params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(final_leverage),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price,
            'timeInForce': 'PostOnly'  
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',       
            side='sell',
            amount=final_amount,
            price=entry_price,  
            params=params
        )

        print(f"SĖKMĖ: SHORT LIMIT pastatytas monetai {symbol}! Kiekis: {final_amount} | Kaina: {entry_price} | SL: {sl_price} | TP: {tp_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
