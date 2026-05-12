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
# Naudok 25 USDT, kad užtektų minimaliam BTC kontraktui
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
            print("KLAIDA: Neteisingas slaptažodis")
            return "Unauthorized", 403
        
        if str(data.get('action')).lower() != 'short':
            return "Ignored: Not a short signal", 200

        print(f"Gautas signalas: {data}")

        # 1. Rinkos duomenys
        markets = exchange.load_markets()
        market = markets[SYMBOL]
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])
        
        # 2. Saugus kiekio skaičiavimas
        total_value = MARGIN_USDT * LEVERAGE
        raw_amount = total_value / entry_price
        
        # Gauname minimalų kiekį (BTC tai dažniausiai 1.0 kontraktas)
        min_qty = float(market['limits']['amount']['min'])
        
        # Imam didesnį iš skaičiuoto arba minimalaus
        final_qty = max(raw_amount, min_qty)
        
        # Suapvaliname pagal biržos reikalavimus
        amount = float(exchange.amount_to_precision(SYMBOL, final_qty))

        print(f"Bandomas atidaryti Short: {amount} | Kaina: {entry_price}")

        # 3. Svertas (Svarbu nustatyti prieš orderį)
        try:
            exchange.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'isolated'})
        except Exception as e:
            print(f"Svertas jau nustatytas: {e}")

        # 4. Atidarome SHORT (Market Order)
        # Pridėtas 'leverage' parametras į params, kaip reikalauja MEXC Isolated režimui
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params={
                'posSide': 'SHORT',
                'openType': 1,      # 1 = Isolated
                'leverage': LEVERAGE # PRIVALOMA MEXC ISOLATED REŽIMUI
            }
        )

        print(f"SĖKMĖ! Short ID: {order['id']}")
        return {"status": "success", "order_id": order['id'], "amount": amount}, 200

    except Exception as e:
        error_full = traceback.format_exc()
        print(f"VYKDYMO KLAIDA: {error_full}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
