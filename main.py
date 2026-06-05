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
DEFAULT_LEVERAGE = 20  
MARGIN_USDT = 50.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (1x TP IŠ PLOT_1, ENTRY IŠ PLOT_2)", 200

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

               # --- NAUJA MONETŲ TVARKYMO IR APSAUGOS LOGIKA ---
        clean_ticker = str(tv_ticker).upper().strip()
        clean_ticker = clean_ticker.replace(".P", "").replace("_", "").replace("-", "")
        
        # Saugus USDT nukirpimas, kad neliktų GMEUSDT/USDT
        if clean_ticker.endswith("USDT"):
            clean_ticker = clean_ticker[:-4]
            
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"
        print(f"Apdorotas signalas monetai: {symbol}")

        # Apsauga nuo neegzistuojančių monetų ir tinklo klaidų (pvz., NICKEL)
        try:
            markets = exchange.load_markets()
            if symbol not in markets:
                print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
                return {"error": f"Symbol {symbol} not found on MEXC"}, 400

            market = markets[symbol]
        except (ccxt.NetworkError, ccxt.BaseError) as exchange_err:
            print(f"KLAIDA: MEXC birža atmetė užklausą dėl simbolio {symbol}. Detalės: {exchange_err}")
            return {"error": f"Biržos klaida apdorojant {symbol}."}, 400

        
        # Sverto tikrinimas
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"Monetai {symbol} taikomas svertas: {final_leverage}x")

        # --- DUOMENŲ SKAITYMAS IŠ TRADINGVIEW PLOTŲ ---
        try:
            sl_price = float(data.get('sl_price'))
            
            # Skaitome ENTRY kainą iš plot_2. Jei nerandame, kaip atsarginį variantą paimame biržos ticker kine
            entry_raw = data.get('entry_price')
            if entry_raw and str(entry_raw).strip().lower() not in ['nan', 'na', 'null', '']:
                entry_price = float(entry_raw)
            else:
                ticker = exchange.fetch_ticker(symbol)
                entry_price = float(ticker['ask'])
                print("Įspėjimas: Nerasta entry_price žinutėje, naudojama biržos ASK kaina.")
            
            # Skaitome TP1 lygį iš plot_1
            tp_raw = data.get('tp_price_1')
            tp_price = float(tp_raw) if tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.985
            
        except (TypeError, ValueError):
            return {"error": "Klaida: Žinutėje gauti blogi kainų formatai"}, 400

        # Suapvaliname kainas pagal tikslias biržos taisykles
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # --- KIEKIO SKAČIAVIMAS (100% pozicijos) ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        
        # Bendras kontraktų kiekis 100% pozicijai
        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])

        # Užtikriname, kad kiekis atitiktų minimalų biržos limitą
        total_contracts = max(total_contracts, min_contracts)

        # Suapvaliname kiekį pagal biržos žingsnį
        final_amount = float(exchange.amount_to_precision(symbol, total_contracts))

        pos_mode = 2  # SHORT fiksuotas
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # --- LIMIT ORDERIO PATEIKIMAS (100% KIEKIO) ---
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

        print(f"SHORT LIMIT pastatytas! Kiekis: {final_amount} (100%) | Kaina: {entry_price} | SL: {sl_price} | TP: {tp_price}")

        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
