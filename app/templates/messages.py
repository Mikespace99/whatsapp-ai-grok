"""
Template di risposta standard.
Usare sempre questi quando la situazione è prevedibile.
AI#2 interviene solo nei casi non coperti.
"""

from app.constants import (
    STEP_ASKING_SERVICE,
    STEP_ASKING_DATE,
    STEP_ASKING_TIME,
    STEP_ASKING_PERSON_NAME,
)


# ------------------------------------------------------------
# Messaggi di sistema
# ------------------------------------------------------------

WELCOME = (
    "Ciao! Sono l'assistente per le prenotazioni.\n\n"
    "Posso aiutarti a:\n"
    "• Prenotare un appuntamento\n"
    "• Spostare o cancellare un appuntamento\n"
    "• Darti informazioni su orari, prezzi e servizi\n\n"
    "Come posso aiutarti?"
)

CONVERSATION_EXPIRED = (
    "La conversazione è scaduta per inattività.\n"
    "Scrivi pure se hai bisogno di aiuto!"
)

UNCLEAR = (
    "Non ho capito bene.\n"
    "Vuoi prenotare, spostare o cancellare un appuntamento? "
    "Oppure ti servono informazioni?"
)

VERIFYING_AVAILABILITY = "Perfetto, verifico subito la disponibilità e ti faccio sapere."

NO_SLOTS_FOUND = (
    "Al momento non ho disponibilità nei prossimi giorni.\n"
    "Posso avvisarti se si libera qualcosa, oppure vuoi lasciare i tuoi dati?"
)

ABANDONED = "Va bene, ho annullato l'operazione in corso. Se ti serve altro sono qui."

BOOKING_CONFIRMED = (
    "Appuntamento confermato!\n\n"
    "Ti arriverà un riepilogo. A presto!"
)

BOOKING_CANCELLED = "Appuntamento cancellato. Se hai bisogno di riprenotare, sono qui."

ASK_SERVICE = "Certo! Per quale servizio vorresti prenotare?"
ASK_DATE = "Che giorno ti andrebbe bene?"
ASK_TIME_PREFERENCE = "Preferisci mattina, pomeriggio, o hai un orario preciso?"
ASK_PERSON_NAME = "A nome di chi devo intestare l'appuntamento?"

ASK_RESCHEDULE = (
    "Va bene, ti aiuto a spostare l'appuntamento. "
    "Qual è la data dell'appuntamento da spostare?"
)
ASK_CANCEL = "Va bene. Qual è la data dell'appuntamento che vuoi cancellare?"

INFO_GENERIC = "Certo, cosa vorresti sapere? (orari, prezzi, indirizzo, parcheggio…)"

LATERAL_CONTINUE = "Vuoi continuare con la prenotazione che stavamo facendo?"
LATERAL_CONTINUE_SHORT = "Quando vuoi, dimmi pure come procedere con la prenotazione."


# ------------------------------------------------------------
# Template dinamici
# ------------------------------------------------------------

def showing_slots(slots: list[str]) -> str:
    """
    slots: lista di stringhe già formattate
    es. ["Martedì 19 agosto alle 10:00", ...]
    """
    lines = "\n".join(f"{i+1}. {s}" for i, s in enumerate(slots))
    return (
        f"Ho trovato queste disponibilità:\n\n{lines}\n\n"
        "Quale preferisci? (puoi rispondere con il numero o con l'orario)"
    )


def confirmation_summary(service: str, date: str, time: str, person_name: str) -> str:
    return (
        "Riepilogo del tuo appuntamento:\n\n"
        f"• Servizio: {service}\n"
        f"• Data: {date}\n"
        f"• Ora: {time}\n"
        f"• Intestato a: {person_name}\n\n"
        "Confermi?"
    )


# ------------------------------------------------------------
# Mappe per risoluzione elegante
# template_key  →  testo (o callable)
# ------------------------------------------------------------

TEMPLATES = {
    "welcome": WELCOME,
    "unclear": UNCLEAR,
    "ask_service": ASK_SERVICE,
    "ask_date": ASK_DATE,
    "ask_person_name": ASK_PERSON_NAME,
    "verifying_availability": VERIFYING_AVAILABILITY,
    "booking_confirmed": BOOKING_CONFIRMED,
    "booking_cancelled": BOOKING_CANCELLED,
    "abandoned": ABANDONED,
    "ask_reschedule": ASK_RESCHEDULE,
    "ask_cancel": ASK_CANCEL,
    "info": INFO_GENERIC,
    "conversation_expired": CONVERSATION_EXPIRED,
}

# Mappa step → template_key (utile se un giorno si decide dallo step)
ASK_BY_STEP = {
    STEP_ASKING_SERVICE: "ask_service",
    STEP_ASKING_DATE: "ask_date",
    STEP_ASKING_TIME: "ask_date",  # riusiamo ask_date / preferenza oraria
    STEP_ASKING_PERSON_NAME: "ask_person_name",
}


def get_template(key: str, default: str | None = None) -> str | None:
    """Ritorna il testo del template, o default/None se non trovato."""
    if key in TEMPLATES:
        return TEMPLATES[key]
    return default if default is not None else UNCLEAR
