import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Naudok environment variables!) ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('mx0vglmDs15A34AFNE')
    'secret': os.getenv('7f79ccbe92ac42af94e897d9d0de77ea')
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.0 
SYMBOL = 'BTC/USDT:USDT'  # CCXT standartas MEXC futures

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data:
            return "No data", 400
        
        print(f"Gautas signalas: {data}")

        # Patikra
        if data.get('passphrase') != MY_PASSWORD or data.get('action') != 'short':
            return "Unauthorized", 403

        # 1. Gauname rinkos duomenis ir kainą
        market = exchange.load_markets()[SYMBOL]
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = ticker['last']
        
        sl_price = float(data.get('sl'))
        risk_dist = sl_price - entry_price
        tp_price = entry_price - (risk_dist * 2)

        # 2. Skaičiuojame kiekį (BTC) pagal maržą ir svertą
        # amount = (9 USDT * 25) / kaina
        raw_amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = exchange.amount_to_precision(SYMBOL, raw_amount)

        print(f"Entry: {entry_price}, SL: {sl_price}, TP: {tp_price}, Kiekis: {amount}")

        # 3. Atidarome SHORT (Market Order)
        # MEXC swap rinkoje Short = side: 'sell'
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params={
                'leverage': LEVERAGE,
                'openType': 1, # Isolated
                'posSide': 'SHORT' 
            }
        )
        print(f"Short atidarytas: {order['id']}")

        # 4. Nustatome Stop Loss ir Take Profit
        # Naudojame params, nes MEXC reikalauja specifinių triggerių
        
        # Stop Loss
        exchange.create_order(
            symbol=SYMBOL,
            type='limit', # Planorderiai dažnai siunčiami kaip limit/market trigger
            side='buy',   # Uždaryti Short reikia Buy operacija
            amount=amount,
            params={
                'stopPrice': exchange.price_to_precision(SYMBOL, sl_price),
                'triggerType': 'last_price',
                'reduceOnly': True,
                'posSide': 'SHORT'
            }
        )
        
        # Take Profit
        exchange.create_order(
            symbol=SYMBOL,
            type='limit',
            side='buy',
            amount=amount,
            params={
                'stopPrice': exchange.price_to_precision(SYMBOL, tp_price),
                'triggerType': 'last_price',
                'reduceOnly': True,
                'posSide': 'SHORT'
            }
        )

        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    # Render/Railway portų palaikymas
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
