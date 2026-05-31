import os
import json
import time
import traceback
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- MEXC KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',                 # Naudojame Futures (USDT-M)
        'createMarketBuyOrderRequiresPrice': False,
        'defaultMarket': 'swap',               # Užtikrina, kad CCXT nenaudos spot API
    }
})

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 25
MARGIN_USDT = 5.0 

@app.route('/')
def home():
    return "BOTAS ONLINE (LIMIT ĮĖJIMAS + LAUKIMO LOGIKA)", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # --- JSON NUSKAITYMAS ---
        data = request.get_json(force=True, silent=True)
        if not data:
            data = json.loads(request.data.decode('utf-8').strip())

        # --- SAUGUMAS ---
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        # --- TIK SHORT ---
        if str(data.get('action', '')).lower() != 'short':
            return "Ignored (SHORT only mode)", 200

        # --- TICKER ---
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = tv_ticker.upper().replace(".P", "").replace("_", "").replace("-", "")
        if clean.endswith("USDT"):
            clean = clean[:-4]

        symbol = f"{clean}/USDT:USDT"
        print(f"TV: {tv_ticker} -> CCXT: {symbol}")

        markets = exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found"}, 400

        market = markets[symbol]

        # --- DINAMINIS SVERTO APRIBOROJIMAS ---
        max_exchange_leverage = 20  
        if 'limits' in market and 'leverage' in market['limits']:
            max_exchange_leverage = float(market['limits']['leverage'].get('max', 20))
        elif 'info' in market and 'maxLeverage' in market['info']:
            max_exchange_leverage = float(market['info']['maxLeverage'])

        active_leverage = int(min(DEFAULT_LEVERAGE, max_exchange_leverage))
        print(f"Pasirinktas svertas {symbol}: {active_leverage}x (Maksimalus biržos limitas: {max_exchange_leverage}x)")

        # --- ENTRY KAINA IR ORDER BOOK ---
        order_book = exchange.fetch_order_book(symbol, limit=5)
        entry_price = float(order_book['bids'][0][0])

        # --- LEVERAGE NUSTATYMAS ---
        try:
            exchange.set_leverage(active_leverage, symbol, {
                'openType': 1,   # 1 = Isolated
                'positionType': 2 # 2 = Short
            })
        except Exception as le:
            print(f"Leverage nustatymo įspėjimas: {le}")

        # --- SL/TP iš TradingView ---
        def safe_float(v):
            return float(v) if v not in [None, "", "null", "nan", "na"] else None

        sl_price  = safe_float(data.get('sl_price'))
        tp1_price = safe_float(data.get('tp1_price'))
        tp2_price = safe_float(data.get('tp2_price'))
        tp3_price = safe_float(data.get('tp3_price'))

        if sl_price is None:
            return {"error": "TradingView did not send SL (plot_0)"}, 400

        if tp1_price is None: tp1_price = entry_price * 0.990
        if tp2_price is None: tp2_price = entry_price * 0.980
        if tp3_price is None: tp3_price = entry_price * 0.970

        # --- PRECISION ---
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price    = float(exchange.price_to_precision(symbol, sl_price))
        tp1_price   = float(exchange.price_to_precision(symbol, tp1_price))
        tp2_price   = float(exchange.price_to_precision(symbol, tp2_price))
        tp3_price   = float(exchange.price_to_precision(symbol, tp3_price))

        # --- KIEKIS IR KONTRAKTAI ---
        total_value = MARGIN_USDT * active_leverage
        raw_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_amount / contract_size

        amount = int(float(exchange.amount_to_precision(symbol, contracts_qty)))
        min_amount = int(float(market['limits']['amount']['min'])) if market['limits']['amount']['min'] is not None else 1
        if amount < min_amount:
            amount = min_amount

        # --- 1. ATIDARYMAS (LIMIT - MAKER) ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=amount,
            price=entry_price,
            params=open_params
        )
        order_id = order['id']
        print(f"SHORT LIMIT OPENED | {symbol} | ID: {order_id} | Price={entry_price} | Qty={amount}")

        # --- 2. LAUKIMO CIKLAS (UŽTIKRINA APSAUGĄ NUO TUŠČIŲ TP/SL) ---
        filled = False
        # Tikriname 6 kartus kas 5 sekundes (iš viso 30 sekundžių laukimo)
        for i in range(6):
            time.sleep(5)
            try:
                check_order = exchange.fetch_order(order_id, symbol)
                status = check_order.get('status')
                print(f"Patikra {i+1}/6 | Orderio būsena: {status}")
                
                if status == 'closed':
                    filled = True
                    print("Limit orderis sėkmingai UŽPILDYTAS biržoje. Pereiname prie SL/TP.")
                    break
                elif status == 'canceled':
                    print("Orderis buvo atšauktas iš šalies. Nutraukiame darbą.")
                    return {"status": "ignored", "message": "Order was canceled externally"}, 200
            except Exception as fe:
                print(f"Klaida tikrinant orderio būseną: {fe}")

        # Jei per 30s neužsipildė – saugiai atšaukiame orderį
        if not filled:
            try:
                print("Laiko limitas baigėsi. Atšaukiame LIMIT užsakymą...")
                exchange.cancel_order(order_id, symbol)
                return {"status": "timeout", "message": "Limit order timeout. Order canceled, SL/TP ignored."}, 200
            except Exception as ce:
                # Jei nespėjome atšaukti, nes jis užsipildė paskutinę sekundę
                print(f"Nepavyko atšaukti, tikriausiai užsipildė paskutinę akimirką: {ce}")
                filled = True

        # --- 3. SL IR TP DEDAMI TIK JEI ORDERIS SĖKMINGAI UŽPILDYTAS ---
        if filled:
            min_amount = int(min_amount)
            
            # Saugus kiekių padalinimas
            amt_tp1 = int(amount * 0.70)
            amt_tp2 = int(amount * 0.20)
            
            if amt_tp1 < min_amount:
                amt_tp1 = amount
                amt_tp2 = 0
                amt_tp3 = 0
            elif amt_tp2 < min_amount:
                amt_tp2 = 0
                amt_tp3 = amount - amt_tp1
            else:
                amt_tp3 = amount - (amt_tp1 + amt_tp2)
                if amt_tp3 < min_amount:
                    amt_tp2 += amt_tp3
                    amt_tp3 = 0

            # --- STOP LOSS TRIGGERIS (TAKER - Saugumui užtikrinti) ---
            sl_params = {
                'posSide': 'SHORT',
                'openType': 1,
                'openClose': 'CLOSE',
                'triggerType': 1,      # Mark Price
                'stopPrice': sl_price
            }
            exchange.create_order(symbol, 'spotMarketOrder', 'buy', amount, None, sl_params)
            print(f"SL TRIGGER SET (TAKER) | Price={sl_price} | Qty={amount}")

            # --- TAKE PROFIT TRIGGER-LIMIT (MAKER - Sutaupome mokesčius) ---
            tp_limit_base = {
                'posSide': 'SHORT',
                'openType': 1,
                'openClose': 'CLOSE',
                'triggerType': 1,  # Mark Price
            }

            if amt_tp1 > 0:
                p1 = tp_limit_base.copy()
                p1['stopPrice'] = tp1_price
                exchange.create_order(symbol, 'limit', 'buy', amt_tp1, tp1_price, p1)
                print(f"TP1 LIMIT SET (MAKER) | Trigger/Price={tp1_price} | Qty={amt_tp1}")

            if amt_tp2 > 0:
                p2 = tp_limit_base.copy()
                p2['stopPrice'] = tp2_price
                exchange.create_order(symbol, 'limit', 'buy', amt_tp2, tp2_price, p2)
                print(f"TP2 LIMIT SET (MAKER) | Trigger/Price={tp2_price} | Qty={amt_tp2}")

            if amt_tp3 > 0:
                p3 = tp_limit_base.copy()
                p3['stopPrice'] = tp3_price
                exchange.create_order(symbol, 'limit', 'buy', amt_tp3, tp3_price, p3)
                print(f"TP3 LIMIT SET (MAKER) | Trigger/Price={tp3_price} | Qty={amt_tp3}")

            return {"status": "success", "order_id": order_id}, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
