import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame
import os
import pytz
from datetime import datetime, timedelta

API_KEY = os.environ.get('ALPACA_API_KEY', 'PK6LPVZX6NQAIJLIRYX3E4ML3A')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', 'DveH7XeVTDJoAKpzS6phetP7XeJWQe4FNmsbNAWzmLEM')
BASE_URL = "https://paper-api.alpaca.markets/v2"

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL)

ACCIONES = ["AAPL", "TSLA", "NVDA", "MSFT"]

def analizar(simbolo):
    try:
        fin = datetime.utcnow()
        inicio = fin - timedelta(days=7)
        barras = api.get_bars(
            simbolo,
            TimeFrame.Minute15,
            start=inicio.strftime('%Y-%m-%dT%H:%M:%SZ'),
            end=fin.strftime('%Y-%m-%dT%H:%M:%SZ'),
            limit=30
        ).df
        if barras.empty or len(barras) < 20:
            print(f"⚠️  {simbolo}: datos insuficientes")
            return "ESPERAR"
        cierre = barras["close"]
        media_corta = cierre.tail(5).mean()
        media_larga = cierre.tail(20).mean()
        precio_actual = cierre.iloc[-1]
        print(f"📊 {simbolo}: ${precio_actual:.2f} | Media5: ${media_corta:.2f} | Media20: ${media_larga:.2f}")
        if media_corta > media_larga * 1.002:
            return "COMPRAR"
        elif media_corta < media_larga * 0.998:
            return "VENDER"
        else:
            return "ESPERAR"
    except Exception as e:
        print(f"❌ Error analizando {simbolo}: {e}")
        return "ESPERAR"

def ejecutar_orden(simbolo, accion):
    try:
        if accion == "COMPRAR":
            api.submit_order(symbol=simbolo, qty=1, side="buy", type="market", time_in_force="gtc")
            print(f"✅ COMPRADA 1 accion de {simbolo}")
        elif accion == "VENDER":
            api.submit_order(symbol=simbolo, qty=1, side="sell", type="market", time_in_force="gtc")
            print(f"✅ VENDIDA 1 accion de {simbolo}")
        else:
            print(f"⏸️  {simbolo}: ESPERAR")
    except Exception as e:
        print(f"❌ Error en orden {simbolo}: {e}")

ET = pytz.timezone("America/New_York")
ahora = datetime.now(ET)
hora = ahora.hour
minuto = ahora.minute
dia = ahora.weekday()
mercado_abierto = (dia < 5) and (hora > 9 or (hora == 9 and minuto >= 30)) and (hora < 16)

print(f"🤖 Trading Bot — First Class Capital | {ahora.strftime('%A %H:%M')} ET")

if mercado_abierto:
    print(f"📈 Mercado ABIERTO — analizando...")
    for simbolo in ACCIONES:
        decision = analizar(simbolo)
        print(f"🧠 {simbolo}: {decision}")
        ejecutar_orden(simbolo, decision)
    print("✅ Ciclo completado.")
else:
    print(f"😴 Mercado cerrado — nada que hacer.")
