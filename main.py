import os
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 10.0  # Pakeičiau į 25.0, kad atitiktų tavo norus
SYMBOL = 'BTC/USDT:USDT'

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        if str(data.get('action')).lower() != 'short':
            return "Ignored", 200

        # 1. Rinkos duomenys
        markets = exchange.load_markets()
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])
        
        # 2. SL ir TP skaičiavimas (RR 1:2)
        sl_input = data.get('sl')
        sl_price = float(sl_input) if sl_input else entry_price * 1.01
        risk_dist = sl_price - entry_price
        tp_price = entry_price - (risk_dist * 2)

        # 3. KIEKIO SKAIČIAVIMAS (Pataisyta, kad naudotų visą maržą)
        # Paskaičiuojame bendrą galią: 25 USDT * 25 svertas = 625 USDT vertės pozicija
        total_value_usdt = MARGIN_USDT * LEVERAGE
        
        # Paskaičiuojame kiek tai BTC (pvz., 625 / 80500 = 0.0077 BTC)
        raw_btc_amount = total_value_usdt / entry_price
        
        # Naudojame specialią funkciją, kuri suapvalina kiekį pagal MEXC taisykles
        # Svarbu: MEXC BTC kontrakto žingsnis yra 0.0001 BTC
        amount = float(exchange.amount_to_precision(SYMBOL, raw_btc_amount))

        # Saugiklis: jei netyčia amount būtų 0, imam minimumą
        min_qty = float(markets[SYMBOL]['limits']['amount']['min'])
        if amount < min_qty:
            amount = min_qty

        # 4. Svertas
        try:
            exchange.set_leverage(int(LEVERAGE), SYMBOL, {'marginMode': 'isolated'})
        except:
            pass

        # 5. Atidarome SHORT (Market)
        order = exchange.create_order(
            symbol=SYMBOL, type='market', side='sell', amount=amount,
            params={'posSide': 'SHORT', 'openType': 1, 'leverage': int(LEVERAGE)}
        )

        # 6. AUTOMATINIS STOP LOSS
        exchange.create_order(
            symbol=SYMBOL, type='spot_market', side='buy', amount=amount,
            params={
                'stopPrice': exchange.price_to_precision(SYMBOL, sl_price),
                'posSide': 'SHORT', 'reduceOnly': True, 'triggerType': 'last_price'
            }
        )

        # 7. AUTOMATINIS TAKE PROFIT (1:2)
        exchange.create_order(
            symbol=SYMBOL, type='spot_market', side='buy', amount=amount,
            params={
                'stopPrice': exchange.price_to_precision(SYMBOL, tp_price),
                'posSide': 'SHORT', 'reduceOnly': True, 'triggerType': 'last_price'
            }
        )

        print(f"SĖKMĖ! Atidaryta pozicija: {amount} BTC. Entry: {entry_price}")
        return {"status": "success", "amount_deployed": amount}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
