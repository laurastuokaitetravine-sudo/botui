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
    return "Short Botas Veikia!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
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
        
        # Priverstinis patikrinimas: Short pozicijai SL turi būti AUKŠČIAU kainos
        if sl_price <= entry_price:
            error_msg = f"KLAIDA: SL ({sl_price}) turi buti AUKSCIAU nei BTC kaina ({entry_price}) Short pozicijai!"
            print(error_msg)
            return error_msg, 400

        # Skaičiuojame TP (2:1 rizikos santykis)
        risk_distance = sl_price - entry_price
        tp_price = entry_price - (risk_distance * 2)

        # 1. Nustatom svertą (PositionMode 2 = Short)
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': 2})
        except:
            pass

        # 2. Skaičiuojame kiekį ir apvaliname pagal biržos reikalavimus
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount_str = exchange.amount_to_precision(symbol, amount)
        tp_price_str = exchange.price_to_precision(symbol, tp_price)
        sl_price_str = exchange.price_to_precision(symbol, sl_price)
        
        # 3. ATIDAROME SHORT POZICIJĄ
        # PRIDĖTA: entry_price įrašytas į 'price' laukelį, kad išvengtume 700004 klaidos
        print(f"Atidarau SHORT užsakymą: {amount_str} BTC...")
        exchange.create_order(symbol, 'market', 'sell', amount_str, entry_price, {
            'openType': 1,      # Isolated
            'positionMode': 2   # Short
        })
        
        time.sleep(1) # Laukiam, kol birža „suvirškins“ poziciją

        # 4. STOP LOSS (uždarymas su 'buy')
        print(f"Nustatau SL ties {sl_price_str}")
        exchange.create_order(symbol, 'stop_market', 'buy', amount_str, None, {
            'stopPrice': sl_price_str, 
            'reduceOnly': True,
            'positionMode': 2
        })

        # 5. TAKE PROFIT (uždarymas su 'buy' limit užsakymu)
        print(f"Nustatau TP ties {tp_price_str_str}")
        exchange.create_order(symbol, 'limit', 'buy', amount_str, tp_price_str, {
            'reduceOnly': True,
            'positionMode': 2
        })

        return {"status": "success", "action": "short"}, 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
