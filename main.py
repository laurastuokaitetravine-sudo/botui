import os
import json
from flask import Flask, request
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA ---
exchange = ccxt.mexc({
    'apiKey': 'mx0vglmDs15A34AFNE',
    'secret': '7f79ccbe92ac42af94e897d9d0de77ea',
    'options': {'defaultType': 'swap'}
})

MY_PASSWORD = "OrtofonG"
LEVERAGE = 25
MARGIN_USDT = 9.5  # suma pozicijai


# --- UNIVERSALUS JSON PRIĖMIMAS + DEBUG ---
def extract_json():
    # DEBUG – matysime, ką naršyklė iš tikro siunčia
    print("RAW BODY:", request.get_data(as_text=True))
    print("FORM DATA:", request.form)

    # 1. Tikras JSON (TradingView, Postman)
    if request.is_json:
        try:
            return request.get_json()
        except:
            pass

    # 2. Raw tekstas (HTML forma su text/plain)
    raw = request.get_data(as_text=True).strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            return json.loads(raw)
        except:
            pass

    # 3. Form-data (HTML forma su textarea)
    if len(request.form) > 0:
        form_value = next(iter(request.form.values()))
        form_value = form_value.strip()
        if form_value.startswith("{") and form_value.endswith("}"):
            try:
                return json.loads(form_value)
            except:
                pass

    return None


@app.route('/webhook', methods=['POST'])
def webhook():
    # --- JSON PARSINIMAS ---
    data = extract_json()
    if data is None:
        print("❌ JSON klaida – negavau tinkamo formato")
        return "Invalid JSON", 400

    print(f"📩 Gautas signalas: {data}")

    # --- PASSCODE ---
    if data.get('passphrase') != MY_PASSWORD:
        print("❌ Neteisingas slaptažodis")
        return "Unauthorized", 403

    try:
        symbol = 'BTC/USDT'
        action = data.get('action')
        sl_price = float(data.get('sl'))

        # --- KAINA ---
        ticker = exchange.fetch_ticker(symbol)
        entry_price = ticker['last']

        # --- RIZIKOS SKAIČIAVIMAS ---
        risk_distance = abs(entry_price - sl_price)

        if action == 'short':
            tp_price = entry_price - (risk_distance * 2)
            side, close_side, pos_mode = 'sell', 'buy', 2
        else:
            tp_price = entry_price + (risk_distance * 2)
            side, close_side, pos_mode = 'buy', 'sell', 1

        # --- LEVERAGE ---
        try:
            exchange.set_leverage(LEVERAGE, symbol, params={'openType': 1, 'positionType': pos_mode})
        except Exception as e:
            print("⚠ Nepavyko nustatyti sverto:", e)

        # --- KIEKIS ---
        amount = (MARGIN_USDT * LEVERAGE) / entry_price
        amount = float(exchange.amount_to_precision(symbol, amount))

        print(f"📌 Atidarau {action.upper()} | amount={amount} | entry={entry_price}")

        # --- ATIDARYMAS ---
        exchange.create_order(symbol, 'market', side, amount, params={
            'positionMode': pos_mode,
            'openType': 1
        })

        # --- STOP LOSS ---
        exchange.create_order(symbol, 'stop_market', close_side, amount, None, {
            'stopPrice': sl_price,
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        # --- TAKE PROFIT ---
        exchange.create_order(symbol, 'limit', close_side, amount, tp_price, {
            'reduceOnly': True,
            'positionMode': pos_mode
        })

        print("✅ Pozicija atidaryta sėkmingai")
        return f"Sekme! {action} atidarytas.", 200

    except Exception as e:
        print(f"❌ Klaida vykdant orderius: {str(e)}")
        return str(e), 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
