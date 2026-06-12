import os
import json
import traceback
import time
from flask import Flask, request
import ccxt

app = Flask(__name__)

MY_PASSWORD = "OrtofonG"
DEFAULT_LEVERAGE = 5          # tavo default svertas
MARGIN_USDT = 100.0           # kiek USDT skiri pozicijai (be sverto)

# ============================================================
# PUBLIC CLIENT – TIK KAINAI (FUTURES ONLY, SU PROXY)
# ============================================================

public_exchange = ccxt.mexc({
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'swap',
        'types': {'swap': True},
        'fetchMarkets': ['swap']
    },
    'urls': {
        'api': {
            'public': 'https://contract.mexc.com',
            'private': 'https://contract.mexc.com'
        }
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})

# ============================================================
# PRIVATE CLIENT – ORDERIAMS (FUTURES ONLY, SU PROXY)
# ============================================================

private_exchange = ccxt.mexc({
    'apiKey': os.getenv('MEXC_API_KEY'),
    'secret': os.getenv('MEXC_API_SECRET'),
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'swap',
        'types': {'swap': True},
        'fetchMarkets': ['swap']
    },
    'urls': {
        'api': {
            'public': 'https://contract.mexc.com',
            'private': 'https://contract.mexc.com'
        }
    },
    'proxies': {
        'http': os.getenv('PROXY_URL'),
        'https': os.getenv('PROXY_URL'),
    }
})


@app.route('/')
def home():
    return "BOTAS ONLINE (LIMIT SHORT + SL + TP, FUTURES ONLY)", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return {"error": "Invalid JSON"}, 400

        # --------------------------------------------------------
        # PASS
        # --------------------------------------------------------
        if data.get('passphrase') != MY_PASSWORD:
            return "Unauthorized", 403

        # --------------------------------------------------------
        # VEIKIA TIK SU SHORT
        # --------------------------------------------------------
        action = str(data.get('action', '')).lower()
        if action != 'short':
            return "Ignored (only SHORT allowed)", 200

        # --------------------------------------------------------
        # TICKER → SYMBOL (FUTURES FORMATAS)
        # --------------------------------------------------------
        tv_ticker = data.get('ticker')
        if not tv_ticker:
            return {"error": "Missing ticker"}, 400

        clean = (
            tv_ticker
            .replace(".P", "")
            .replace("_", "")
            .replace("-", "")
            .replace("USDT", "")
        )

        if clean == "PEPE":
            clean = "10000PEPE"

        symbol = f"{clean}/USDT:USDT"

        # --------------------------------------------------------
        # LOAD MARKETS (FUTURES)
        # --------------------------------------------------------
        markets = private_exchange.load_markets()
        if symbol not in markets:
            return {"error": f"Symbol {symbol} not found on MEXC Futures"}, 400

        market = markets[symbol]

        # --------------------------------------------------------
        # GAUNAM ASK KAINĄ IŠ FUTURES PUBLIC API
        # --------------------------------------------------------
        ticker = None
        for attempt in range(3):
            try:
                ticker = public_exchange.fetch_ticker(symbol)
                break
            except Exception as e:
                print(f"Klaida gaunant kainą (bandymas {attempt+1}/3): {e}")
                time.sleep(1)

        if not ticker:
            return {"error": "Nepavyko gauti kainos"}, 400

        entry_price = float(ticker['ask'])

        # --------------------------------------------------------
        # SL / TP IŠ PINE (JSON)
        # --------------------------------------------------------
        try:
            sl_price = float(data.get('sl_price'))

            tp_raw = data.get('tp_price_1')
            if tp_raw and str(tp_raw).strip().lower() not in ['nan', 'na', 'null', '']:
                tp_price = float(tp_raw)
            else:
                # fallback, jei TP iš Pine nėra
                tp_price = entry_price * 0.985
        except (TypeError, ValueError):
            return {"error": "Blogas SL/TP formatas JSON'e"}, 400

        # --------------------------------------------------------
        # PRECISION
        # --------------------------------------------------------
        entry_price = float(private_exchange.price_to_precision(symbol, entry_price))
        sl_price = float(private_exchange.price_to_precision(symbol, sl_price))
        tp_price = float(private_exchange.price_to_precision(symbol, tp_price))

        # --------------------------------------------------------
        # LEVERAGE LIMITAI
        # --------------------------------------------------------
        max_leverage = DEFAULT_LEVERAGE
        if 'limits' in market and 'leverage' in market['limits']:
            if market['limits']['leverage']['max'] is not None:
                max_leverage = int(market['limits']['leverage']['max'])

        final_leverage = min(DEFAULT_LEVERAGE, max_leverage)
        print(f"[{symbol}] naudojamas svertas: {final_leverage}x")

        # --------------------------------------------------------
        # KIEKIO SKAIČIAVIMAS (100% POZICIJA)
        # --------------------------------------------------------
        total_value = MARGIN_USDT * final_leverage
        raw_crypto_amount = total_value / entry_price

        contract_size = float(market.get('contractSize', 1.0))
        total_contracts = raw_crypto_amount / contract_size

        min_contracts = float(market['limits']['amount']['min'])
        total_contracts = max(total_contracts, min_contracts)

        final_amount = float(private_exchange.amount_to_precision(symbol, total_contracts))

        # --------------------------------------------------------
        # NUSTATOM LEVERAGE (ISOLATED)
        # --------------------------------------------------------
        pos_mode = 2  # 2 = one-way / short
        try:
            private_exchange.set_leverage(
                int(final_leverage),
                symbol,
                {'openType': 1, 'positionType': pos_mode}
            )
        except Exception as e:
            print(f"Sverto nustatymo klaida: {e}")

        # ========================================================
        # 1) LIMIT SHORT ENTRY (ŠVARUS, BE SL/TP PARAMS)
        # ========================================================
        entry_params = {
            'posSide': 'SHORT',
            'openType': 1,
            'marginMode': 'isolated',
            'leverage': int(final_leverage),
            'timeInForce': 'PostOnly'
        }

        entry_order = private_exchange.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=final_amount,
            price=entry_price,
            params=entry_params
        )

        print(f"[ENTRY] SHORT LIMIT: {symbol} | qty={final_amount} | price={entry_price}")

        # ========================================================
        # 2) STOP LOSS – ATSKIRAS TRIGGER ORDERIS
        # ========================================================
        sl_params = {
            'marginMode': 'isolated',
            'leverage': int(final_leverage),
            'stopPrice': sl_price,
            'triggerPrice': sl_price,
            'posSide': 'SHORT',
            'reduceOnly': True
        }

        sl_order = private_exchange.create_order(
            symbol=symbol,
            type='stop_market',
            side='buy',
            amount=final_amount,
            params=sl_params
        )

        print(f"[SL] STOP MARKET: {symbol} | qty={final_amount} | stop={sl_price}")

        # ========================================================
        # 3) TAKE PROFIT – ATSKIRAS TRIGGER ORDERIS
        # ========================================================
        tp_params = {
            'marginMode': 'isolated',
            'leverage': int(final_leverage),
            'stopPrice': tp_price,
            'triggerPrice': tp_price,
            'posSide': 'SHORT',
            'reduceOnly': True
        }

        tp_order = private_exchange.create_order(
            symbol=symbol,
            type='take_profit_market',
            side='buy',
            amount=final_amount,
            params=tp_params
        )

        print(f"[TP] TAKE PROFIT MARKET: {symbol} | qty={final_amount} | tp={tp_price}")

        return {
            "status": "success",
            "symbol": symbol,
            "entry_id": entry_order['id'],
            "sl_id": sl_order['id'],
            "tp_id": tp_order['id']
        }, 200

    except Exception as e:
        print("KLAIDA:\n", traceback.format_exc())
        return {"error": str(e)}, 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
