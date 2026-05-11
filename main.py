import os
import json
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.5 # Sumažinau iki 9.5, kad 100% užtektų mokesčiams

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw_data = request.get_data(as_text=True)
        data = json.loads(raw_data)
        print(f"Gautas signalas: {data}")
    except Exception as e:
        return "Invalid JSON", 400

    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') 
        sl_price = float(data.get('sl'))

        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        risk_distance = abs(entry_price - sl_price)
        if action == 'short':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side, pos_mode = 'sell', 'buy', 2
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side, pos_mode = 'buy', 'sell', 1

        # 1. Nustatom svertą (Isolated)
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # 2. Skaičiuojame kiekį
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = float(exchange.amount_to_precision(symbol, amount))
        
        # 3. ATIDAROME POZICIJĄ (SVARBU: pridedame openType ir positionMode)
        print(f"Atidarau {action}...")
        exchange.create_order(symbol, 'market', side, amount, params={
            'positionMode': pos_mode,
            'openType': 1 # 1 reiškia OPEN (atidaryti naują)
        })

        # 4. STOP LOSS
        exchange.create_order(symbol, 'stop_market', close_side, amount, None, {
            'stopPrice': sl_price, 
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        # 5. TAKE PROFIT
        exchange.create_order(symbol, 'limit', close_side, amount, tp_price, {
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        return f"Sekme! {action} atidarytas.", 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
