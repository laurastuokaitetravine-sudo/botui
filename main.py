import os
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# --- KONFIGŪRACIJA (Pakeisk šiuos duomenis) ---
MEXC_API_KEY = 'mx0vglHpZzjRqrv8jW'
MEXC_SECRET_KEY = 'ba4961633f34493da9fede2010471ec6'
WEBHOOK_PASSPHRASE = 'mano_slaptas_botas_123' # Sugalvok slaptažodį TradingView

# Prisijungimas prie MEXC biržos
exchange = ccxt.mexc({
    'apiKey': MEXC_API_KEY,
    'secret': MEXC_SECRET_KEY,
    'options': {
        'createMarketBuyOrderRequiresPrice': False
    }
})

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. Gauname duomenis iš TradingView
    data = request.json
    
    # 2. Saugumo patikra
    if not data or data.get('passphrase') != WEBHOOK_PASSPHRASE:
        return jsonify({"status": "error", "message": "Neteisingas slaptazodis"}), 403

    try:
        symbol = 'BTC/USDT'
        # MEXC minimumas paprastai yra 5 USDT, tad 0.0002 BTC (~12-15$) tinka
        amount = 0.0002 

        # 3. Logika: Jei TradingView sako "buy"
        if data.get('action') == 'buy':
            print(f"Gavau pirkimo signala: {symbol}")
            
            # Vykdome pirkimą rinkos kaina
            buy_order = exchange.create_market_buy_order(symbol, amount)
            
            # Gauname tikslią kainą, už kiek nupirko
            ticker = exchange.fetch_ticker(symbol)
            entry_price = ticker['last']
            
            # 4. Automatiškai pastatome pardavimą (Take Profit) +2% pelno
            profit_target = 1.02 # 1.02 reiškia +2%
            tp_price = round(entry_price * profit_target, 2)
            
            sell_order = exchange.create_limit_sell_order(symbol, amount, tp_price)
            
            return jsonify({
                "status": "success", 
                "message": f"Nupirkta uz {entry_price}, TP nustatytas ties {tp_price}"
            }), 200

    except Exception as e:
        print(f"Klaida: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    # Render naudoja PORT aplinkos kintamąjį
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
