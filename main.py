import os
import json
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'TAVO_ACCESS_KEY',
    'secret': 'TAVO_SECRET_KEY',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 10

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
        
        # Paskaičiuojame TP (1:2 santykis)
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

        # 2. PATAISYMAS: Skaičiuojame kiekį KONTRAKTAIS (MEXC reikalauja sveikojo skaičiaus, min 1)
        # Formulė: (10 USDT * 25x svertas) / BTC kaina = BTC kiekis
        btc_amount = (MARGIN_USDT * LEVERAGE) / entry_price
        
        # Paverčiame į MEXC kontraktus (suapvaliname į mažesnę pusę, kad neviršyti 10 USDT)
        amount = int(btc_amount * 10000) / 10000  # Standartinis BTC kontraktų žingsnis
        amount = float(exchange.amount_to_precision(symbol, amount))
        
        # Jei netyčia gavosi 0, priverstinai nustatome mažiausią įmanomą (1 kontraktą)
        if amount <= 0:
            amount = 1.0

        print(f"Apskaičiuotas kiekis prekybai: {amount} contracts")

        # 3. ATIDAROME POZICIJĄ
        print(f"Atidarau {action}...")
        exchange.create_order(symbol, 'market', side, amount, params={
            'positionMode': pos_mode,
            'openType': 1  # 1 = Atidaryti naują poziciją
        })

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

        msg = f"Sekme! {action} atidarytas. Kiekis: {amount}. SL: {sl_price}, TP: {tp_price}"
        print(msg)
        return msg, 200

    except Exception as e:
        print(f"Klaida vykdant sandori: {str(e)}")
        return str(e), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
