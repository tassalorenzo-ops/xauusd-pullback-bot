"""
Bot di segnali Pullback MA100/MA30 per XAUUSD — versione GitHub Actions
========================================================================
Stessa strategia di pullback_signal_bot_cloud.py, ma pensata per essere
eseguita UNA VOLTA per esecuzione (non in loop infinito), perché
GitHub Actions lancia questo script a intervalli regolari (cron) su una
macchina "usa e getta". Lo stato del bot (se è in posizione, a che
prezzo) viene salvato in state.json e ricaricato al run successivo,
così il bot "si ricorda" tra un'esecuzione e l'altra.

Token e chat id Telegram NON sono scritti qui: arrivano dalle variabili
d'ambiente TELEGRAM_TOKEN e TELEGRAM_CHAT_IDS (impostate come Secrets
su GitHub, mai visibili nel codice).
"""

import json
import os
import sys
from datetime import datetime
import requests
import yfinance as yf

# ---------------------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------------------

SYMBOL = "GC=F"  # future oro COMEX su Yahoo Finance

MA_TREND_LEN = 100
MA_PULLBACK_LEN = 30
MIN_DIP_USD = 2.0
TARGET_ABOVE_USD = 0.5
STOP_USD = 20.0

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]

# ---------------------------------------------------------------------------
# STATO (persistito su file tra un'esecuzione e l'altra)
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "in_position": False,
    "entry_price": None,
    "stop_price": None,
    "last_bar_time": None,
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return dict(DEFAULT_STATE)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ---------------------------------------------------------------------------
# DATI (Yahoo Finance)
# ---------------------------------------------------------------------------

def get_rates(n=200):
    df = yf.Ticker(SYMBOL).history(period="5d", interval="1m")
    if df is None or df.empty:
        raise RuntimeError(f"Nessun dato Yahoo Finance per {SYMBOL}")
    df = df.reset_index().rename(columns={"Datetime": "time", "Close": "close"})
    df = df[["time", "close"]].tail(n).reset_index(drop=True)
    df["time"] = df["time"].astype(str)  # serializzabile in JSON
    return df

# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        print("ATTENZIONE: TELEGRAM_TOKEN o TELEGRAM_CHAT_IDS non configurati (Secrets mancanti).")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"Errore invio Telegram a {chat_id}: {e}")

# ---------------------------------------------------------------------------
# LOGICA STRATEGIA (un solo controllo)
# ---------------------------------------------------------------------------

def check_and_alert(state):
    df = get_rates(200)
    df["ma_trend"] = df["close"].rolling(MA_TREND_LEN).mean()
    df["ma_pullback"] = df["close"].rolling(MA_PULLBACK_LEN).mean()
    df = df.dropna().reset_index(drop=True)
    if len(df) < 2:
        return state

    last = df.iloc[-1]
    current_bar_time = last["time"]

    if state["last_bar_time"] == current_bar_time:
        return state  # candela gia' valutata
    state["last_bar_time"] = current_bar_time

    if not state["in_position"]:
        dip = last["ma_pullback"] - last["close"]
        if last["close"] > last["ma_trend"] and dip >= MIN_DIP_USD:
            entry_price = float(last["close"])
            stop_price = entry_price - STOP_USD
            state.update({"in_position": True, "entry_price": entry_price, "stop_price": stop_price})
            target_price = last["ma_pullback"] + TARGET_ABOVE_USD
            message = (
                f"📈 *Segnale Pullback XAUUSD*\n"
                f"Direzione: LONG\n"
                f"Prezzo entry: {entry_price:.2f}\n"
                f"Stop loss: {stop_price:.2f} (-{STOP_USD:.0f} USD)\n"
                f"Target: {target_price:.2f}\n"
                f"MA{MA_TREND_LEN} (trend): {last['ma_trend']:.2f}\n"
                f"MA{MA_PULLBACK_LEN} (pullback): {last['ma_pullback']:.2f}\n\n"
                f"Verifica sempre a occhio prima di entrare — questo è un avviso, non un ordine."
            )
            send_telegram(message)
            print(f"[{datetime.now()}] ENTRY inviato: {entry_price:.2f}")
    else:
        target_price = last["ma_pullback"] + TARGET_ABOVE_USD
        hit_target = last["close"] >= target_price
        hit_stop = last["close"] <= state["stop_price"]
        if hit_target or hit_stop:
            exit_price = float(last["close"])
            pnl = exit_price - state["entry_price"]
            esito = "STOP" if hit_stop else "TARGET"
            message = (
                f"{'🔴' if hit_stop else '✅'} *Uscita Pullback XAUUSD ({esito})*\n"
                f"Prezzo uscita: {exit_price:.2f}\n"
                f"Risultato: {pnl:+.2f} USD (per oncia)"
            )
            send_telegram(message)
            print(f"[{datetime.now()}] EXIT inviato: {exit_price:.2f} ({pnl:+.2f})")
            state.update({"in_position": False, "entry_price": None, "stop_price": None})

    return state


if __name__ == "__main__":
    state = load_state()
    try:
        state = check_and_alert(state)
    except Exception as e:
        print(f"[{datetime.now()}] Errore: {e}")
        sys.exit(0)  # non far fallire il workflow per un errore temporaneo (es. Yahoo irraggiungibile)
    save_state(state)
