import os
import json
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- SUTVARKYTA MEXC KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,  # Apsaugo nuo IP blokavimo
    'options': {
        'defaultType': 'swap',  # Automatiškai parenka Futures URL
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 25.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (TIK SHORT REŽIMAS)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Nuskaitymas ir JSON validacija
        data = request.get_json(force=True, silent=True)
        if not data:
            raw_data = request.data.decode('utf-8').strip()
            data = json.loads(raw_data)

        # Saugumo patikra
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        # Veiksmo validacija (Priimame TIK short)
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return f"Ignored (Bot is in SHORT-ONLY mode. Received: {action})", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker in request"}, 400

        # --- 1. SIMBOLIO VALYMAS IR FORMATAVIMAS ---
        clean_ticker = str(tv_ticker).upper().replace(".P", "").replace("_", "").replace("-", "")
        if clean_ticker.endswith("USDT"):
            clean_ticker = clean_ticker[:-4]
        
        symbol = f"{clean_ticker}/USDT:USDT"
        print(f"Gautas iš TradingView: {tv_ticker} -> Suformatuotas CCXT simbolis: {symbol}")

        # Užkrauname rinkas iš biržos
        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC Futures"}, 400

        market = markets[symbol]

        # --- 2. STABILUS KAINOS GAVIMAS PER ORDER BOOK ---
        order_book = None
        for _ in range(3):
            try:
                order_book = exchange.fetch_order_book(symbol, limit=5)
                break
            except ccxt.NetworkError as ne:
                print(f"Laikinai nepavyko pasiekti MEXC, bandoma vėl... Klaida: {ne}")
                exchange.sleep(1000)
        
        if not order_book or not order_book['asks']:
            return {"error": "Nepavyko gauti Order Book kainos iš MEXC."}, 502

        # TIK SHORT: Naudojama geriausia pardavimo kaina (Asks)
        entry_price = float(order_book['asks'][0][0])
        side = 'sell'
        pos_side = 'SHORT'
        pos_mode = 2
        
        # Maksimalaus leistino sverto patikra
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)

        # Nustatome sverto dydį biržoje
        try:
            exchange.set_leverage(int(final_leverage), symbol, {
                'openType': 1, # Isolated
                'positionType': pos_mode
            })
        except Exception as e:
            print(f"Sverto nustatymo pranešimas: {e}")

        # SL kainos nuskaitymas iš TradingView
        raw_sl = data.get('sl_price')
        sl_price = None
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        # --- 3. TIK SHORT TP/SL MATEMATINĖ LOGIKA ---
        tp_price = entry_price * 0.990  # Pelno fiksavimas ties -0.8% krentant žemyn
        
        if sl_price is None or sl_price <= entry_price:
            sl_price = entry_price * 1.01  # Numatytasis SL +1%
            
        # Sumažiname SL riziką per pusę pagal jūsų buvusią logiką
        sl_price = entry_price + ((sl_price - entry_price) / 2)

        # Kainų pritaikymas biržos tikslumui
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # --- 4. KIEKIO SKAIČIAVIMAS IR APVALINIMAS ---
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        amount = float(exchange.amount_to_precision(symbol, contracts_qty))
        min_contracts = float(market['limits']['amount']['min'])
        if amount < min_contracts:
            amount = min_contracts

        # Užsakymo parametrai MEXC Futures platformai
        params = {
            'posSide': pos_side,
            'openType': 1, # Isolated
            'leverage': int(final_leverage),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp_price,
            'tpPrice': tp_price,
            'slPrice': sl_price,
            'priceWay': 1
        }

        # Pozicijos atidarymas
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=side,
            amount=amount,
            price=None,
            params=params
        )

        print(f"SHORT MARKET sandoris įvykdytas | {symbol} | Kiekis: {amount} | SL={sl_price} | TP={tp_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
