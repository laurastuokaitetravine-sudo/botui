import os
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
# API raktus įrašyk į Render/Railway Environment Variables!
exchange = ccxt.mexc({
    'apiKey': os.getenv('mx0vglT4ta6TAGvGsZ'),
    'secret': os.getenv('6b588e8b0da64ff8b28c6b798d357434'),
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 25 
SYMBOL = 'BTC/USDT:USDT'

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data or data.get('passphrase') != MY_PASSWORD:
            print("KLAIDA: Neteisingas slaptažodis")
            return "Unauthorized", 403
        
        if str(data.get('action')).lower() != 'short':
            return "Ignored: Not a short signal", 200

        print(f"Gautas signalas: {data}")

        # 1. Rinkos duomenys
        markets = exchange.load_markets()
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])
        
        # 2. Kiekio skaičiavimas (Svarbu!)
        # Paskaičiuojame bendrą pozicijos vertę su svertu
        total_position_value = MARGIN_USDT * LEVERAGE
        raw_amount = total_position_value / entry_price
        
        # Naudojame exchange funkciją suapvalinimui
        amount_str = exchange.amount_to_precision(SYMBOL, raw_amount)
        amount = float(amount_str)

        # PATIKRA: MEXC reikalauja bent 1 kontrakto. 
        # Jei paskaičiuotas kiekis per mažas, jį padidiname iki minimalaus.
        min_amount = float(markets[SYMBOL]['limits']['amount']['min'])
        if amount < min_amount:
            print(f"Kiekis {amount} per mažas. Nustatomas minimalus: {min_amount}")
            amount = min_amount

        # 3. Svertas (Isolated)
        try:
            exchange.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'isolated'})
        except Exception as e:
            print(f"Svertas jau nustatytas: {e}")

        # 4. Atidarome SHORT (Market)
        print(f"Vykdomas SHORT. Simbolis: {SYMBOL}, Kiekis: {amount}")
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params={
                'posSide': 'SHORT',
                'openType': 1  # 1 reiškia Isolated
            }
        )

        print(f"SĖKMĖ: Short atidarytas! ID: {order['id']}")
        return {"status": "success", "order_id": order['id'], "amount": amount}, 200

    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"KLAIDA VYKDYME: {error_msg}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
