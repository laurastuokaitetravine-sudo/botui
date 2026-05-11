import os
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# --- ĮRAŠYK SAVO RAKTUS ČIA ---
exchange = ccxt.mexc({
    'apiKey': 'TAVO_ACCESS_KEY',
    'secret': 'TAVO_SECRET_KEY',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "mano_slaptas_botas_123"
LEVERAGE = 25    # Tavo nurodytas svertas
MARGIN_USDT = 10 # Tavo nurodyta suma

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('passphrase') != MY_PASSWORD:
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action') # 'buy' arba 'short'
        sl_price = float(data.get('sl'))

        # 1. Gauname įėjimo kainą
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']
        
        # 2. Skaičiuojame TP (1:2 santykis)
        risk_distance = abs(entry_price - sl_price)
        if action == 'short':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side = 'sell', 'buy'
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side = 'buy', 'sell'

        # 3. Nustatom svertą biržoje
        exchange.set_leverage(LEVERAGE, symbol)

        # 4. Skaičiuojame kiekį (10 USDT * 25x / kaina)
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = float(exchange.amount_to_precision(symbol, amount))
        
        # 5. Atidarome poziciją
        exchange.create_order(symbol, 'market', side, amount)

        # 6. Statome Stop Loss
        exchange.create_order(symbol, 'stop_market', close_side, amount, None, {
            'stopPrice': sl_price, 'reduceOnly': True
        })

        # 7. Statome Take Profit (1:2)
        exchange.create_order(symbol, 'limit', close_side, amount, tp_price, {
            'reduceOnly': True
        })

        return f"Atidaryta {action}. SL: {sl_price}, TP: {tp_price}", 200

    except Exception as e:
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
