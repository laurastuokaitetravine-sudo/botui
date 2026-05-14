import os
import traceback
import math
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
MARGIN_USDT = 10.0  # Jūsų norima marža (užstatas)
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
        market = markets[SYMBOL]
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])
        
        # 2. GRIEŽTAS KIEKIO SKAIČIAVIMAS
        # Bendra pozicijos vertė (pvz., 10 USDT * 25 svertas = 250 USDT pozicija)
        total_value = float(MARGIN_USDT) * float(LEVERAGE)
        raw_amount = total_value / entry_price
        
        # Sužinome biržos žingsnį (lot size). BTC dažniausiai yra 0.0001
        min_qty = float(market['limits']['amount']['min'])
        
        # Nukertame skaičių po kablelio žemyn (floor), kad neviršytume biudžeto
        # Pvz., jei reikia 0.004347, suapvaliname iki 0.0043
        step_decimals = int(abs(math.log10(min_qty))) if min_qty < 1 else 0
        factor = 10 ** step_decimals
        final_qty = math.floor(raw_amount * factor) / factor
        
        # Saugiklis: jei suapvalintas kiekis mažesnis už biržos minimumą, imam minimumą
        amount = max(final_qty, min_qty)
        
        # Galutinis patikrinimas per CCXT saugumo funkciją
        amount = float(exchange.amount_to_precision(SYMBOL, amount))

        print(f"--- VYKDYMAS ---")
        print(f"Esama kaina: {entry_price} USDT")
        print(f"Skaičiuojama marža: {MARGIN_USDT} USDT (Svertas: {LEVERAGE}x)")
        print(f"Bendra užsakymo vertė rinkoje: {amount * entry_price} USDT")
        print(f"Perkamas kiekis: {amount} BTC")

        # 3. Sverto ir maržos režimo nustatymas
        try:
            exchange.set_leverage(int(LEVERAGE), SYMBOL, {'marginMode': 'isolated'})
        except Exception as leverage_err:
            # Dažnai meta klaidą, jei svertas jau nustatytas – tiesiog ignoruojame
            pass

        # 4. Užsakymo siuntimas į MEXC
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params={
                'posSide': 'SHORT',
                'openType': 1,
                'leverage': int(LEVERAGE)
            }
        )

        print(f"Short sėkmingai atidarytas! ID: {order['id']}")
        return {"status": "success", "order_id": order['id'], "amount": amount}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
