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
    return "BOTAS ONLINE (STABILUS INTEGRUOTAS LIMIT ENTRY + SL SU PROXY)", 200

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

        # ========================================================
        # UNIVERSALI MONETŲ IR AKCIJŲ TVARKYMO LOGIKA
        # ========================================================
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "").strip()
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
        elif clean_ticker == "GEV":
            clean_ticker = "GEVSTOCK"
        elif clean_ticker == "AAOI":
            clean_ticker = "AAOISTOCK"

        symbol = f"{clean_ticker}/USDT:USDT"

        if not exchange.markets:
            exchange.load_markets()

        # AUTOMATINIS AKCIJŲ SAUGIKLIS
        if symbol not in exchange.markets:
            alternative_ticker = f"{clean_ticker}STOCK"
            alternative_symbol = f"{alternative_ticker}/USDT:USDT"
            if alternative_symbol in exchange.markets:
                symbol = alternative_symbol
                print(f"Automatiškai pritaikyta akcijų galūnė: {symbol}")

        if symbol not in exchange.markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = exchange.markets[symbol]

        # 🟢 TAVO NORIMAS EP BŪDAS: Paimame gyvą ASK kainą iš biržos LIMIT orderiui
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

        pos_mode = 2  # SHORT One-Way / isolated fiksuotas
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # ========================================================
        # ONE-CLICK INTEGRUOTAS LIMIT ORDERIS (ENTRY + SL PARAMETRUOSE)
        # ========================================================
        entry_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'marginMode': 'isolated',
            'positionType': 2,                  # Nurodomas SHORT pozicijos tipas
            'leverage': int(final_leverage),     # Įrašytas privalomas svertas biržai
            'stopLossPrice': sl_price,          # 🟢 Įrašome tavo SL tiesiai į pagrindinį užsakymą!
            'timeInForce': 'PostOnly'
        }

        entry_order = exchange.create_order(
            symbol=symbol,
            type='limit',                       # Tavo norimas LIMIT įėjimas
            side='sell',
            amount=final_amount,
            price=entry_price,
            params=entry_params
        )
        print(f"SHORT LIMIT pastatytas su integruotu SL! Kiekis: {final_amount} | Kaina: {entry_price} | SL iš kodo plot: {sl_price}")

        return {
            "status": "success", 
            "symbol": symbol, 
            "entry_id": entry_order['id']
        }, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
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

# Užkrauname rinkas tik kartą starto metu
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
    return "BOTAS ONLINE (STABILUS GYVAS LIMIT ENTRY + FIX SL SU PROXY)", 200

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

        # ========================================================
        # UNIVERSALI MONETŲ IR AKCIJŲ TVARKYMO LOGIKA
        # ========================================================
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "").strip()
        
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
        elif clean_ticker == "GEV":
            clean_ticker = "GEVSTOCK"
        elif clean_ticker == "AAOI":
            clean_ticker = "AAOISTOCK"

        symbol = f"{clean_ticker}/USDT:USDT"

        if not exchange.markets:
            exchange.load_markets()

        # AUTOMATINIS AKCIJŲ SAUGIKLIS
        if symbol not in exchange.markets:
            alternative_ticker = f"{clean_ticker}STOCK"
            alternative_symbol = f"{alternative_ticker}/USDT:USDT"
            if alternative_symbol in exchange.markets:
                symbol = alternative_symbol
                print(f"Automatiškai pritaikyta akcijų galūnė: {symbol}")

        if symbol not in exchange.markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = exchange.markets[symbol]

        # ========================================================
        # SAUGUS KAINOS GAVIMAS SU ATSARGINIAIS VARIANTAS
        # ========================================================
        ticker = exchange.fetch_ticker(symbol)
        raw_price = ticker.get('markPrice') or ticker.get('ask') or ticker.get('last') or ticker.get('close')
        
        if raw_price is None:
            print(f"KLAIDA: Nepavyko gauti jokios kainos monetai {symbol} iš MEXC API.")
            return {"error": "Nepavyko gauti biržos kainos"}, 400
            
        entry_price = float(raw_price)
        entry_price = float(exchange.price_to_precision(symbol, entry_price))

        # ========================================================
        # STOP LOSS IŠ TAVO INDIKATORIAUS (SAUGUS BLOKAS)
        # ========================================================
        sl_raw = data.get('sl_price')

        if sl_raw is None:
            print("KLAIDA: indikatorius atsiuntė SL = None (plot dar nespėjo apskaičiuoti)")
            return {"error": "SL iš indikatoriaus negautas"}, 400

        sl_raw_str = str(sl_raw).strip().lower()
        if sl_raw_str in ["", "na", "nan", "null"]:
            print(f"KLAIDA: indikatorius atsiuntė sugadintą SL: {sl_raw}")
            return {"error": "SL iš indikatoriaus yra neteisingas"}, 400

        sl_price = float(sl_raw)
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

        pos_mode = 2  # SHORT One-Way / isolated fiksuotas
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # ========================================================
        # 1) BASE LIMIT SHORT ENTRY ORDER (PostOnly)
        # ========================================================
        entry_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'marginMode': 'isolated',
            'leverage': int(final_leverage),
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
        print(f"SHORT LIMIT pastatytas! Kiekis: {final_amount} | Kaina: {entry_price}")

        # ========================================================
        # 2) ATSKIRAS STOP LOSS TRIGGER MARKET ORDERIS (PATAISYTAS!)
        # ========================================================
        sl_params = {
            'openType': 1,
            'marginMode': 'isolated',
            'leverage': int(final_leverage),
            'stopPrice': sl_price,
            'triggerPrice': sl_price,
            'posSide': 'SHORT',
            'type': 5  # 🔴 5 praneša MEXC Futures, kad tai sąlyginis Rinkos užsakymas
        }

        sl_order = exchange.create_order(
            symbol=symbol,
            type='market',  # 🔴 Pataisyta: bazinis užsakymo tipas pakeistas į 'market'
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
