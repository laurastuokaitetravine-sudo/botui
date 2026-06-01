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
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 5  
MARGIN_USDT = 5.0     

@app.route('/')
def home():
    return "BOTAS ONLINE (TIK SHORT - LIMIT POST-ONLY)", 200

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
            return {"error": "Missing ticker"}, 400

        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]

        # --- DUOMENŲ PAĖMIMAS IŠ TRADINGVIEW ---
        try:
            entry_price = float(data.get('entry_price'))
            sl_price = float(data.get('sl_price'))
            tp_price = float(data.get('tp_price'))
        except (TypeError, ValueError):
            return {"error": "Klaida: Žinutėje trūksta entry_price, sl_price arba tp_price reikšmių"}, 400

        pos_side = 'SHORT'
        pos_mode = 2 

        # --- SVERTO NUSTATYMAS ---
        max_lev = DEFAULT_LEVERAGE
        if market.get('limits', {}).get('leverage', {}).get('max'):
            max_lev = min(DEFAULT_LEVERAGE, int(market['limits']['leverage']['max']))

        try:
            exchange.set_leverage(max_lev, symbol, {
                'openType': 1,
                'positionType': pos_mode
            })
        except Exception:
            pass

        # --- APSAUGA NUO POST-ONLY ATMETIMO ---
        # Kadangi kaina jau liečia lygį, padidiname Short limit kainą 0.05%, 
        # kad orderis garantuotai atsidurtų knygoje virš esamos kainos ir nebūtų atmestas.
        limit_entry_price = entry_price * 1.0005 

        # Kainų suapvalinimas pagal tikslias biržos taisykles
        limit_entry_price = float(exchange.price_to_precision(symbol, limit_entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # --- KIEKIO (AMOUNT) SKAIČIAVIMAS ---
        total_value = MARGIN_USDT * max_lev
        raw_crypto = total_value / limit_entry_price
        contract_size = float(market.get('contractSize', 1.0))
        contracts = raw_crypto / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts, min_contracts)
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # --- PARAMETRAI SU POSTONLY IR TP/SL ---
        params = {
            'posSide': pos_side,
            'openType': 1,  
            'leverage': max_lev,
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price,
            'timeInForce': 'PostOnly'  # Užtikrina 0% Maker mokesčius (jei neįmanoma pastatyti – orderis atšaukiamas, o ne vykdomas kaip brangus Taker)
        }

        print(f"Siunčiamas SHORT LIMIT (Post-Only) | Įėjimas: {limit_entry_price} | Kiekis: {amount} | SL: {sl_price} | TP: {tp_price}")

        # Vykdomas LIMIT užsakymas
        order = exchange.create_order(
            symbol=symbol,
            type='limit', 
            side='sell',
            amount=amount,
            price=limit_entry_price,
            params=params
        )

        print(f"SHORT LIMIT PATEIKTAS SĖKMINGAI | {symbol} | ID: {order['id']}")

        return {
            "status": "success",
            "symbol": symbol,
            "order_id": order['id'],
            "entry_price": limit_entry_price,
            "sl": sl_price,
            "tp": tp_price
        }, 200

    except Exception as e:
        print(traceback.format_exc())
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
