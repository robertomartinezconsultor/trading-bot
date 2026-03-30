import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame
import os, pytz, smtplib, sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText

API_KEY    = os.environ.get('ALPACA_API_KEY', 'PK6LPVZX6NQAIJLIRYX3E4ML3A')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', 'DveH7XeVTDJoAKpzS6phetP7XeJWQe4FNmsbNAWzmLEM')
GMAIL_USER = os.environ.get('GMAIL_USER', 'roberto.martinezconsultor@gmail.com')
GMAIL_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')
BASE_URL   = "https://paper-api.alpaca.markets/v2"

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL)
ACCIONES = ["AAPL", "TSLA", "NVDA", "MSFT"]

def send_email(subject, body):
    if not GMAIL_PASS:
        print(f"📧 (sin contraseña Gmail) {subject}")
        return
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = GMAIL_USER
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.send_message(msg)
        print(f"📧 Email enviado: {subject}")
    except Exception as e:
        print(f"⚠️ Email error: {e}")

def get_account_summary():
    acc = api.get_account()
    return float(acc.portfolio_value), float(acc.cash), float(acc.equity) - float(acc.last_equity)

def get_todays_orders():
    orders = api.list_orders(status='filled', limit=20)
    today = datetime.now(pytz.timezone('America/New_York')).date()
    lines = []
    for o in orders:
        filled = o.filled_at
        if filled and filled.date() == today:
            lines.append(f"{'COMPRA' if o.side=='buy' else 'VENTA'} {o.qty} {o.symbol} @ ${float(o.filled_avg_price):.2f}")
    return lines or ["Sin operaciones hoy"]

def analizar(simbolo):
    try:
        fin = datetime.utcnow()
        inicio = fin - timedelta(days=7)
        barras = api.get_bars(simbolo, TimeFrame.Minute15,
            start=inicio.strftime('%Y-%m-%dT%H:%M:%SZ'),
            end=fin.strftime('%Y-%m-%dT%H:%M:%SZ'), limit=30).df
        if barras.empty or len(barras) < 20:
            return "ESPERAR"
        cierre = barras["close"]
        mc = cierre.tail(5).mean()
        ml = cierre.tail(20).mean()
        precio = cierre.iloc[-1]
        print(f"📊 {simbolo}: ${precio:.2f} | Media5: ${mc:.2f} | Media20: ${ml:.2f}")
        if mc > ml * 1.002: return "COMPRAR"
        elif mc < ml * 0.998: return "VENDER"
        else: return "ESPERAR"
    except Exception as e:
        print(f"❌ {simbolo}: {e}")
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
modo = sys.argv[1] if len(sys.argv) > 1 else "trade"

print(f"🤖 Trading Bot | {ahora.strftime('%A %H:%M')} ET | Modo: {modo}")

mercado_abierto = (dia < 5) and (hora > 9 or (hora == 9 and minuto >= 30)) and (hora < 16)

if modo == "open":
    portfolio, cash, pnl = get_account_summary()
    send_email("🔔 Mercado Abierto — Bot Activo",
        f"Buenos días Roberto!\n\nEl mercado abrió y el bot está operando.\n\nPortafolio: ${portfolio:,.2f}\nEfectivo: ${cash:,.2f}\n\nMonitoreando: {', '.join(ACCIONES)}\n\napp.alpaca.markets → Paper → Orders")

elif modo == "noon":
    portfolio, cash, pnl = get_account_summary()
    orders = get_todays_orders()
    send_email("📊 Reporte Mediodía — Trading Bot",
        f"Reporte de mediodía\n\nPortafolio: ${portfolio:,.2f}\nP&L hoy: ${pnl:+.2f}\n\nOperaciones:\n" + "\n".join(orders))

elif modo == "close":
    portfolio, cash, pnl = get_account_summary()
    orders = get_todays_orders()
    send_email("📈 Resumen Final del Día — Trading Bot",
        f"El mercado cerró. Resumen del día:\n\nPortafolio: ${portfolio:,.2f}\nP&L hoy: ${pnl:+.2f}\nEfectivo: ${cash:,.2f}\n\nOperaciones de hoy:\n" + "\n".join(orders) + "\n\napp.alpaca.markets para más detalles.")

else:
    if mercado_abierto:
        print("📈 Mercado ABIERTO — analizando...")
        resultados = []
        for simbolo in ACCIONES:
            decision = analizar(simbolo)
            print(f"🧠 {simbolo}: {decision}")
            ejecutar_orden(simbolo, decision)
            if decision != "ESPERAR":
                resultados.append(f"{decision} {simbolo}")
        print("✅ Ciclo completado.")
    else:
        print("😴 Mercado cerrado.")
