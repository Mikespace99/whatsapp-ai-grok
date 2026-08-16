"""
AI#1 – Intent + Entities + Preferences extractor.
Restituisce sempre un JSON strutturato.
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from openai import OpenAI
from app.config import Config
from app.constants import (
    INTENT_GREETING,
    INTENT_BOOK,
    INTENT_RESCHEDULE,
    INTENT_CANCEL,
    INTENT_INFO,
    INTENT_SLOT_SELECTION,
    INTENT_CONFIRM,
    INTENT_AFFIRM,
    INTENT_DENY,
    INTENT_REQUEST_HUMAN,
    INTENT_ABANDON,
    INTENT_UNCLEAR,
)

client = OpenAI(api_key=Config.OPENAI_API_KEY)

# Valori esatti delle costanti (devono combaciare 1:1 con constants.py)
ALLOWED_INTENTS = [
    INTENT_GREETING,           # "greeting"
    INTENT_BOOK,               # "book_appointment"
    INTENT_RESCHEDULE,         # "reschedule_appointment"
    INTENT_CANCEL,             # "cancel_appointment"
    INTENT_INFO,               # "get_info"
    INTENT_SLOT_SELECTION,     # "slot_selection"
    INTENT_CONFIRM,            # "confirm"
    INTENT_AFFIRM,             # "affirm"
    INTENT_DENY,               # "deny"
    INTENT_REQUEST_HUMAN,      # "request_human"
    INTENT_ABANDON,            # "abandon"
    INTENT_UNCLEAR,            # "unclear"
]


def _build_system_prompt(today_str: str, weekday_str: str) -> str:
    intents_list = ", ".join(f'"{i}"' for i in ALLOWED_INTENTS)
    return f"""
Sei un classificatore di intent per un sistema di prenotazione appuntamenti via WhatsApp.
Analizza il messaggio dell'utente e restituisci SOLO un JSON valido con questa struttura:

{{
  "intent": "uno dei valori ammessi",
  "confidence": 0.0-1.0,
  "entities": {{
    "service": null o stringa,
    "person_name": null o stringa,
    "slot_number": null o intero (1, 2, 3...),
    "selected_time": null o stringa,
    "info_type": null o "parking" o "price" o "address" o "hours" o "other"
  }},
  "preferences": {{
    "date": null o "YYYY-MM-DD",
    "date_from": null o "YYYY-MM-DD",
    "date_to": null o "YYYY-MM-DD",
    "period": null o "today" o "tomorrow" o "this_week" o "next_week",
    "time_preference": null o "morning" o "afternoon" o "evening" o "exact",
    "exact_time": null o "HH:MM"
  }},
  "notes": null o breve nota
}}

INTENT AMMESSI (usa ESATTAMENTE queste stringhe):
{intents_list}

Regole intent:
- Saluto → "greeting"
- Vuole prenotare → "book_appointment"
- Vuole spostare → "reschedule_appointment"
- Vuole cancellare → "cancel_appointment"
- Chiede info (prezzo, parcheggio, orari, indirizzo...) → "get_info"
  e popola entities.info_type di conseguenza
- Sceglie uno slot ("il secondo", "alle 10:30") → "slot_selection"
- Conferma (sì, ok, confermo) → "confirm" oppure "affirm"
- Rifiuta → "deny"
- Vuole un umano → "request_human"
- Vuole abbandonare il flusso corrente → "abandon"
- Dubbio → "unclear"

DATA ODIERNA (importante per calcolare date relative):
Oggi è {weekday_str} {today_str}.
Quando l'utente dice "domani", "venerdì prossimo", "la prossima settimana" ecc.,
calcola la data reale in formato YYYY-MM-DD usando la data di oggi.

Le preferenze di data/ora non sono vincolanti, sono solo indicazioni.
Restituisci SOLO il JSON, nient'altro.
""".strip()


def parse_intent(
    message_text: str,
    recent_messages: list | None = None,
    current_workflow: str = "idle",
    timezone_str: str = "Europe/Rome",
) -> dict:
    """
    Chiama l'AI e restituisce il dict strutturato.
    In caso di errore restituisce intent=unclear.
    """
    recent_messages = recent_messages or []

    # Data corrente nel timezone del tenant
    try:
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    weekday_map = {
        0: "lunedì", 1: "martedì", 2: "mercoledì", 3: "giovedì",
        4: "venerdì", 5: "sabato", 6: "domenica",
    }
    weekday_str = weekday_map[now.weekday()]

    # Contesto storico
    history_text = ""
    if recent_messages:
        history_text = "Ultime battute della conversazione:\n"
        for m in recent_messages[-4:]:
            role = "Cliente" if m.get("role") == "user" else "Assistente"
            history_text += f"{role}: {m.get('content')}\n"
        history_text += "\n"

    user_content = (
        f"{history_text}"
        f"Workflow attuale: {current_workflow}\n"
        f"Data di oggi: {weekday_str} {today_str}\n"
        f"Messaggio corrente del cliente: {message_text}"
    )

    system_prompt = _build_system_prompt(today_str, weekday_str)

    try:
        response = client.chat.completions.create(
            model=Config.AI_MODEL_INTENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)

        intent = data.get("intent", INTENT_UNCLEAR)
        # Normalizza: se l'AI inventa qualcosa fuori lista → unclear
        if intent not in ALLOWED_INTENTS:
            intent = INTENT_UNCLEAR

        confidence = float(data.get("confidence", 0.5))
        entities = data.get("entities") or {}
        preferences = data.get("preferences") or {}

        return {
            "intent": intent,
            "confidence": confidence,
            "entities": entities,
            "preferences": preferences,
            "notes": data.get("notes"),
        }

    except Exception as e:
        print(f"[intent_parser] Errore: {e}")
        return {
            "intent": INTENT_UNCLEAR,
            "confidence": 0.0,
            "entities": {},
            "preferences": {},
            "notes": str(e),
        }
