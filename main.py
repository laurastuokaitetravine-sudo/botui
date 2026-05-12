import os
import traceback
import math
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('mx0vglT4ta6TAGvGsZ'),
    'secret': os.getenv('6b588e8b0da64ff8b28c6b798d357434'),
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 25.0 
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

        # 1. Užkrauname rinkos duomenis
        markets = exchange.load_markets()
        market = markets[SYMBOL]
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])
        
        # 2. KIEKIO SKAIČIAVIMAS (Sutvarkytas „Normaliai“)
        # Paskaičiuojame, kiek kontraktų išeina už 25 USDT su 25x svertu
        total_value = MARGIN_USDT * LEVERAGE
        raw_amount = total_value / entry_price
        
        # Tikriname minimalų biržos reikalavimą
        min_qty = float(market['limits']['amount']['min'])
        
        # Sprendimas: Imam didesnį iš dviejų ir suapvaliname į viršų (math.ceil)
        # Tai garantuoja, kad kiekis bus bent minimalus leidžiamas (pvz., 1.0)
        final_qty = max(raw_amount, min_qty)
        
        # Suformatuojame pagal biržos tikslumą (kad nebūtų per daug skaičių po kablelio)
        amount = float(exchange.amount_to_precision(SYMBOL, final_qty))

        print(f"Signal_Price: {entry_price} | Calc_Amount: {amount}")

        # 3. SVERTO NUSTATYMAS
        try:
            exchange.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'isolated'})
        except:
            pass # Jei jau nustatyta, ignoruojame

        # 4. ORDERIO VYKDYMAS
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params={
                'posSide': 'SHORT',
                'openType': 1  # Isolated
            }
        )

        print(f"SĖKMĖ: Short atidarytas! ID: {order['id']}")
        return {"status": "success", "amount": amount}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
