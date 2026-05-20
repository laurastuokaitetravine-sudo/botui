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
MARGIN_USDT = 5.0 

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
        
        # Saugiai pasiimame veiksmą ir priimame TIK short signalus
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        # --- DALYKAS 1: DINAMINIS MONETOS PAVADINIMAS ---
        # Pasiimame monetą iš TradingView (pvz., "BTCUSDT" arba "SOLUSDT")
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            print("Klaida: Žinutėje negautas 'ticker' kintamasis")
            return {"error": "Missing ticker in request"}, 400

        # Konvertuojame TradingView formatą į MEXC Futures formatą (pvz., "SOL/USDT:USDT")
        clean_ticker = tv_ticker.replace(".P", "").replace("USDT", "")
        symbol = f"{clean_ticker}/USDT:USDT"

        # 1. Rinkos duomenys konkrečiai monetai
        markets = exchange.load_markets()
        if symbol not in markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        ticker = exchange.fetch_ticker(symbol)
        entry_price = float(ticker['last'])
        
        # 2. Saugus dinaminio SL ir TP (1:2) nurašymas
        raw_sl = data.get('sl_price')
        sl_price = None
        tp_price = None
        
        # Tikriname, ar gautas SL yra realus skaičius
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        # 3. IŠVALYTA MATEMATIKA: Tik SHORT pozicijos 1:2 skaičiavimas
        if sl_price and sl_price > entry_price:
            risk_distance = sl_price - entry_price
            tp_price = entry_price - (risk_distance * 1)

        # ATSARGINIS PLANAS: Jei SL nerastas arba buvo klaidingas
        if sl_price is None or tp_price is None:
            sl_price = entry_price * 1.01
            tp_price = entry_price * 0.98

        # Suapvaliname kainas pagal konkrečios monetos MEXC taisykles
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # 4. Kiekio skaičiavimas pritaikytas konkrečios monetos fjučerių kontraktams
        total_value = MARGIN_USDT * LEVERAGE
        raw_crypto_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # 5. Svertas (Isolated režimas nustatomas šiai monetai)
        try:
            exchange.set_leverage(int(LEVERAGE), symbol, {'marginMode': 'isolated'})
        except:
            pass

        # 6. Užsakymo parametrų paruošimas (Fiksuota TIK SHORT pozicijai)
        params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(LEVERAGE),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price
        }

        # 7. Vykdome užsakymą biržoje
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=amount,
            params=params
        )

        print(f"SHORT sėkmingai atidarytas! Moneta: {symbol} | ID: {order['id']} | Įėjimas: {entry_price} | SL: {sl_price} | TP (1:2): {tp_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
