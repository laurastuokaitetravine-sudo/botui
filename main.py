import os
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
        
        # 2. Dinaminis SL ir TP (1:2) skaičiavimas
        sl_price = data.get('sl_price')
        tp_price = None
        
        if sl_price:
            sl_price = float(sl_price)
            # Apskaičiuojame atstumą nuo įėjimo kainos iki Stop Loss
            risk_distance = sl_price - entry_price
            
            # Jei indikatorius atsiuntė logišką SL SHORT pozicijai (SL turi būti aukščiau kainos)
            if risk_distance > 0:
                # Take Profit bus 2 kartus toliau į apačią nei Stop Loss
                tp_price = entry_price - (risk_distance * 2)
            else:
                # Atsarginis variantas, jei indikatoriaus SL kaina buvo netiksli (pvz., lygiai lygi rinkos kainai)
                sl_price = entry_price * 1.01
                tp_price = entry_price * 0.98
        else:
            # Jei TradingView išvis neatsiuntė SL, naudojame standartinį 1% SL ir 2% TP fiksavimą
            sl_price = entry_price * 1.01
            tp_price = entry_price * 0.98

        # Suapvaliname kainas pagal biržos reikalavimus
        sl_price = float(exchange.price_to_precision(SYMBOL, sl_price))
        tp_price = float(exchange.price_to_precision(SYMBOL, tp_price))

        # 3. Kiekio skaičiavimas pritaikytas MEXC kontraktams
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

        # 5. Užsakymo parametrų paruošimas
        params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(LEVERAGE),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price
        }

        # 6. Atidarome SHORT su automatinėmis apsaugomis
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params=params
        )

        print(f"Short atidarytas: {order['id']} | Įėjimas: {entry_price} | Indikatoriaus SL: {sl_price} | Boto paskaičiuotas TP (1:2): {tp_price}")
        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
