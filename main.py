import os
import json
from flask import Flask, request, jsonify
import ccxt
import time
import traceback

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea', # Būtinai patikrinkite, ar čia pilnas Secret Key!
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.0 

@app.route('/')
def home():
    return "BOTAS GYVAS! Serveris laukia signalu.", 200

# Naujas maršrutas greitam patikrinimui per naršyklę
@app.route('/test')
def test_connection():
    try:
        ticker = exchange.contractPublicGetTicker({'symbol': 'BTC_USDT'})
        kaina = ticker['data']['fairPrice']
        return f"Sėkmė! Ryšys su MEXC yra. BTC kaina dabar: {kaina}", 200
    except Exception as e:
        return f"Klaida jungiantis prie MEXC: {str(e)}", 500

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. Priverstinis teksto gavimas (išvengia JSON formatavimo klaidų)
    raw_body = request.get_data(as_text=True)
    print(f"--- GAUTAS NAUJAS SIGNALAS ---")
    print(f"Raw data: '{raw_body}'")

    try:
        data = json.loads(raw_body)
    except Exception as e:
        print(f"KLAIDA: Nepavyko perskaityti JSON. {str(e)}")
        return f"JSON klaida: {str(e)}", 400

    # 2. Slaptažodžio ir krypties patikra
    if data.get('passphrase') != MY_PASSWORD:
        print(f"KLAIDA: Neteisingas slaptazodis. Gauta: {data.get('passphrase')}")
        return "Unauthorized", 403

    if data.get('action') != 'short':
        print("KLAIDA: Gautas ne SHORT signalas.")
        return "Tik SHORT palaikomas", 400

    try:
        symbol = 'BTC/USDT'
        sl_price_raw = float(data.get('sl'))

        # 3. Gauname kaina
        response = exchange.contractPublicGetTicker({'symbol': 'BTC_USDT'})
        entry_price = float(response['data']['fairPrice'])
        print(f"Entry kaina: {entry_price}")

        if sl_price_raw <= entry_price:
            return f"Klaida: SL ({sl_price_raw}) turi buti virs kainos!", 400

        # 4. Kiekiai
        risk_distance = sl_price_raw - entry_price
        tp_price = entry_price - (risk_distance * 2)

        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        sl_str = exchange.price_to_precision(symbol, sl_price_raw)
        tp_str = exchange.price_to_precision(symbol, tp_price)

        # 5. ATIDARYMAS
        print(f"Vykdomas SHORT: {amount_str} BTC...")
        exchange.create_order(symbol, 'market', 'sell', float(amount_str), entry_price, {
            'openType': 1,
            'positionMode': 2
        })
        
        time.sleep(2)

        # 6. SL IR TP
        print(f"Nustatomas SL ({sl_str}) ir TP ({tp_str})")
        
        # Stop Loss
        exchange.create_order(symbol, 'trigger', 'buy', float(amount_str), None, {
            'triggerPrice': float(sl_str),
            'triggerDirection': 1, 
            'reduceOnly': True,
            'positionMode': 2
        })

        # Take Profit
        exchange.create_order(symbol, 'trigger', 'buy', float(amount_str), float(tp_str), {
            'triggerPrice': float(tp_str),
            'triggerDirection': 2, 
            'reduceOnly': True,
            'positionMode': 2
        })

        print("SĖKMĖ: Sandoris sudarytas!")
        return {"status": "success"}, 200

    except Exception as e:
        # Atspausdina pilną klaidos kelią (Traceback)
        print("--- KRITINĖ KLAIDA ---")
        traceback.print_exc()
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
