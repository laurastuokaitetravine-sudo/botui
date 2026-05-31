import os
import json
import time
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- GRIEŽTA MEXC KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',                 # Naudojame Futures (USDT-M)
        'createMarketBuyOrderRequiresPrice': False,
        'defaultMarket': 'swap',
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 10
MARGIN_USDT = 1.0  # Jūsų nustatyta 5 USDT marža

@app.route('/')
def home():
    return "BOTAS ONLINE (SUTVARKYTA VIENO ŽINGSNIO STRUKTŪRA)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # --- JSON NUSKAITYMAS ---
        raw_data = request.data.decode('utf-8').strip()
        try:
            data = json.loads(raw_data)
        except Exception as je:
            print(f"KRITINĖ KLAIDA: Sugadintas JSON formatas! Tekstas: {raw_data}")
            return {"error": "Invalid JSON format"}, 400

        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        if str(data.get('action', '')).lower() != 'short':
            return "Ignored (SHORT only mode)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        # --- 1. SAUGUS MONETŲ PAVADINIMŲ VALYMAS (ĮSKAITANT PEPE) ---
        clean = tv_ticker.upper().replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean == "PEPE":
            clean = "10000PEPE"
            
        symbol = f"{clean}/USDT:USDT"

        # Užkrauname rinkas iš anksto
        markets = exchange.load_markets()
        if symbol not in markets:
            print(f"KLAIDA: Moneta {symbol} nerasta MEXC fjučeriuose.")
            return {"error": f"Symbol {symbol} not found"}, 400

        market = markets[symbol]

        # --- 2. TINKLO KLAIDŲ APSAUGA KAINAI GAUTI ---
        ticker = None
        for _ in range(3):
            try:
                ticker = exchange.fetch_ticker(symbol)
                break
            except ccxt.NetworkError as ne:
                print(f"Laikinai nepavyko pasiekti MEXC, bandoma vėl... {ne}")
                time.sleep(1)
        
        if not ticker:
            return {"error": "Nepavyko gauti kainos iš MEXC dėl tinklo sutrikimų."}, 502

        entry_price = float(ticker['ask'])

        # Sverto limitų patikra
        max_exchange_leverage = DEFAULT_LEVERAGE  
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_exchange_leverage = float(market['limits']['leverage']['max'])

        active_leverage = int(min(DEFAULT_LEVERAGE, max_exchange_leverage))

        # Nustatome svertą biržoje
        try:
            exchange.set_leverage(active_leverage, symbol, {
                'openType': 1,   # Isolated
                'positionType': 2 # Short
            })
        except:
            pass

        # --- 3. SL IR TP NUSKAITYMAS ---
        def safe_float(v):
            return float(v) if v not in [None, "", "null", "nan", "na", "NaN"] else None

        sl_price = safe_float(data.get('sl_price'))
        
        # Pasiimame TP1 kaip pagrindinį tikslą. Jei jo nėra – naudojame jūsų procentinį 0.8% fallback
        tp_price = safe_float(data.get('tp1_price'))
        if tp_price is None:
            tp_price = entry_price * 0.992

        if sl_price is None or sl_price <= entry_price:
            sl_price = entry_price * 1.01

        # Suapvaliname pagal biržos taisykles (Griežtai būtina)
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price    = float(exchange.price_to_precision(symbol, sl_price))
        tp_price    = float(exchange.price_to_precision(symbol, tp_price))

        # --- 4. KIEKIO SKAIČIAVIMAS IR APVALINIMAS Į SVEIKĄJĮ SKAIČIŲ (PENGU pataisymas) ---
        total_value = MARGIN_USDT * active_leverage
        raw_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_amount / contract_size

        # Priverstinai paverčiame į sveikąjį skaičių (int), nes MEXC nepriima kablelių kontraktų kiekyje
        amount = int(float(exchange.amount_to_precision(symbol, contracts_qty)))
        min_amount = int(float(market['limits']['amount']['min'])) if market['limits']['amount']['min'] is not None else 1
        
        if amount < min_amount:
            amount = min_amount

        # --- 5. VIENO ŽINGSNIO UŽSAKYMAS SU VISISKAIS PARAMETRAIS (Ištaiso leverage klaidą) ---
        params = {
            'posSide': 'SHORT',
            'openType': 1,                      # Isolated
            'leverage': int(active_leverage),   # Paduodame svertą tiesiai į užsakymą fjučerių API reikalavimui
            'stopLossPrice': sl_price,          # Prisegtas SL
            'takeProfitPrice': tp_price,        # Prisegtas TP
            'slPrice': sl_price,                # Papildomi dubliuojantys laukai CCXT suderinamumui
            'tpPrice': tp_price,
            'priceWay': 1                       # 1 = Triggeriuojama pagal Mark Price
        }

        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=amount,
            price=None,
            params=params
        )

        print(f"SHORT MARKET sandoris sėkmingai įvykdytas! | {symbol} | Qty={amount} | SL={sl_price} | TP={tp_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KRITINĖ KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
