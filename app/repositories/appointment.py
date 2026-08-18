"""
Repository appuntamenti.

Il backend Python è l'UNICO componente che legge/scrive su Supabase.
n8n si occupa solo di Google Calendar e restituisce il google_event_id;
è compito nostro registrare l'appuntamento nel database.
"""

from app.supabase_client import get_supabase


def create_appointment(
    tenant_id: str,
    customer_id: str | None,
    phone_number: str | None,
    service: str | None,
    appointment_date: str | None,
    appointment_time: str | None,
    google_event_id: str | None,
    status: str = "confirmed",
) -> dict | None:
    """
    Crea la riga dell'appuntamento dopo che n8n ha confermato la creazione
    dell'evento su Google Calendar. Ritorna la riga inserita, o None se
    il salvataggio fallisce (l'evento sul calendario resta comunque valido:
    l'errore viene solo loggato, non blocca la risposta al cliente).
    """
    sb = get_supabase()
    payload = {
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "phone_number": phone_number,
        "service": service,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "status": status,
        "google_event_id": google_event_id,
    }
    try:
        res = sb.table("appointments").insert(payload).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        # Disallineamento noto e accettato per ora: l'evento Google Calendar
        # esiste già, ma la riga DB non è stata salvata. Va solo loggato;
        # in futuro qui potrebbe agganciarsi un job di riconciliazione.
        print(f"[appointment_repo] Errore salvataggio appuntamento (google_event_id={google_event_id}): {e}")
        return None


def update_appointment_status(appointment_id: str, status: str) -> dict | None:
    """Aggiorna lo stato di un appuntamento (es. 'cancelled')."""
    sb = get_supabase()
    try:
        res = (
            sb.table("appointments")
            .update({"status": status})
            .eq("id", appointment_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        print(f"[appointment_repo] Errore aggiornamento stato appuntamento {appointment_id}: {e}")
        return None
