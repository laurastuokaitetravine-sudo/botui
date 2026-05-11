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

MY_PASSWORD = "mano_slaptas_botas_123"
LEVERAGE = 25
MARGIN_USDT = 10

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw_data = request.get_data(as_text=True)
        data = json.loads(raw_data)
        print(f"Gautas signalas: {data}")
    except Exception as e:
        print(f"Klaida nuskaitant JSON: {e}")
        return "Invalid JSON", 400

    if not data or data.get('passphrase') != MY_PASSWORD:
        print("Klaida: Neteisingas slaptazodis")
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') # 'buy' arba 'short'
        sl_price = float(data.get('sl'))

        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        risk_distance = abs(entry_price - sl_price)
        if action == 'short':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side = 'sell', 'buy'
            pos_mode = 2 # Short position
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side = 'buy', 'sell'
            pos_mode = 1 # Long position

        # 1. Nustatom svertą
        exchange.set_leverage(LEVERAGE, symbol)

        # 2. Skaičiuojame kiekį
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = float(exchange.amount_to_precision(symbol, amount))
        
        # 3. ATIDAROME POZICIJĄ (Su positionMode pataisymu)
        print(f"Vykdomas {action} uzsakymas...")
        exchange.create_order(symbol, 'market', side, amount, params={'positionMode': pos_mode})

        # 4. STATOME STOP LOSS
        exchange.create_order(symbol, 'stop_market', close_side, amount, None, {
            'stopPrice': sl_price, 
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        # 5. STATOME TAKE PROFIT
        exchange.create_order(symbol, 'limit', close_side, amount, tp_price, {
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        msg = f"Sekme! {action} atidarytas. SL: {sl_price}, TP: {tp_price}"
        print(msg)
        return msg, 200

    except Exception as e:
        error_msg = f"Klaida vykdant sandori: {str(e)}"
        print(error_msg)
        return error_msg, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
