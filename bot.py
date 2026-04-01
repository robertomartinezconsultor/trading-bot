import alpaca_trade_api as tradeapi
import os, pytz, smtplib, sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText

API_KEY    = os.environ.get('ALPACA_API_KEY', 'PK6LPVZX6NQAIJLIRYX3E4ML3A')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', 'DveH7XeVTDJoAKpzS6phetP7XeJWQe4FNmsbNAWzmLEM')
GMAIL_USER = os.environ.get('GMAIL_USER', 'roberto.martinezconsultor@gmail.com')
GMAIL_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')
BASE_URL   = "https://paper-api.alpaca.markets"

# ── Parámetros de riesgo ──────────────────────────────────────────────────────
POSICION_PCT    = 0.08   # 8% del portafolio por operacion (~$8,000)
STOP_LOSS_PCT   = 0.02   # stop-loss -2%
TAKE_PROFIT_PCT = 0.03   # take-profit +3%
MAX_POSICIONES  = 3      # max 3 posiciones abiertas al mismo tiempo
MAX_PERDIDA_DIA = 0.03   # parar si pierde 3% en el dia
RSI_COMPRA      = 38     # comprar si RSI < 38 (oversold)
RSI_VENTA       = 65     # vender si RSI > 65 (overbought)

ACCIONES = ["AAPL", "TSLA", "NVDA", "MSFT", "AMD", "META"]  # ampliamos universo

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL)

# ─── Utilidades ───────────────────────────────────────────────────────────────
def send_email(subject, body):
    print(f"[EMAIL] {subject}")
    if not GMAIL_PASS:
        print(body)
        return
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From']    = GMAIL_USER
        msg['To']      = GMAIL_USER
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f"Email error: {e}")

def get_account():
    a = api.get_account()
    portfolio = float(a.portfolio_value)
    cash      = float(a.cash)
    pnl       = float(a.equity) - float(a.last_equity)
    pnl_pct   = pnl / float(a.last_equity) * 100 if float(a.last_equity) > 0 else 0
    return portfolio, cash, pnl, pnl_pct

def get_positions():
    try:
        return {p.symbol: p for p in api.list_positions()}
    except:
        return {}

def get_todays_trades():
    try:
        orders = api.list_orders(status='filled', limit=50)
        today  = datetime.now(pytz.timezone('America/New_York')).date()
        lines  = []
        for o in orders:
            if o.filled_at and o.filled_at.date() == today:
                side  = 'COMPRA' if o.side == 'buy' else 'VENTA'
                lines.append(f"{side} {o.qty} {o.symbol} @ ${float(o.filled_avg_price):.2f}")
        return lines or ["Sin operaciones hoy"]
    except:
        return ["Error obteniendo ordenes"]

# ─── Indicadores técnicos ─────────────────────────────────────────────────────
def ema(precios, n):
    k = 2 / (n + 1)
    e = precios[0]
    for p in precios[1:]:
        e = p * k + e * (1 - k)
    return e

def calcular_rsi(precios, n=14):
    if len(precios) < n + 1:
        return 50
    deltas  = [precios[i] - precios[i-1] for i in range(1, len(precios))]
    gans    = [d if d > 0 else 0 for d in deltas[-n:]]
    pers    = [-d if d < 0 else 0 for d in deltas[-n:]]
    avg_g   = sum(gans) / n
    avg_p   = sum(pers) / n
    if avg_p == 0:
        return 100
    return 100 - (100 / (1 + avg_g / avg_p))

def obtener_datos(simbolo, dias=15, barras_max=120):
    fin    = datetime.utcnow()
    inicio = fin - timedelta(days=dias)
    try:
        df = api.get_bars(
            simbolo, "15Min",
            start=inicio.strftime('%Y-%m-%dT%H:%M:%SZ'),
            end=fin.strftime('%Y-%m-%dT%H:%M:%SZ'),
            limit=barras_max,
            feed='iex'
        ).df
        return list(df['close']) if not df.empty else []
    except Exception as e:
        print(f"  {simbolo} datos error: {e}")
        return []

# ─── Señales ──────────────────────────────────────────────────────────────────
def analizar(simbolo, portfolio):
    cierres = obtener_datos(simbolo)
    if len(cierres) < 25:
        print(f"  {simbolo}: datos insuficientes ({len(cierres)})")
        return "ESPERAR", 0, 0, 0, 0

    precio = cierres[-1]
    rsi    = calcular_rsi(cierres[-20:])
    e9     = ema(cierres[-25:], 9)
    e21    = ema(cierres[-25:], 21)

    # Tendencia: EMA9 vs EMA21
    tendencia_ok = e9 >= e21 * 0.995   # permite hasta 0.5% bajo EMA21

    # Volumen relativo (variación de precio reciente)
    volatilidad = abs(cierres[-1] - cierres[-5]) / cierres[-5] * 100

    qty = max(1, int((portfolio * POSICION_PCT) / precio))

    print(f"  {simbolo}: ${precio:.2f} | EMA9:{e9:.2f} EMA21:{e21:.2f} | RSI:{rsi:.1f} | Vol:{volatilidad:.2f}%")

    # ── Señal COMPRA: oversold y no en caída fuerte ──
    if rsi < RSI_COMPRA and tendencia_ok:
        return "COMPRAR", qty, precio, rsi, volatilidad

    # ── Señal COMPRA fuerte: RSI extremo (< 25) sin importar tendencia ──
    if rsi < 25:
        return "COMPRAR_FUERTE", qty, precio, rsi, volatilidad

    # ── Señal VENTA: overbought ──
    if rsi > RSI_VENTA:
        return "VENDER", qty, precio, rsi, volatilidad

    return "ESPERAR", 0, precio, rsi, volatilidad

# ─── Gestión de posiciones ────────────────────────────────────────────────────
def gestionar_posiciones(posiciones, portfolio):
    vendidos = []
    for sym, pos in list(posiciones.items()):
        entrada = float(pos.avg_entry_price)
        actual  = float(pos.current_price)
        pct     = (actual - entrada) / entrada
        qty     = int(float(pos.qty))

        razon = None
        if pct <= -STOP_LOSS_PCT:
            razon = f"STOP-LOSS ({pct*100:.1f}%)"
        elif pct >= TAKE_PROFIT_PCT:
            razon = f"TAKE-PROFIT ({pct*100:.1f}%)"

        if razon:
            try:
                api.submit_order(symbol=sym, qty=qty, side="sell", type="market", time_in_force="gtc")
                pnl = (actual - entrada) * qty
                msg = f"{razon}: VENTA {qty} {sym} entrada ${entrada:.2f} → ${actual:.2f} | P&L ${pnl:+.2f}"
                print(f"  {msg}")
                vendidos.append(msg)
            except Exception as e:
                print(f"  Error cerrando {sym}: {e}")
    return vendidos

def comprar(simbolo, qty, precio, razon, posiciones):
    if simbolo in posiciones:
        print(f"  {simbolo}: ya tenemos posicion, omitiendo")
        return None
    try:
        api.submit_order(symbol=simbolo, qty=qty, side="buy", type="market", time_in_force="gtc")
        costo = qty * precio
        msg = f"COMPRA {qty} {simbolo} @ ${precio:.2f} (${costo:,.0f}) — {razon}"
        print(f"  {msg}")
        return msg
    except Exception as e:
        print(f"  Error comprando {simbolo}: {e}")
        return None

def vender(simbolo, posiciones):
    if simbolo not in posiciones:
        return None
    try:
        qty     = int(float(posiciones[simbolo].qty))
        entrada = float(posiciones[simbolo].avg_entry_price)
        actual  = float(posiciones[simbolo].current_price)
        api.submit_order(symbol=simbolo, qty=qty, side="sell", type="market", time_in_force="gtc")
        pnl = (actual - entrada) * qty
        msg = f"VENTA {qty} {simbolo} @ ${actual:.2f} (RSI alto) | P&L ${pnl:+.2f}"
        print(f"  {msg}")
        return msg
    except Exception as e:
        print(f"  Error vendiendo {simbolo}: {e}")
        return None

# ─── MAIN ─────────────────────────────────────────────────────────────────────
ET    = pytz.timezone("America/New_York")
ahora = datetime.now(ET)
dia   = ahora.weekday()
hora  = ahora.hour
min_  = ahora.minute
modo  = sys.argv[1] if len(sys.argv) > 1 else "trade"

print(f"=== Trading Bot v3 | {ahora.strftime('%a %d %b %H:%M')} ET | Modo: {modo} ===")

mercado_abierto = (dia < 5) and ((hora == 9 and min_ >= 30) or (9 < hora < 16))

# ── REPORTES ──────────────────────────────────────────────────────────────────
if modo == "open":
    portfolio, cash, pnl, pnl_pct = get_account()
    send_email(
        "Mercado Abierto — Bot Activo",
        f"Buenos dias Roberto!\n\n"
        f"Portafolio: ${portfolio:,.2f}\n"
        f"Efectivo: ${cash:,.2f}\n\n"
        f"Monitoreando: {', '.join(ACCIONES)}\n"
        f"Estrategia: RSI<38 compra | RSI>65 vende | Stop -2% | Take +3%\n\n"
        f"app.alpaca.markets → Paper → Orders"
    )

elif modo == "noon":
    portfolio, cash, pnl, pnl_pct = get_account()
    posiciones = get_positions()
    trades     = get_todays_trades()
    pos_lines  = []
    for sym, p in posiciones.items():
        entrada = float(p.avg_entry_price)
        actual  = float(p.current_price)
        upnl    = float(p.unrealized_pl)
        pct     = (actual - entrada) / entrada * 100
        pos_lines.append(f"  {sym}: {float(p.qty):.0f} acc | entrada ${entrada:.2f} | ahora ${actual:.2f} | P&L ${upnl:+.2f} ({pct:+.1f}%)")
    send_email(
        f"Mediodia — P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
        f"Portafolio: ${portfolio:,.2f}\n"
        f"P&L hoy: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"Efectivo: ${cash:,.2f}\n\n"
        f"Posiciones abiertas ({len(posiciones)}):\n" +
        ("\n".join(pos_lines) if pos_lines else "  Ninguna") +
        f"\n\nOperaciones de hoy:\n" + "\n".join(trades)
    )

elif modo == "close":
    portfolio, cash, pnl, pnl_pct = get_account()
    trades = get_todays_trades()
    send_email(
        f"Cierre — P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
        f"Mercado cerro. Resumen:\n\n"
        f"Portafolio: ${portfolio:,.2f}\n"
        f"P&L hoy: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"Efectivo: ${cash:,.2f}\n\n"
        f"Operaciones:\n" + "\n".join(trades) +
        f"\n\napp.alpaca.markets para detalles."
    )

# ── TRADING ───────────────────────────────────────────────────────────────────
else:
    if not mercado_abierto:
        print("Mercado cerrado.")
        sys.exit(0)

    print("Mercado ABIERTO")
    portfolio, cash, pnl, pnl_pct = get_account()
    print(f"Portfolio: ${portfolio:,.2f} | Cash: ${cash:,.2f} | P&L hoy: ${pnl:+.2f} ({pnl_pct:+.1f}%)")

    # Limite de perdida diaria
    if pnl_pct <= -MAX_PERDIDA_DIA * 100:
        print(f"LIMITE DE PERDIDA DIARIA alcanzado ({pnl_pct:.1f}%) — deteniendo operaciones.")
        sys.exit(0)

    posiciones = get_positions()
    print(f"Posiciones abiertas: {len(posiciones)}/{MAX_POSICIONES}")

    operaciones = []

    # 1. Gestionar posiciones existentes (stop-loss / take-profit)
    print("Revisando stop-loss / take-profit...")
    cierres_auto = gestionar_posiciones(posiciones, portfolio)
    operaciones.extend(cierres_auto)

    # Recargar posiciones
    posiciones = get_positions()

    # 2. Analizar señales
    print("Analizando señales...")
    for simbolo in ACCIONES:
        decision, qty, precio, rsi, vol = analizar(simbolo, portfolio)

        if decision in ("COMPRAR", "COMPRAR_FUERTE"):
            if len(posiciones) >= MAX_POSICIONES:
                print(f"  Max posiciones alcanzado, omitiendo {simbolo}")
                continue
            razon = f"RSI:{rsi:.0f}"
            if decision == "COMPRAR_FUERTE":
                razon = f"RSI EXTREMO:{rsi:.0f}"
            resultado = comprar(simbolo, qty, precio, razon, posiciones)
            if resultado:
                operaciones.append(resultado)
                posiciones = get_positions()  # actualizar

        elif decision == "VENDER":
            resultado = vender(simbolo, posiciones)
            if resultado:
                operaciones.append(resultado)
                posiciones = get_positions()

    print(f"Ciclo completado. Operaciones este ciclo: {len(operaciones)}")

    # Notificar si hubo operaciones
    if operaciones and GMAIL_PASS:
        portfolio2, cash2, pnl2, pnl_pct2 = get_account()
        send_email(
            f"Operacion ejecutada — P&L: ${pnl2:+.2f}",
            f"El bot ejecuto operaciones:\n\n" +
            "\n".join(f"• {op}" for op in operaciones) +
            f"\n\nPortafolio: ${portfolio2:,.2f}\nP&L hoy: ${pnl2:+.2f} ({pnl_pct2:+.1f}%)"
        )
