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
    'options': {
        'defaultType': 'swap',
        'createMarketBuyOrderRequiresPrice': False
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25  
MARGIN_USDT = 5.0 

# Funkcija, kuri stebi kainą fone ir valdo 50% TP bei Break-Even
def monitor_position(symbol, entry_price, sl_price, tp_price, amount, pos_mode):
    print(f"[{symbol}] Pradedamas fone pozicijos sekimas...")
    
    # Paskaičiuojame, kur yra pusė kelio iki TP (50% TP tikslo)
    # Kadangi tai SHORT, kaina krenta. Pusė kelio bus žemiau įėjimo kainos.
    target_50_price = entry_price - (abs(entry_price - tp_price) / 2)
    
    half_closed = False
    
    while True:
        try:
            time.sleep(3) # Tikriname kainą kas 3 sekundes
            
            # Atsiunčiame naujausią kainą
            ticker = exchange.fetch_ticker(symbol)
            current_price = float(ticker['last'])
            
            # Tikriname, ar pozicija dar gyva biržoje
            positions = exchange.fetch_positions([symbol])
            active_position = False
            for p in positions:
                if float(p['contracts']) > 0:
                    active_position = True
                    break
            
            if not active_position:
                print(f"[{symbol}] Pozicija biržoje nebėra aktyvi. Baigiam sekimą.")
                break

            # SHORT sandoriui: kaina nukrito žemiau 50% TP ribos
            if not half_closed and current_price <= target_50_price:
                print(f"[{symbol}] Pasiekta 50% TP riba ({target_50_price})! Vykdomi veiksmai...")
                
                half_amount = amount / 2
                half_amount = float(exchange.amount_to_precision(symbol, half_amount))
                
                # 1. Atšaukiame senus SL ir TP orderius, kad jie netrukdytų
                try:
                    exchange.cancel_all_orders(symbol)
                except:
                    pass
                
                # 2. Uždarome PUSĘ pozicijos (perkam rinkos kaina SHORT uždarymui)
                exchange.create_order(symbol, 'market', 'buy', half_amount, params={
                    'positionMode': pos_mode,
                    'openType': 2 # 2 = Close pozicija
                })
                print(f"[{symbol}] Sėkmingai uždaryta pusė pozicijos: {half_amount}")
                
                # 3. Statome NAUJĄ Stop Loss ant Break-Even (įėjimo kainos) likusiam kiekiui
                exchange.create_order(symbol, 'stop_market', 'buy', half_amount, None, {
                    'stopPrice': entry_price, 
                    'reduceOnly': True,
                    'positionMode': pos_mode
                })
                
                # 4. Statome NAUJĄ Take Profit pirminiam tikslui likusiam kiekiui
                exchange.create_order(symbol, 'limit', 'buy', half_amount, tp_price, {
                    'reduceOnly': True,
                    'positionMode': pos_mode
                })
                
                print(f"[{symbol}] SL perkeltas ant BE ({entry_price}). Likęs TP ties: {tp_price}")
                half_closed = True
                break # Užduotis įvykdyta, fone stebėjimą baigiame (birža pati uždarys likusį)

        except Exception as e:
            print(f"Klaida sekant kainą fone: {e}")
            time.sleep(5)

@app.route('/')
def home():
    return "BOTAS ONLINE", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        
        if not data:
            try:
                raw_data = request.data.decode('utf-8').strip()
                data = json.loads(raw_data)
            except Exception as json_err:
                return {"error": "Invalid JSON format"}, 400

        if not data or data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403
        
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker in request"}, 400

        clean_ticker = tv_ticker.replace(".P", "").replace("USDT", "")
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        ticker = exchange.fetch_ticker(symbol)
        entry_price = float(ticker['last'])
        
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        pos_mode = 2 # SHORT fijo

        raw_sl = data.get('sl_price')
        sl_price = None
        tp_price = None
        
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        if sl_price and sl_price > entry_price:
            risk_distance = sl_price - entry_price
            tp_price = entry_price - (risk_distance * 2)

        if sl_price is None or tp_price is None:
            sl_price = entry_price * 1.01
            tp_price = entry_price * 0.98

        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # Atidaro pradinę poziciją
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=amount,
            params={'posSide': 'SHORT', 'openType': 1, 'leverage': int(final_leverage)}
        )

        # Pirminis SL ir TP pastatymas saugumui
        try:
            exchange.create_order(symbol, 'stop_market', 'buy', amount, None, {'stopPrice': sl_price, 'reduceOnly': True, 'positionMode': pos_mode})
            exchange.create_order(symbol, 'limit', 'buy', amount, tp_price, {'reduceOnly': True, 'positionMode': pos_mode})
        except:
            pass

        # --- SVARBU: Paleidžiame sekimą fone ---
        threading.Thread(
            target=monitor_position, 
            args=(symbol, entry_price, sl_price, tp_price, amount, pos_mode),
            daemon=True
        ).start()

        print(f"SHORT atidarytas fone sekimui! Moneta: {symbol} | SL: {sl_price} | TP: {tp_price}")
        return {"status": "success", "symbol": symbol, "order_id": order['id']}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
