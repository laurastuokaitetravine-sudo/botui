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
        'defaultType': 'swap',                          # Kablelis čia yra privalomas
        'createMarketBuyOrderRequiresPrice': False      # Perkelta į 'options' vidų
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
        
        # 2. Kiekio skaičiavimas (PATAISYTAS)
        total_value = MARGIN_USDT * LEVERAGE  # Pav. 25 * 25 = 625 USDT vertė
        raw_btc_amount = total_value / entry_price  # Kiekis tikrais BTC vienetais
        
        # Gauname MEXC vieno kontrakto dydį (BTC/USDT dažniausiai yra 0.0001)
        contract_size = float(market.get('contractSize', 1.0))
        
        # Paverčiame BTC kiekį į kontraktų (lotų) skaičių
        contracts_qty = raw_btc_amount / contract_size
        
        # Tikriname minimalų biržos leidžiamą kontraktų kiekį
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        # Suapvaliname pagal MEXC reikalavimus (kontraktai dažniausiai apvalinami iki sveikų skaičių)
        amount = float(exchange.amount_to_precision(SYMBOL, final_contracts))


        # 3. Svertas (Isolated režimas)
        try:
            exchange.set_leverage(int(LEVERAGE), SYMBOL, {'marginMode': 'isolated'})
        except:
            pass

        # 4. Atidarome SHORT (Sutvarkyta pagal paskutinę klaidą)
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=amount,
            params={
                'posSide': 'SHORT',
                'openType': 1,
                'leverage': int(LEVERAGE) # Čia buvo pagrindinė klaida
            }
        )

        print(f"Short atidarytas: {order['id']}")
        return {"status": "success", "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
