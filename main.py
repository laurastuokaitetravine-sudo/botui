import os
import json
from flask import Flask, request
import ccxt
import time

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.5 

@app.route('/')
def home():
    return "SHORT BOTAS VEIKIA!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Pataisytas duomenų gavimas (force=True padeda, jei headeriai netikslūs)
        data = request.get_json(force=True)
        print(f"Gautas signalas: {data}")
    except:
        return "Invalid JSON", 400

    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        sl_price = float(data.get('sl'))

        # Gauname dabartinę rinkos kainą
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        # Saugiklis: Short pozicijai SL turi būti AUKŠČIAU kainos
        if sl_price <= entry_price:
            error_msg = f"KLAIDA: SL ({sl_price}) turi buti AUKSCIAU nei BTC kaina ({entry_price})!"
            print(error_msg)
            return error_msg, 400

        # Skaičiuojame TP (2:1 rizikos santykis)
        risk_distance = sl_price - entry_price
        tp_price = entry_price - (risk_distance * 2)

        # 1. Nustatom svertą (PositionType 2 = Short)
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except:
            pass

        # 2. Skaičiuojame kiekius ir apvaliname pagal biržos tikslumą
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)
        sl_price_str = exchange.price_to_precision(symbol, sl_price)
        
        # 3. ATIDAROME SHORT POZICIJĄ
        # Pridedame entry_price, kad išvengtume Mandatory parameter 'price' klaidos
        print(f"Vykdomas SHORT užsakymas: {amount_str} BTC...")
        exchange.create_order(symbol, 'market', 'sell', amount_str, entry_price, {
            'openType': 1,      # Isolated
            'positionMode': 2   # Short
        })
        
        time.sleep(1.5) # Šiek tiek palaukiame, kol birža užregistruos poziciją

        # 4. STOP LOSS (uždarymas su 'buy')
        # Svarbu: pridėtas papildomas 'price' parametras į params, kad MEXC neatmestų
        print(f"Nustatau SL ties {sl_price_str}")
        exchange.create_order(symbol, 'stop_market', 'buy', amount_str, None, {
            'stopPrice': sl_price_str, 
            'price': sl_price_str, # <--- Čia ištaisoma 700004 klaida SL užsakymui
            'reduceOnly': True,
            'positionMode': 2
        })

        # 5. TAKE PROFIT (uždarymas su 'buy' limit)
        print(f"Nustatau TP ties {tp_price_str}")
        exchange.create_order(symbol, 'limit', 'buy', amount_str, tp_price_str, {
            'reduceOnly': True,
            'positionMode': 2
        })

        return {"status": "success", "message": "Short pozicija su SL ir TP atidaryta"}, 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    # Render automatiškai naudoja PORT aplinkos kintamąjį
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
