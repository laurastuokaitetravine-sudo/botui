import os
import json
import traceback
import time
import threading
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
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
DEFAULT_LEVERAGE = 25  
MARGIN_USDT = 30.0 

# --- FONINĖ LIKUSIOS POZICIJOS BE SEKIMO FUNKCIJA ---
def monitor_tp1_and_set_breakeven(symbol, tp1_order_id, entry_price, current_sl, final_leverage):
    """
    Ši funkcija fone stebi TP1 užsakymą. Kai jis užsipildo, 
    botas automatiškai perkelia likusių dalių SL į Breakeven (įėjimo kainą).
    """
    print(f"[BE SEKKIKLIS] Pradedama fone stebėti TP1 užsakymą: {tp1_order_id} monetai {symbol}")
    tp1_filled = False
    
    # Tikriname kas 3 sekundes, maksimaliai iki 12 valandų (14400 ciklų)
    for _ in range(14400):
        try:
            time.sleep(3)
            order_info = exchange.fetch_order(tp1_order_id, symbol)
            
            # Jei užsakymas sėkmingai užsipildė (filled) arba dingo iš aktyvių
            if order_info['status'] == 'closed':
                print(f"[BE SEKIKLIS] 🔥 TP1 pasiektas! Užsakymas {tp1_order_id} užpildytas. Perkeliamas SL į Breakeven...")
                tp1_filled = True
                break
                
            # Jei užsakymą atšaukėte rankiniu būdu
            if order_info['status'] == 'canceled':
                print(f"[BE SEKIKLIS] TP1 užsakymas buvo atšauktas rankiniu būdu. Sekimas stabdomas.")
                break
                
        except Exception as err:
            # Jei birža meta klaidą, kad užsakymo neranda, reiškia jis jau įvykdytas ir išvalytas
            if "Order does not exist" in str(err) or "not found" in str(err).lower():
                print(f"[BE SEKIKLIS] TP1 užsakymas užpildytas (nerastas aktyviuose). Perkeliamas SL į Breakeven...")
                tp1_filled = True
                break
            print(f"[BE SEKIKLIS] Klaida tikrinant užsakymą: {err}")
            
    if tp1_filled:
        try:
            # MEXC biržoje keičiant SL/TP pozicijai, tiesiog nusiunčiame naują Stop Loss kainą, lygią įėjimo kainai
            # Naudojame paramatrus, kurie tiesiogiai atnaujina einamosios SHORT pozicijos stopLoss
            exchange.create_order(
                symbol=symbol,
                type='limit', # fjučeriuose pozicijos keitimas vyksta per specialų orderio parametrą
                side='buy',   # kadangi esame SHORT, uždarymo apsauga yra BUY kryptimi
                amount=0,     # 0 kiekis MEXC biržoje keičiant parametrą reiškia visos likusios pozicijos SL atnaujinimą
                price=entry_price,
                params={
                    'posSide': 'SHORT',
                    'openType': 1,
                    'leverage': int(final_leverage),
                    'stopLossPrice': entry_price, # NAUJAS SL = ĮĖJIMO KAINA (Breakeven)!
                    'type': 'EXECUTE_ORDER' # Atnaujinimo komanda fjučeriams
                }
            )
            print(f"[BE SEKIKLIS] ✅ SĖKMINGAI likusios dalys perkeltos į Breakeven ties kaina: {entry_price}")
        except Exception as e:
            print(f"[BE SEKIKLIS] ❌ Nepavyko perstatyti SL į Breakeven: {e}")

@app.route('/')
def home():
    return "BOTAS ONLINE (3x TP + AUTOMATINIS BREAKEVEN)", 200

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

        # Universali monetų tvarkymo logika
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
            tp2_raw = data.get('tp_price_2')
            tp3_raw = data.get('tp_price_3')
            
            tp1_price = float(tp1_raw) if tp1_raw and str(tp1_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.992
            tp2_price = float(tp2_raw) if tp2_raw and str(tp2_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.985
            tp3_price = float(tp3_raw) if tp3_raw and str(tp3_raw).strip().lower() not in ['nan', 'na', 'null', ''] else entry_price * 0.980
            
        except (TypeError, ValueError):
            return {"error": "Klaida: Žinutėje gauti blogi kainų formatai"}, 400

        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price = float(exchange.price_to_precision(symbol, tp3_price))

        # Kiekio skaičiavimas (70% / 20% / 10%)
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        contract_size = float(market.get('contractSize', 1.0))
        
        total_contracts = raw_crypto_amount / contract_size
        min_contracts = float(market['limits']['amount']['min'])

        qty_tp1 = max(total_contracts * 0.70, min_contracts)
        qty_tp2 = max(total_contracts * 0.20, min_contracts)
        qty_tp3 = max(total_contracts * 0.10, min_contracts)

        amt_tp1 = float(exchange.amount_to_precision(symbol, qty_tp1))
        amt_tp2 = float(exchange.amount_to_precision(symbol, qty_tp2))
        amt_tp3 = float(exchange.amount_to_precision(symbol, qty_tp3))

        pos_mode = 2
        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        tp_configs = [
            {"num": 1, "amt": amt_tp1, "tp": tp1_price, "pct": "70%"},
            {"num": 2, "amt": amt_tp2, "tp": tp2_price, "pct": "20%"},
            {"num": 3, "amt": amt_tp3, "tp": tp3_price, "pct": "10%"}
        ]

        order_ids = []
        tp1_generated_id = None

        # --- 3 LIMIT ORDERIŲ PATEIKIMAS ---
        for config in tp_configs:
            params = {
                'posSide': 'SHORT',
                'openType': 1,
                'leverage': int(final_leverage),
                'stopLossPrice': sl_price,
                'takeProfitPrice': config["tp"],
                'timeInForce': 'PostOnly'  
            }

            order = exchange.create_order(
                symbol=symbol,
                type='limit',       
                side='sell',
                amount=config["amt"],
                price=entry_price,  
                params=params
            )
            order_ids.append(order['id'])
            if config["num"] == 1:
                tp1_generated_id = order['id']
                
            print(f"SHORT LIMIT TP{config['num']} ({config['pct']}) pastatytas! Moneta: {symbol} | Kiekis: {config['amt']} | SL: {sl_price} | TP: {config['tp']}")

        # --- AKTYVUOJAME BE SEKIKLĮ ATSKIRAME SRAUTE (THREAD) ---
        if tp1_generated_id:
            t = threading.Thread(
                target=monitor_tp1_and_set_breakeven, 
                args=(symbol, tp1_generated_id, entry_price, sl_price, final_leverage)
            )
            t.daemon = True # Užtikrina, kad srautas neužkabins serverio išjungimo metu
            t.start()

        return {"status": "success", "symbol": symbol, "order_ids": order_ids}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
