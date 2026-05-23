import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

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
MARGIN_USDT = 10.0 

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker in request"}, 400

        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        
        # Išvalome senus orderius
        try:
            exchange.cancel_all_orders(symbol)
        except:
            pass

        ticker = exchange.fetch_ticker(symbol)
        
        # --- STRATEGINIS PAKEITIMAS: MAKER ĮĖJIMAS ---
        # Kad LIMIT būtų MAKER, SHORT kainą keliame šiek tiek AUKŠČIAU dabartinės rinkos kainos (0.02%)
        # Tai apsaugo nuo PostOnly atmetimo ir garantuoja 0% mokestį įeinant.
        raw_price = float(ticker['ask'])
        entry_price = raw_price * 1.0002 
        
        # Sverto tikrinimas
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])
        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)

        # SL nurašymas
        raw_sl = data.get('sl_price')
        sl_price = None
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try: sl_price = float(raw_sl)
            except ValueError: sl_price = None

        # Pelno matematika (20% ROI)
        tp_price = entry_price * 0.992

        if sl_price is None or sl_price <= entry_price:
            sl_price = entry_price * 1.01  

        # Apvalinimai
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # Kiekis
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # Sverto nustatymas
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': 2})
        except:
            pass

        # --- 1 UŽSAKYMAS: ĮĖJIMAS SU POST-ONLY (0% MAKER MOKESTIS) ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(final_leverage),
            'timeInForce': 'PostOnly'  # Garantuoja MAKER statusą
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=amount,
            price=entry_price,
            params=open_params
        )
        print(f"Garantuotas 0% mokesčio SHORT pastatytas kaina: {entry_price}")

        # --- 2 UŽSAKYMAS: TAKE PROFIT TIESIOGINIS LIMIT (0% MAKER MOKESTIS) ---
        # Šis orderis iškart atsistoja į knygą kaip pirkimas žemesne kaina. Kai rinka nukris, suveiks 0% Maker mokestis.
        try:
            tp_params = {
                'posSide': 'SHORT',
                'openType': 1,
                'reduceOnly': True  # Tik uždarymui, kad nesukurti naujos pozicijos
            }
            exchange.create_order(
                symbol=symbol,
                type='limit',              
                side='buy',
                amount=amount,
                price=tp_price,            
                params=tp_params
            )
            print(f"Garantuotas 0% mokesčio LIMIT TP pastatytas ties: {tp_price}")

            # --- 3 UŽSAKYMAS: STOP LOSS (TRIGGER) ---
            # Stop loss negali būti paprastas limit orderis knygoje, nes kaina yra viršuje. Jis lieka trigger tipo.
            sl_params = {
                'posSide': 'SHORT',
                'openType': 1,
                'triggerPrice': sl_price,
            }
            exchange.create_order(
                symbol=symbol,
                type='limit',              
                side='buy',
                amount=amount,
                price=sl_price,            
                params=sl_params
            )
            print(f"Apsauginis Trigger SL nustatytas ties: {sl_price}")

        except Exception as trigger_err:
            print(f"ĮSPĖJIMAS prikabinant TP/SL: {trigger_err}")

        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
