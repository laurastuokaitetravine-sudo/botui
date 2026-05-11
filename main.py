import os
import json
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Būtinai įrašyk savo MEXC API raktus) ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "Ortofon121213!G" # Turi sutapti su TradingView passphrase
LEVERAGE = 25
MARGIN_USDT = 10

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. Skaitome duomenis kaip paprastą tekstą, kad išvengtume 415 klaidos
    try:
        raw_data = request.get_data(as_text=True)
        data = json.loads(raw_data)
        print(f"Gautas signalas: {data}")
    except Exception as e:
        print(f"Klaida nuskaitant JSON: {e}")
        return "Invalid JSON format", 400

    # 2. Saugumo patikra
    if not data or data.get('passphrase') != MY_PASSWORD:
        print("Klaida: Neteisingas slaptažodis")
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') # 'buy' arba 'short'
        sl_price = float(data.get('sl'))

        # 3. Gauname dabartinę kainą (Entry)
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        # 4. Skaičiuojame Take Profit (1:2 santykis)
        risk_distance = abs(entry_price - sl_price)
        if action == 'short':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side = 'sell', 'buy'
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side = 'buy', 'sell'

        # 5. Nustatome svertą biržoje
        exchange.set_leverage(LEVERAGE, symbol)

        # 6. Apskaičiuojame kiekį (10 USDT * 25x / kaina)
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = float(exchange.amount_to_precision(symbol, amount))
        
        # 7. ATIDAROME POZICIJĄ
        print(f"Vykdomas {action} užsakymas...")
        exchange.create_order(symbol, 'market', side, amount)

        # 8. STATOME STOP LOSS
        exchange.create_order(symbol, 'stop_market', close_side, amount, None, {
            'stopPrice': sl_price, 
            'reduceOnly': True
        })

        # 9. STATOME TAKE PROFIT
        exchange.create_order(symbol, 'limit', close_side, amount, tp_price, {
            'reduceOnly': True
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
