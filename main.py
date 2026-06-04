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
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 5  
MARGIN_USDT = 5.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (100% TP1 - 100% UNIVERSALUS)", 200

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

        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            print("Klaida: Žinutėje negautas 'ticker' kintamasis")
            return {"error": "Missing ticker in request"}, 400

        # --- IŠMANUS IR UNIVERSALUS TICKERIO VALDYMAS (VISOMS MONETOMS) ---
        clean_base = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "").upper()
        
        markets = exchange.load_markets()
        symbol = None

        possible_symbols = [
            f"{clean_base}/USDT:USDT",
            f"10000{clean_base}/USDT:USDT",
            f"1000{clean_base}/USDT:USDT",
            f"100{clean_base}/USDT:USDT"
        ]

        for pos_sym in possible_symbols:
            if pos_sym in markets:
                symbol = pos_sym
                break

        if not symbol:
            for m_sym, m_info in markets.items():
                if 'linear' in m_info and m_info['linear'] and clean_base in m_sym:
                    symbol = m_sym
                    break

        if not symbol or symbol not in markets:
            print(f"Klaida: Moneta {clean_base} fjučerių rinkoje nerasta")
            return {"error": f"Symbol for {clean_base} not found on MEXC futures"}, 400

        market = markets[symbol]
        ticker = exchange.fetch_ticker(symbol)
        entry_price = float(ticker['ask']) 
        
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"Monetai {symbol} surastas fjučerių svertas: {final_leverage}x")

        # --- DUOMENŲ SKAITYMAS ---
        try:
            sl_price = float(data.get('sl_price'))
            tp1_raw = data.get('tp_price_1')
            
            # Saugiklis: Jei TP1 tuščias, naudojame atsarginį 0.8% pelną
            tp1_price = float(tp1_raw) if tp1_raw and str(tp1_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.992
            
        except (TypeError, ValueError):
            return {"error": "Klaida: Žinutėje gauti blogi kainų formatai"}, 400

        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price = float(exchange.price_to_precision(symbol, tp1_price))

        # Kiekio skaičiavimas 100% pozicijai
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        
        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])

        # Imame 100% viso kiekio
        final_contracts = max(total_contracts, min_contracts)
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        pos_mode = 2
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # Užsakymo parametrai (PostOnly + Įrašyti TP1 ir SL)
        params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(final_leverage),
            'stopLossPrice': sl_price,
            'takeProfitPrice': tp1_price,
            'timeInForce': 'PostOnly'  
        }

        # Vykdomas 1 LIMIT užsakymas su 100% kiekiu
        order = exchange.create_order(
            symbol=symbol,
            type='limit',       
            side='sell',
            amount=amount,
            price=entry_price,  
            params=params
        )
            
        print(f"SHORT LIMIT 100% TP1 pastatytas! Moneta: {symbol} | Kiekis: {amount} | SL: {sl_price} | TP1: {tp1_price}")

        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
