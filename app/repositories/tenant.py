"""
Repository tenant.
- Ricerca per numero WhatsApp filtrata a database (JSON)
- Controlli difensivi sui parametri
"""

from app.supabase_client import get_supabase
from app.repositories.customer import normalize_phone


def get_tenant_by_whatsapp_number(phone_number: str) -> dict | None:
    """
    Trova il tenant dal numero WhatsApp business.
    Filtra a database su info->>'whatsapp_number' (normalizzato).
    """
    phone = normalize_phone(phone_number)
    if not phone:
        return None

    sb = get_supabase()

    # Filtro JSON lato Postgres (non scarica tutta la tabella)
    result = (
        sb.table("tenants")
        .select("*")
        .eq("info->>whatsapp_number", phone)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    # Fallback: prova anche con il + davanti (se qualcuno l'ha salvato così)
    result = (
        sb.table("tenants")
        .select("*")
        .eq("info->>whatsapp_number", f"+{phone}")
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_tenant(tenant_id: str) -> dict | None:
    if not tenant_id:
        return None

    sb = get_supabase()
    result = (
        sb.table("tenants")
        .select("*")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_working_hours(tenant_id: str) -> list[dict]:
    if not tenant_id:
        return []

    sb = get_supabase()
    result = (
        sb.table("working_hours")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("day_of_week")
        .execute()
    )
    return result.data or []


def get_services(tenant_id: str) -> list[dict]:
    if not tenant_id:
        return []

    sb = get_supabase()
    result = (
        sb.table("services")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("active", True)
        .execute()
    )
    return result.data or []
