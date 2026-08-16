"""
Repository clienti.
- Normalizza sempre il numero di telefono
- Gestisce race condition sul primo insert
"""

import re
from app.supabase_client import get_supabase


def normalize_phone(phone: str | None) -> str:
    """
    Normalizza il numero WhatsApp:
    - rimuove spazi, trattini, parentesi
    - rimuove il prefisso +
    - lascia solo cifre
    Esempio: "+39 333-1234567" → "393331234567"
    """
    if not phone:
        return ""
    # Solo cifre
    digits = re.sub(r"\D", "", str(phone))
    return digits


def get_or_create_customer(tenant_id: str, phone_number: str) -> dict:
    """
    Recupera o crea il cliente.
    - Normalizza sempre il numero
    - In caso di race condition sull'insert (unique violation),
      riprova con una select.
    """
    phone = normalize_phone(phone_number)
    if not phone:
        raise ValueError("phone_number vuoto dopo normalizzazione")

    sb = get_supabase()

    # 1. Cerca esistente
    result = (
        sb.table("customers")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("phone_number", phone)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]

    # 2. Prova insert
    try:
        insert = (
            sb.table("customers")
            .insert({
                "tenant_id": tenant_id,
                "phone_number": phone,
            })
            .execute()
        )
        if insert.data:
            return insert.data[0]
    except Exception as e:
        # Probabile unique violation per race condition
        print(f"[customer] Insert fallito (possibile race): {e}")

    # 3. Retry select (l'altro thread ha creato il record)
    result = (
        sb.table("customers")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("phone_number", phone)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]

    raise RuntimeError(
        f"Impossibile creare o recuperare customer tenant={tenant_id} phone={phone}"
    )
