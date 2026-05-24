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
MARGIN_USDT = 10.0 

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

        # 2. Saugus dinaminio SL nuskaitymas
        raw_sl = data.get('sl_price')
        sl_price = None
        
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        # 3. DINAMINIS TP (pelno didinimas) + koreguotas SL
        # Bazinis TP ~0.8%, bet didėja pagal rinkos impulsą
        # Impulsą matuojame pagal spread'ą / kainą (paprastas momentumo proxy)
        try:
            price_change = abs(ticker['bid'] - ticker['ask']) / entry_price
        except Exception:
            price_change = 0.0

        base_tp = 0.008  # 0.8%

        if price_change > 0.002:
            tp_multiplier = 2.0   # iki ~1.6%
        elif price_change > 0.001:
            tp_multiplier = 1.5   # ~1.2%
        else:
            tp_multiplier = 1.0   # ~0.8%

        if action == 'long':
            # TP dinamiškai didinamas
            tp_price = entry_price * (1 + base_tp * tp_multiplier)

            # Jei SL neateina arba blogas – atsarginis 0.8% SL (geresnis R)
            if sl_price is None or sl_price >= entry_price:
                sl_price = entry_price * 0.992  # ~0.8% žemiau

            # Pusės atstumo taisyklė
            sl_price = entry_price - ((entry_price - sl_price) / 2)
        else:
            # SHORT: TP žemiau, dinamiškai didinamas
            tp_price = entry_price * (1 - base_tp * tp_multiplier)

            # Jei SL neateina arba blogas – atsarginis 0.8% virš
            if sl_price is None or sl_price <= entry_price:
                sl_price = entry_price * 1.008  # ~0.8% aukščiau

            # Pusės atstumo taisyklė
            sl_price = entry_price + ((sl_price - entry_price) / 2)

        # Suapvaliname kainas pagal biržos taisykles
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # 4. Kiekio skaičiavimas naudojant dinamiškai parinktą svertą
        total_value = MARGIN_US
