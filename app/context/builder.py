"""
Costruisce il Context ufficiale che viaggia tra Backend e n8n.
Versione semplice e lineare.

Importante:
la sezione "booking" viene ripristinata dai dati persistiti
in collected_data (last_slots, selected_slot, ecc.)
così non si perde tra un messaggio e l'altro.
"""

from datetime import datetime, timezone


def build_context(
    tenant: dict,
    customer: dict,
    conversation: dict,
    message: dict,
    services: list | None = None,
    working_hours: list | None = None,
) -> dict:
    """
    Context minimale ma completo.
    n8n può aggiornare soprattutto la sezione "booking".
    """

    services = services or []
    working_hours = working_hours or []
    collected = conversation.get("collected_data") or {}
    recent = conversation.get("recent_messages") or []

    # -------------------------------------------------
    # BOOKING: ripristina dallo stato persistito
    # -------------------------------------------------
    booking = {
        "candidate_slots": collected.get("last_slots") or [],
        "selected_slot": collected.get("selected_slot"),
        "matched_preferences": collected.get("matched_preferences"),
        "result": collected.get("last_booking_result"),
    }

    context = {
        # -------------------------------------------------
        # TENANT
        # -------------------------------------------------
        "tenant": {
            "id": tenant["id"],
            "business_name": tenant.get("business_name"),
            "assistant_name": tenant.get("assistant_name"),
            "timezone": tenant.get("timezone", "Europe/Rome"),
            "language": tenant.get("language", "it"),
            "slot_search_days": tenant.get("slot_search_days", 30),
            "info": tenant.get("info") or {},
        },

        # -------------------------------------------------
        # CUSTOMER
        # -------------------------------------------------
        "customer": {
            "id": customer["id"],
            "phone_number": customer.get("phone_number"),
            "full_name": customer.get("full_name"),
        },

        # -------------------------------------------------
        # CONVERSATION STATE
        # -------------------------------------------------
        "conversation": {
            "id": conversation["id"],
            "status": conversation.get("status", "active"),
            "workflow": conversation.get("workflow", "idle"),
            "step": conversation.get("step", "none"),
            "retry_count": conversation.get("retry_count", 0),
            "timeout_at": conversation.get("timeout_at"),
            "last_message_at": conversation.get("last_message_at"),
        },

        # -------------------------------------------------
        # DATI RACCOLTI + PREFERENZE
        # -------------------------------------------------
        "collected_data": collected,

        # -------------------------------------------------
        # MESSAGGIO CORRENTE
        # -------------------------------------------------
        "request": {
            "message": message.get("message") or message.get("text"),
            "message_id": message.get("message_id") or message.get("id"),
            "received_at": message.get("received_at"),
        },

        # -------------------------------------------------
        # STORIA RECENTE (per AI)
        # -------------------------------------------------
        "recent_messages": recent,

        # -------------------------------------------------
        # AI (riempito dopo la chiamata a AI#1)
        # -------------------------------------------------
        "ai": {
            "intent": None,
            "confidence": None,
            "entities": {},
            "preferences": {},
        },

        # -------------------------------------------------
        # BOOKING (ripristinato dallo stato precedente)
        # -------------------------------------------------
        "booking": booking,

        # -------------------------------------------------
        # KNOWLEDGE
        # -------------------------------------------------
        "knowledge": {
            "services": services,
            "working_hours": working_hours,
        },

        # -------------------------------------------------
        # RUNTIME
        # -------------------------------------------------
        "runtime": {
            "timezone": tenant.get("timezone", "Europe/Rome"),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    return context
