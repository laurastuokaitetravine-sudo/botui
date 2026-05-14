import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Naudojant Render Environment Variables) ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 10.0 
SYMBOL = 'BTC/USDT:USDT'

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Priverstinai nuskaitome JSON duomenis
        data = request.get_json(force=True, silent=True)
        
        if not data:
            try:
                raw_data = request.data.decode('utf-8').strip()
                data = json.loads(raw_data)
            except Exception as json_err:
                print(f"Nepavyko konvertuoti teksto į JSON: {json_err}")
                return {"error": "Invalid JSON format"}, 400

        # Slaptažodžio patikrinimas
        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        # Tikriname, ar gautas SHORT signalas
        if str(data.get('action')).lower() != 'short':
            return "Ignored", 200

        # 1. Rinkos duomenys
        markets = exchange.load_markets()
        market = markets[SYMBOL]
        ticker = exchange.fetch_ticker(SYMBOL)
        entry_price = float(ticker['last'])
        
        # 2. Saugus dinaminio SL ir TP (1:2) nurašymas
        raw_sl = data.get('sl_price')
        sl_price = None
        tp_price = None
        
        # Tikriname, ar gautas SL yra realus skaičius (ne NaN, ne na, ne tuščias)
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
                risk_distance = sl_price - entry_price
                
                if risk_distance > 0:
                    # Sėkmingas 1:2 skaičiavimas pagal indikatoriaus SL
                    tp_price = entry_price - (risk_distance * 2)
                else:
                    # Saugiklis, jei SL kaina gauta mažesnė už rinkos kainą
                    sl_price = entry_price * 1.01
                    tp_price = entry_price * 0.98
            except ValueError:
                # Jei teksto nepavyko paversti į skaičių
                sl_price = None

        # Jei SL nerastas arba buvo klaidingas, naudojame standartinį 1% SL ir 2% TP
        if sl_price is na or tp_price is na:
            sl_price = entry_price * 1.01
            tp_price = entry_price * 0.98

        # Suapvaliname kainas pagal MEXC taisykles
        sl_price = float(exchange.price_to_precision(SYMBOL, sl_price))
        tp_price = float(exchange.price_to_precision(SYMBOL, tp_price))

        # 3. Kiekio skaičiavimas pritaikytas MEXC fjučerių kontraktams
        total_value = MARGIN_USDT * LEVERAGE
        raw_btc_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_btc_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(SYMBOL, final_contracts))

        # 4. Svertas (Isolated režimas)
        try:
            exchange.set_leverage(int(LEVERAGE), SYMBOL, {'marginMode': 'isolated'})
        except:
            pass

        # 5. Užsakymo parametrų paruošimas su biržos TP/SL
        params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(LEVERAGE),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price
        }

        # 6. Vykdome užsakymą biržoje
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params=params
        )

        print(f"SHORT sėkmingai atidarytas! ID: {order['id']} | Įėjimas: {entry_price} | SL: {sl_price} | TP (1:2): {tp_price}")
        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
