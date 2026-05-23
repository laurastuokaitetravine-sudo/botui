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
        
        # Saugiai pasiimame veiksmą ir priimame TIK short signalus
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (Only SHORT allowed)", 200

        # Dinaminis monetos pavadinimas
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            print("Klaida: Žinutėje negautas 'ticker' kintamasis")
            return {"error": "Missing ticker in request"}, 400

        # Išvalome .P, brūkšnius ir USDT galūnes, kad gautume tikrąjį MEXC formatą
        clean_ticker = tv_ticker.replace(".P", "").replace("_", "").replace("-", "").replace("USDT", "")
        
        # Grąžinama PEPE konvertavimo logika MEXC biržai
        if clean_ticker == "PEPE":
            clean_ticker = "10000PEPE"
            
        symbol = f"{clean_ticker}/USDT:USDT"

        # 1. Rinkos duomenys konkrečiai monetai
        markets = exchange.load_markets()
        if symbol not in markets:
            print(f"Klaida: Moneta {symbol} nerasta MEXC biržoje")
            return {"error": f"Symbol {symbol} not found on MEXC"}, 400

        market = markets[symbol]
        
        # --- SAUGIKLIS: Išvalome senus šios monetos užsakymus prieš naują sandorį ---
        try:
            exchange.cancel_all_orders(symbol)
            print(f"Išvalyti visi seni užsakymai monetai: {symbol}")
        except Exception as cancel_err:
            print(f"Pastaba: Nepavyko išvalyti senų užsakymų: {cancel_err}")

        ticker = exchange.fetch_ticker(symbol)
        
        # Kad LIMIT būtų MAKER (0% mokestis), SHORT įėjimo kainą keliame vos vos aukščiau ask (0.02%)
        raw_price = float(ticker['ask'])
        entry_price = raw_price * 1.0002 
        
        # --- SVERTO DINAMINIS PATIKRINIMAS ---
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"Monetai {symbol} taikomas svertas: {final_leverage}x (Maksimalus biržos limitas: {max_leverage}x)")

        # 2. Saugus dinaminio SL nurašymas iš TradingView
        raw_sl = data.get('sl_price')
        sl_price = None
        
        if raw_sl and str(raw_sl).strip().lower() not in ['nan', 'na', 'null', '']:
            try:
                sl_price = float(raw_sl)
                print(f"Iš indikatoriaus gauta SL kaina: {sl_price}")
            except ValueError:
                sl_price = None

        # --- MATEMATIKA: SHORT pozicijos pelno skaičiavimas ---
        # 0.8% kainos judesys žemyn su 25x svertu duoda lygiai 20% ROI pelno
        tp_price = entry_price * 0.992

        # PATAISYTAS SAUGIKLIS: Kadangi vykdome SHORT, indikatoriaus SL PRIVALO būti didesnis už įėjimo kainą.
        # Jei sl_price yra mažesnis arba lygus entry_price, tai logiškai yra LONG pozicijos SL, todėl SHORT pozicijai jis netinka.
        # Tokiu atveju suveikia tavo atsarginis planras (1% virš įėjimo kainos).
        if sl_price is None or sl_price <= entry_price:
            print(f"Įspėjimas: Indikatoriaus SL ({sl_price}) yra neteisingas SHORT pozicijai. Naudojamas atsarginis SL.")
            sl_price = entry_price * 1.01  

        # Suapvaliname kainas pagal biržos taisykles
        entry_price = float(exchange.price_to_precision(symbol, entry_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
        tp_price = float(exchange.price_to_precision(symbol, tp_price))

        # 4. Kiekio skaičiavimas naudojant dinamiškai parinktą svertą
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price
        
        contract_size = float(market.get('contractSize', 1.0))
        contracts_qty = raw_crypto_amount / contract_size
        
        min_contracts = float(market['limits']['amount']['min'])
        final_contracts = max(contracts_qty, min_contracts)
        
        amount = float(exchange.amount_to_precision(symbol, final_contracts))

        # 5. Svertas (Isolated režimas nustatomas šiai monetai)
        pos_mode = 2  # SHORT fiksuotas

        try:
            exchange.set_leverage(int(final_leverage), symbol, {
                'openType': 1,      
                'positionType': pos_mode
            })
        except:
            pass

        # --- 6 ŽINGSNIS: Pozicijos atidarymas su PostOnly LIMIT (0% Maker mokestis) ---
        open_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'leverage': int(final_leverage),
            'timeInForce': 'PostOnly'  # Garantuoja, kad užsakymas pateks į knygą kaip Maker
        }

        order = exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=amount,
            price=entry_price,
            params=open_params
        )
        print(f"SHORT LIMIT (Post-Only) pastatytas kaina: {entry_price}! ID: {order['id']}")

        # --- 7 ŽINGSNIS: Atskiri LIMIT TP ir SL užsakymai (0% Maker tikslas) ---
        try:
            # TAKE PROFIT (Grynas LIMIT): Atsistoja į orderių knygą kaip pirkimas žemiau.
            # Kai kaina nukris iki čia, pozicija užsidarys su 0% Maker mokesčiu.
            tp_params = {
                'posSide': 'SHORT',
                'openType': 1,
                'reduceOnly': True  # Užtikrina, kad tik uždarys esamą SHORT poziciją
            }
            exchange.create_order(
                symbol=symbol,
                type='limit',              
                side='buy',
                amount=amount,
                price=tp_price,            
                params=tp_params
            )
            print(f"Garantuotas 0% mokesčio LIMIT TP pastatytas ties: {tp_price}")

            # STOP LOSS (Trigger Limit): Aktyvuojasi tik kainai pakilus iki sl_price (paimto iš tavo indikatoriaus)
            sl_params = {
                'posSide': 'SHORT',
                'openType': 1,
                'triggerPrice': sl_price,  # CCXT standartas triggeriavimui
            }
            exchange.create_order(
                symbol=symbol,
                type='limit',              
                side='buy',
                amount=amount,
                price=sl_price,            
                params=sl_params
            )
            print(f"Apsauginis Trigger SL nustatytas ties tavo indikatoriaus kaina: {sl_price}")

        except Exception as trigger_err:
            print(f"ĮSPĖJIMAS: Nepavyko automatiškai prikabinti atskirų TP/SL: {trigger_err}")

        return {
            "status": "success", 
            "symbol": symbol, 
            "order_id": order['id'],
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price
        }, 200

    except Exception as e:
        print(f"KLAIDA: {traceback.format_exc()}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
