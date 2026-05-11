import os
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Įrašyk savo duomenis) ---
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
    # Gauname duomenis ir priverčiame juos skaityti kaip JSON
    data = request.get_json(force=True, silent=True)

    # 1. Saugumo patikra
    if not data or data.get('passphrase') != MY_PASSWORD:
        print("Klaida: Neteisingas slaptažodis arba tušti duomenys")
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') # 'buy' arba 'short'
        sl_price = float(data.get('sl'))

        # 2. Gauname dabartinę kainą
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        # 3. Skaičiuojame TP (1:2 santykis)
        risk_distance = abs(entry_price - sl_price)
        if action == 'short':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side = 'sell', 'buy'
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side = 'buy', 'sell'

        # 4. Nustatome svertą biržoje
        exchange.set_leverage(LEVERAGE, symbol)

        # 5. Skaičiuojame kiekį
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = float(exchange.amount_to_precision(symbol, amount))
        
        # 6. ATIDAROME POZICIJĄ
        print(f"Vykdomas {action} užsakymas...")
        exchange.create_order(symbol, 'market', side, amount)

        # 7. STATOME STOP LOSS
        exchange.create_order(symbol, 'stop_market', close_side, amount, None, {
            'stopPrice': sl_price, 
            'reduceOnly': True
        })

        # 8. STATOME TAKE PROFIT
        exchange.create_order(symbol, 'limit', close_side, amount, tp_price, {
            'reduceOnly': True
        })

        msg = f"Sėkmė! {action} atidarytas. SL: {sl_price}, TP: {tp_price}"
        print(msg)
        return msg, 200

    except Exception as e:
        print(f"Klaida vykdant sandorį: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
