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
DEFAULT_LEVERAGE = 25  # Tavo norimas svertas
MARGIN_USDT = 2.0 

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
        
        # Pasiimame veiksmą (priimame long arba short)
        action = str(data.get('action', '')).lower()
        if action not in ['long', 'short']:
            return f"Ignored (Invalid action: {action})", 200

        # Dinaminis monetos pavadinimas
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            print("Klaida: Žinutėje negautas 'ticker' kintamasis")
            return {"error": "Missing ticker in request"}, 400

        # Išvalome .P, brūkšnius ir USDT galūnes, kad gautume tikrąjį MEXC formatą
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        symbol = f"{clean_ticker}/USDT:USDT"

        # 1. Rinkos duomenys konkrečiai monetai
        markets = exchange.load_markets()
        if symbol not in markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        ticker = exchange.fetch_ticker(symbol)
        
        # Pariekiame parametrus priklausomai nuo krypties
        # Kadangi tai MARKET orderis, entry_price naudojame tik matematiniam SL/TP skaičiavimui
        if action == 'long':
            entry_price = float(ticker['bid'])
            side = 'buy'
            pos_side = 'LONG'
            pos_mode = 1
        else:  # short
            entry_price = float(ticker['ask'])
            side = 'sell'
            pos_side = 'SHORT'
            pos_mode = 2
        
        # --- SVERTO DINAMINIS PATIKRINIMAS ---
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"Monetai {symbol} ({pos_side}) taikomas svertas: {final_leverage}x (Maksimalus biržos limitas: {max_leverage}x)")

        # 2. Saugus dinaminio SL ir TP skaičiavimas
        raw_sl = data.get('sl_price')
        sl_price = None
        
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        # 3. Matematika abiem kryptims (0.8% pelno fiksavimas)
        if action == 'long':
            tp_price = entry_price * 1.008
            if sl_price is None or sl_price >= entry_price:
                sl_price = entry_price * 0.99  # Atsarginis SL (1%), jei indikatorius klaidingas
            # Pusės atstumo taisyklė tavo SL korekcijai
            sl_price = entry_price - ((entry_price - sl_price) / 2)
        else:  # short
            tp_price = entry_price * 0.992
            if sl_price is None or sl_price <= entry_price:
                sl_price = entry_price * 1.01  # Atsarginis SL (1%), jei indikatorius klaidingas
            # Pusės atstumo taisyklė tavo SL korekcijai
            sl_price = entry_price + ((sl_price - entry_price) / 2)

        # Suapvaliname kainas pagal biržos taisykles
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # 4. Kiekio skaičiavimas naudojant dinamiškai parinktą svertą
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # 5. Svertas ir režimas nustatomas šiai monetai
        try:
            exchange.set_leverage(int(final_leverage), symbol, {
                'openType': 1,  # Isolated pozicija      
                'positionType': pos_mode
            })
        except Exception as lev_err:
            print(f"Sverto nustatymo praleidimas (galbūt jau nustatyta): {lev_err}")

        # 6. Užsakymo parametrų paruošimas MARKET sandoriui
        params = {
            'posSide': pos_side,
            'openType': 1,
            'leverage': int(final_leverage),
            'stopLossPrice': sl_price,      
            'takeProfitPrice': tp_price,    
            'tpPrice': tp_price,            
            'slPrice': sl_price,            
            'priceWay': 1
        }

        # 7. Vykdome užsakymą biržoje kaip MARKET
        order = exchange.create_order(
            symbol=symbol,
            type='market',  # PAKEISTA IŠ LIMIT Į MARKET
            side=side,
            amount=amount,
            price=None,     # MARKET orderiui kainos perduoti nereikia
            params=params
        )

        print(f"{pos_side} MARKET sandoris sėkmingai įvykdytas! Moneta: {symbol} | Svertas: {final_leverage}x | ID: {order['id']} | Orientacinė įėjimo kaina: {entry_price} | SL: {sl_price} | TP: {tp_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
