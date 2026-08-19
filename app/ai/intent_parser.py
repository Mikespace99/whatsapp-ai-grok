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
    INTENT_CHANGE_AVAILABILITY,
    INTENT_CONFIRM,
    INTENT_AFFIRM,
    INTENT_DENY,
    INTENT_REQUEST_HUMAN,
    INTENT_ABANDON,
    INTENT_UNCLEAR,
)

client = OpenAI(api_key=Config.OPENAI_API_KEY)


# ============================================================
# INTENT AMMESSI
# ============================================================

ALLOWED_INTENTS = [
    INTENT_GREETING,
    INTENT_BOOK,
    INTENT_RESCHEDULE,
    INTENT_CANCEL,
    INTENT_INFO,
    INTENT_SLOT_SELECTION,
    INTENT_CHANGE_AVAILABILITY,
    INTENT_CONFIRM,
    INTENT_AFFIRM,
    INTENT_DENY,
    INTENT_REQUEST_HUMAN,
    INTENT_ABANDON,
    INTENT_UNCLEAR,
]


# ============================================================
# SYSTEM PROMPT
# ============================================================

def _build_system_prompt(today_str: str, weekday_str: str) -> str:
    intents_list = ", ".join(f'"{i}"' for i in ALLOWED_INTENTS)

    return f"""
Sei un classificatore di intent per un sistema di prenotazione
appuntamenti via WhatsApp.

Analizza il messaggio dell'utente considerando anche:
- workflow attuale
- step attuale
- ultime battute della conversazione

Restituisci SOLO un JSON valido con questa struttura:

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


REGOLE INTENT:

- Saluto
  → "greeting"

- Vuole prenotare un appuntamento
  → "book_appointment"

- Vuole spostare un appuntamento già esistente
  → "reschedule_appointment"

- Vuole cancellare un appuntamento già esistente
  → "cancel_appointment"

- Chiede informazioni
  (prezzo, parcheggio, orari, indirizzo...)
  → "get_info"
  e popola entities.info_type di conseguenza

- Sceglie uno degli slot attualmente mostrati
  ("il secondo", "numero 2", "alle 10:30")
  → "slot_selection"

- Conferma una scelta o una richiesta
  ("sì", "ok", "va bene", "confermo")
  → "confirm" oppure "affirm"

- Rifiuta una specifica scelta che gli viene chiesto di confermare
  ("no", "non va bene")
  → "deny"

- Se il workflow è "booking" e lo step è "showing_slots",
  l'utente rifiuta gli slot attualmente mostrati e vuole
  una nuova ricerca di disponibilità:
  ("nessuno va bene",
   "nessuno di questi",
   "prova domani",
   "hai qualcosa venerdì?",
   "questi orari non vanno bene",
   "vediamo settimana prossima",
   "domani hai qualcosa?")
  → "change_availability"

IMPORTANTE:

"change_availability" NON significa cancellare o spostare
un appuntamento già esistente.

Significa che l'utente sta effettuando una prenotazione,
ha ricevuto degli slot, non li vuole e desidera cambiare
i criteri della ricerca di disponibilità.

Quando riconosci "change_availability", estrai tutte le
nuove preferenze temporali presenti nel messaggio.

Esempi:

"Nessuno va bene, prova domani"
→ intent = "change_availability"
→ preferences.date = data di domani

"Prova venerdì pomeriggio"
→ intent = "change_availability"
→ preferences.date = venerdì
→ preferences.time_preference = "afternoon"

"Hai qualcosa settimana prossima?"
→ intent = "change_availability"
→ preferences.period = "next_week"

"Nessuno di questi va bene"
→ intent = "change_availability"
→ nessuna nuova preferenza temporale


IMPORTANTE SULLA DISTINZIONE:

"il secondo"
→ "slot_selection"

"alle 16:00"
→ "slot_selection"

"no"
mentre viene chiesto di confermare uno specifico slot
→ "deny"

"nessuno va bene, prova domani"
→ "change_availability"

"questi orari non vanno bene, hai qualcosa venerdì?"
→ "change_availability"


DATA ODIERNA:

Oggi è {weekday_str} {today_str}.

Quando l'utente dice:
- "domani"
- "venerdì prossimo"
- "la prossima settimana"
- ecc.

calcola la data reale in formato YYYY-MM-DD quando
possibile.

Le preferenze di data/ora sono criteri per la ricerca
di disponibilità e devono essere estratte fedelmente.

Restituisci SOLO il JSON, nient'altro.
""".strip()


# ============================================================
# PARSE INTENT
# ============================================================

def parse_intent(
    message_text: str,
    recent_messages: list | None = None,
    current_workflow: str = "idle",
    current_step: str = "none",
    timezone_str: str = "Europe/Rome",
) -> dict:
    """
    Chiama l'AI e restituisce il dict strutturato.

    In caso di errore restituisce:
        intent = unclear
        confidence = 0.0
    """

    recent_messages = recent_messages or []

    # --------------------------------------------------------
    # Data corrente nel timezone del tenant
    # --------------------------------------------------------

    try:
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = timezone.utc

    now = datetime.now(tz)

    today_str = now.strftime("%Y-%m-%d")

    weekday_map = {
        0: "lunedì",
        1: "martedì",
        2: "mercoledì",
        3: "giovedì",
        4: "venerdì",
        5: "sabato",
        6: "domenica",
    }

    weekday_str = weekday_map[now.weekday()]

    # --------------------------------------------------------
    # Contesto storico
    # --------------------------------------------------------

    history_text = ""

    if recent_messages:
        history_text = "Ultime battute della conversazione:\n"

        for m in recent_messages[-4:]:
            role = (
                "Cliente"
                if m.get("role") == "user"
                else "Assistente"
            )

            history_text += (
                f"{role}: {m.get('content')}\n"
            )

        history_text += "\n"

    # --------------------------------------------------------
    # Messaggio inviato al modello
    # --------------------------------------------------------

    user_content = (
        f"{history_text}"
        f"Workflow attuale: {current_workflow}\n"
        f"Step attuale: {current_step}\n"
        f"Data di oggi: {weekday_str} {today_str}\n"
        f"Messaggio corrente del cliente: {message_text}"
    )

    system_prompt = _build_system_prompt(
        today_str,
        weekday_str,
    )

    # --------------------------------------------------------
    # Chiamata AI
    # --------------------------------------------------------

    try:
        response = client.chat.completions.create(
            model=Config.AI_MODEL_INTENT,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content

        data = json.loads(raw)

        # ----------------------------------------------------
        # Normalizzazione intent
        # ----------------------------------------------------

        intent = data.get(
            "intent",
            INTENT_UNCLEAR,
        )

        if intent not in ALLOWED_INTENTS:
            intent = INTENT_UNCLEAR

        # ----------------------------------------------------
        # Confidence
        # ----------------------------------------------------

        try:
            confidence = float(
                data.get("confidence", 0.5)
            )
        except (TypeError, ValueError):
            confidence = 0.5

        # ----------------------------------------------------
        # Entities / Preferences
        # ----------------------------------------------------

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
        print(
            f"[intent_parser] Errore: {e}"
        )

        return {
            "intent": INTENT_UNCLEAR,
            "confidence": 0.0,
            "entities": {},
            "preferences": {},
            "notes": str(e),
        }
