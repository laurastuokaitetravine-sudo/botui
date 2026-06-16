import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# ============================================================
# EXCHANGE KONFIGŪRACIJA (SU PROXY IR OPTIMIZUOTA ATMINTIMI)
# ============================================================
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})

# Svarbus optimizavimas: užkrauname rinkas tik kartą, kad serveris „nepavargtų“ po kelių dienų
try:
    print("Kraunami MEXC Futures rinkos duomenys...")
    exchange.load_markets()
    print("Rinkos sėkmingai užkrautos!")
except Exception as e:
    print(f"Įspėjimas: Nepavyko užkrauti rinkų starto metu: {e}")

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 7
MARGIN_USDT = 10.0

@app.route('/')
def home():
    return "BOTAS ONLINE (STABILUS GYVAS LIMIT ENTRY + SL IŠ PLOT SU PROXY)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            try:
                raw_data = request.data.decode('utf-8').strip()
                data = json.loads(raw_data)
            except Exception as json_err:
                print(f"Nepavyko konvertuoti teksto į JSON: {json_err}")
                return {"error": "Invalid JSON format"}, 400

        print(f"GAUTI DUOMENYS IŠ TRADINGVIEW: {data}")

        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            print("Klaida: Žinutėje negautas 'ticker' kintamasis")
            return {"error": "Missing ticker in request"}, 400

        # Universali monetų tvarkymo logika
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"

        symbol = f"{clean_ticker}/USDT:USDT"

        # Jei po kelių dienų atmintis išsivalė, užkrauname saugiai iš naujo
        if not exchange.markets:
            exchange.load_markets()

        if symbol not in exchange.markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = exchange.markets[symbol]

        # Paimame gyvą ASK kainą iš biržos LIMIT orderiui per proxy
        ticker = exchange.fetch_ticker(symbol)
        entry_price = float(ticker['ask'])

        # --- DUOMENŲ SKAITYMAS TIESIAI IŠ TAVO KODO PLOTO ---
        sl_raw = data.get('sl_price')

        if sl_raw and str(sl_raw).strip().lower() not in ['nan', 'na', 'null', '']:
            sl_price = float(sl_raw)
        else:
            print(f"KLAIDA: Iš kodo plot gautas tuščias arba sugadintas SL: '{sl_raw}'. Orderis stabdomas.")
            return {"error": "Stabdoma: Nerasta SL reikšmė iš plot"}, 400

        # Suapvaliname kainas pagal tikslias biržos taisykles
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))

        # Sverto tikrinimas
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"Monetai {symbol} taikomas svertas: {final_leverage}x")

        # --- KIEKIO SKAČIAVIMAS ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))

        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])
        total_contracts = max(total_contracts, min_contracts)

        final_amount = float(exchange.amount_to_precision(symbol, total_contracts))

        pos_mode = 2  # SHORT fiksuotas
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # 1) BASE LIMIT SHORT ENTRY ORDER
        entry_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'timeInForce': 'PostOnly'
        }

        entry_order = exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=final_amount,
            price=entry_price,
            params=entry_params
        )
        print(f"SHORT LIMIT pastatytas! Kiekis: {final_amount} (100%) | Gyva Biržos Kaina: {entry_price}")

        # 2) ATSKIRAS STOP LOSS TRIGGER MARKET ORDERIS
        sl_params = {
            'openType': 1,
            'stopPrice': sl_price,
            'triggerPrice': sl_price,
            'posSide': 'SHORT',
            'reduceOnly': True,
            'type': 5
        }

        sl_order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='buy',
            amount=final_amount,
            params=sl_params
        )
        print(f"[SL TRIGGER] STOP MARKET pastatytas už kodo plot kainą: {sl_price}")

        return {
            "status": "success", 
            "symbol": symbol, 
            "entry_id": entry_order['id'],
            "sl_id": sl_order['id']
        }, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
