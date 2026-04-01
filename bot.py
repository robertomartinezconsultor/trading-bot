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
POSICION_PCT = 0.05   # 5% del portafolio por accion
STOP_LOSS_PCT = 0.02  # 2% stop-loss

def send_email(subject, body):
    if not GMAIL_PASS:
        print(f"[EMAIL] {subject}\n{body}")
        return
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = GMAIL_USER
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.send_message(msg)
        print(f"Email enviado: {subject}")
    except Exception as e:
        print(f"Email error: {e}")

def get_account():
    acc = api.get_account()
    return {
        'portfolio': float(acc.portfolio_value),
        'cash': float(acc.cash),
        'equity': float(acc.equity),
        'pnl': float(acc.equity) - float(acc.last_equity)
    }

def get_positions():
    try:
        return {p.symbol: p for p in api.list_positions()}
    except:
        return {}

def get_todays_orders():
    try:
        orders = api.list_orders(status='filled', limit=30)
        today = datetime.now(pytz.timezone('America/New_York')).date()
        lines = []
        for o in orders:
            if o.filled_at and o.filled_at.date() == today:
                lines.append(f"{'COMPRA' if o.side=='buy' else 'VENTA'} {o.qty} {o.symbol} @ ${float(o.filled_avg_price):.2f}")
        return lines or ["Sin operaciones hoy"]
    except:
        return ["Error obteniendo ordenes"]

def calcular_rsi(precios, periodo=14):
    if len(precios) < periodo + 1:
        return 50
    deltas = [precios[i] - precios[i-1] for i in range(1, len(precios))]
    ganancias = [d if d > 0 else 0 for d in deltas]
    perdidas = [-d if d < 0 else 0 for d in deltas]
    avg_gan = sum(ganancias[-periodo:]) / periodo
    avg_per = sum(perdidas[-periodo:]) / periodo
    if avg_per == 0:
        return 100
    rs = avg_gan / avg_per
    return 100 - (100 / (1 + rs))

def ema(precios, periodo):
    k = 2 / (periodo + 1)
    e = precios[0]
    for p in precios[1:]:
        e = p * k + e * (1 - k)
    return e

def analizar(simbolo, portfolio_value):
    try:
        fin = datetime.utcnow()
        inicio = fin - timedelta(days=10)
        barras = api.get_bars(
            simbolo, TimeFrame.Minute15,
            start=inicio.strftime('%Y-%m-%dT%H:%M:%SZ'),
            end=fin.strftime('%Y-%m-%dT%H:%M:%SZ'),
            limit=100
        ).df
        if barras.empty or len(barras) < 30:
            print(f"{simbolo}: datos insuficientes ({len(barras)} barras)")
            return "ESPERAR", 0, 0, 0

        cierres = list(barras["close"])
        precio  = cierres[-1]
        ema9    = ema(cierres[-30:], 9)
        ema21   = ema(cierres[-30:], 21)
        rsi     = calcular_rsi(cierres[-30:])

        print(f"{simbolo}: ${precio:.2f} | EMA9: ${ema9:.2f} | EMA21: ${ema21:.2f} | RSI: {rsi:.1f}")

        # Señal de compra: EMA9 cruza sobre EMA21 + RSI < 60 (no sobrecomprado)
        prev_ema9  = ema(cierres[-31:-1], 9)
        prev_ema21 = ema(cierres[-31:-1], 21)
        cruce_arriba = prev_ema9 <= prev_ema21 and ema9 > ema21

        # Señal de venta: EMA9 cruza bajo EMA21 O RSI > 70 (sobrecomprado)
        cruce_abajo = prev_ema9 >= prev_ema21 and ema9 < ema21

        qty = max(1, int((portfolio_value * POSICION_PCT) / precio))

        if cruce_arriba and rsi < 60:
            return "COMPRAR", qty, precio, rsi
        elif cruce_abajo or rsi > 72:
            return "VENDER", qty, precio, rsi
        else:
            return "ESPERAR", 0, precio, rsi

    except Exception as e:
        print(f"Error analizando {simbolo}: {e}")
        return "ESPERAR", 0, 0, 0

def check_stop_loss(posiciones):
    vendidos = []
    for sym, pos in posiciones.items():
        entrada    = float(pos.avg_entry_price)
        actual     = float(pos.current_price)
        pct_cambio = (actual - entrada) / entrada
        if pct_cambio <= -STOP_LOSS_PCT:
            qty = int(float(pos.qty))
            try:
                api.submit_order(symbol=sym, qty=qty, side="sell", type="market", time_in_force="gtc")
                perdida = (actual - entrada) * qty
                print(f"STOP-LOSS activado: VENTA {qty} {sym} @ ${actual:.2f} (entrada ${entrada:.2f}, perdida ${perdida:.2f})")
                vendidos.append(f"STOP-LOSS {sym}: entrada ${entrada:.2f} → ${actual:.2f} ({pct_cambio*100:.1f}%)")
            except Exception as e:
                print(f"Error stop-loss {sym}: {e}")
    return vendidos

def ejecutar_orden(simbolo, accion, qty, posiciones):
    try:
        tiene_posicion = simbolo in posiciones
        if accion == "COMPRAR" and not tiene_posicion:
            api.submit_order(symbol=simbolo, qty=qty, side="buy", type="market", time_in_force="gtc")
            print(f"COMPRADA {qty} acciones de {simbolo}")
            return True
        elif accion == "VENDER" and tiene_posicion:
            qty_real = int(float(posiciones[simbolo].qty))
            api.submit_order(symbol=simbolo, qty=qty_real, side="sell", type="market", time_in_force="gtc")
            print(f"VENDIDAS {qty_real} acciones de {simbolo}")
            return True
        else:
            if accion == "COMPRAR" and tiene_posicion:
                print(f"{simbolo}: señal COMPRA pero ya tenemos posicion — omitiendo")
            elif accion == "VENDER" and not tiene_posicion:
                print(f"{simbolo}: señal VENTA pero sin posicion — omitiendo")
            return False
    except Exception as e:
        print(f"Error orden {simbolo}: {e}")
        return False

# ─── MAIN ───────────────────────────────────────────────────────────────────
ET   = pytz.timezone("America/New_York")
ahora = datetime.now(ET)
dia  = ahora.weekday()
hora = ahora.hour
min_ = ahora.minute
modo = sys.argv[1] if len(sys.argv) > 1 else "trade"

print(f"Trading Bot | {ahora.strftime('%A %d %b %H:%M')} ET | Modo: {modo}")

mercado_abierto = (dia < 5) and ((hora == 9 and min_ >= 30) or (9 < hora < 16))

if modo == "open":
    acc = get_account()
    send_email(
        "Mercado Abierto — Bot Activo",
        f"Buenos dias Roberto!\n\nPortafolio: ${acc['portfolio']:,.2f}\nEfectivo disponible: ${acc['cash']:,.2f}\n\nMonitoreando: {', '.join(ACCIONES)}\nEstrategia: EMA 9/21 + RSI + Stop-Loss 2%\n\napp.alpaca.markets → Paper → Orders"
    )

elif modo == "noon":
    acc = get_account()
    orders = get_todays_orders()
    posiciones = get_positions()
    pos_lines = [f"  {sym}: {float(p.qty):.0f} acc @ ${float(p.avg_entry_price):.2f} (ahora ${float(p.current_price):.2f}, P&L ${float(p.unrealized_pl):.2f})" for sym, p in posiciones.items()] or ["  Sin posiciones abiertas"]
    send_email(
        "Reporte Mediodia — Trading Bot",
        f"Reporte de mediodia\n\nPortafolio: ${acc['portfolio']:,.2f}\nP&L hoy: ${acc['pnl']:+.2f}\nEfectivo: ${acc['cash']:,.2f}\n\nPosiciones abiertas:\n" + "\n".join(pos_lines) + "\n\nOperaciones de hoy:\n" + "\n".join(orders)
    )

elif modo == "close":
    acc = get_account()
    orders = get_todays_orders()
    send_email(
        "Resumen Final del Dia — Trading Bot",
        f"El mercado cerro. Resumen:\n\nPortafolio: ${acc['portfolio']:,.2f}\nP&L hoy: ${acc['pnl']:+.2f}\nEfectivo: ${acc['cash']:,.2f}\n\nOperaciones:\n" + "\n".join(orders) + "\n\napp.alpaca.markets para detalles."
    )

else:  # modo trade
    if mercado_abierto:
        print("Mercado ABIERTO — analizando...")
        acc       = get_account()
        posiciones = get_positions()
        operaciones = []

        # 1. Verificar stop-loss primero
        stops = check_stop_loss(posiciones)
        operaciones.extend(stops)

        # Recargar posiciones despues de stop-loss
        posiciones = get_positions()

        # 2. Analizar señales
        for simbolo in ACCIONES:
            decision, qty, precio, rsi = analizar(simbolo, acc['portfolio'])
            if decision != "ESPERAR":
                ejecutado = ejecutar_orden(simbolo, decision, qty, posiciones)
                if ejecutado:
                    operaciones.append(f"{decision} {qty} {simbolo} @ ${precio:.2f} (RSI:{rsi:.0f})")

        print(f"Ciclo completado. Operaciones: {len(operaciones)}")
    else:
        print("Mercado cerrado.")
