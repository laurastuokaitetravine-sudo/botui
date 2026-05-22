import os
import json
import traceback
import time
import threading
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
DEFAULT_LEVERAGE = 25  
MARGIN_USDT = 15.0  # Tavo nustatyta 15 USDT marža

# --- FONINĖ FUNKCIJA A: AUTOMATIŠKAI IŠTRINA ORDERĮ PO 15 MINUČIŲ, JEI JIS NEUŽSIPILDĖ ---
def cancel_unfilled_order(symbol, order_id):
    print(f"[{symbol}] Fone paleidžiamas 15 minučių laikmatis orderiui {order_id}...")
    time.sleep(900)  # 15 minučių = 900 sek
    try:
        order_info = exchange.fetch_order(order_id, symbol)
        status = order_info.get('status')
        if status == 'open':
            exchange.cancel_order(order_id, symbol)
            print(f"⏰ [{symbol}] Praėjo 15 min. Limit orderis {order_id} nebuvo užpildytas, todėl automatiškai atšauktas iš biržos.")
        else:
            print(f"[{symbol}] Orderis {order_id} jau užpildytas arba uždarytas (Statusas: {status}). Atšaukti nereikia.")
    except Exception as e:
        print(f"Klaida tikrinant laukiantį orderį fone: {e}")

# --- FONINĖ FUNKCIJA B: AKTYVIAI LAUKIA UŽSIPILDYMO IR TADA SAUGIAI UŽDEDA TP/SL ---
def watch_and_apply_tpsl(symbol, order_id, amount, sl_price, tp_price, market_id):
    print(f"[{symbol}] Fone paleidžiamas pozicijos seklys apsaugoms uždėti...")
    for _ in range(450):  # Tikrins kas 2 sekundes, iš viso 15 minučių (450 kartų)
        time.sleep(2)
        try:
            check_order = exchange.fetch_order(order_id, symbol)
            status = check_order.get('status')
            
            # Jei orderis užsipildė arba jau matomas bent dalinis užpildymas
            if status in ['closed', 'filled'] or float(check_order.get('filled', 0)) > 0:
                # Naudojame oficialų MEXC API trigerį aktyvios pozicijos TP/SL uždėjimui
                exchange.private_post_linear_order_create({
                    'symbol': market_id,
                    'price': 0, 
                    'vol': amount,
                    'side': 3,  # CLOSE_SHORT (Uždaryti shortą)
                    'type': 3,  # Trigger Market tipo orderis apsaugoms
                    'openType': 1,
                    'stopLossPrice': sl_price,
                    'takeProfitPrice': tp_price
                })
                print(f"🔥 [{symbol}] Pozicija užsipildė! Apsaugos sėkmingai prikabintos | SL: {sl_price} | TP (30%): {tp_price}")
                break  # Darbą baigiame, nes apsaugos sėkmingai uždėtos
                
            if status == 'canceled':
                print(f"[{symbol}] Užsakymas buvo atšauktas, apsaugų seklys stabdomas.")
                break
        except Exception as e:
            print(f"Klaida foniniame TP/SL seklyje: {e}")
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

        # Universali monetų tvarkymo logika
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        ticker = exchange.fetch_ticker(symbol)
        
        # --- 1. TEISINGAS POSLINKIS SHORT ĮĖJIMUI (0.03% AUKŠČIAU KNYGOJE) ---
        current_price = float(ticker['last'])
        limit_entry_price = current_price * 1.0003  
        
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)

        raw_sl = data.get('sl_price')
        sl_price = None
        
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
            except ValueError:
                sl_price = None

        # --- MATEMATIKA: 30% PELNAS (1.2% kainos judesys žemyn su 25x svertu) ---
        tp_price = limit_entry_price * 0.988

        if sl_price is None or sl_price <= limit_entry_price:
            sl_price = limit_entry_price * 1.01  

        sl_price = limit_entry_price + ((sl_price - limit_entry_price) / 2)

        # Suapvaliname kainas pagal biržos taisykles
        limit_entry_price = float(exchange.price_to_precision(symbol, limit_entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / limit_entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        pos_mode = 2 # SHORT fiksuotas

        try:
            exchange.set_leverage(int(final_leverage), symbol, {'openType': 1, 'positionType': pos_mode})
        except:
            pass

        # --- ŽINGSNIS A: Siunčiame TIK įėjimo LIMIT užsakymą su PostOnly ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(final_leverage),
            'timeInForce': 'PostOnly'  # Garantuoja 0% mokesčių (Maker) tarifą
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',       
            side='sell',
            amount=amount,
            price=limit_entry_price,  
            params=open_params
        )

        order_id = order['id']
        print(f"SHORT LIMIT (Post-Only) pastatytas! Moneta: {symbol} | ID: {order_id} | Kaina: {limit_entry_price}")

        # --- ŽINGSNIS B: Paleidžiame foninį seklį, kuris lauks užsipildymo ir TADA saugiai uždės TP/SL ---
        threading.Thread(
            target=watch_and_apply_tpsl, 
            args=(symbol, order_id, amount, sl_price, tp_price, market['id']),
            daemon=True
        ).start()

        # --- ŽINGSNIS C: Paleidžiame 15 minučių laikmatį užsakymo išvalymui iš knygos ---
        threading.Thread(
            target=cancel_unfilled_order, 
            args=(symbol, order_id),
            daemon=True
        ).start()

        return {"status": "success", "symbol": symbol, "order_id": order_id}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
