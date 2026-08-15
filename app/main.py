import asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from app.config import Config
from app.constants import WORKFLOW_IDLE
from app.repositories.tenant import (
    get_tenant_by_whatsapp_number,
    get_services,
    get_working_hours,
)
from app.repositories.customer import get_or_create_customer
from app.repositories.conversation import (
    get_or_create_conversation,
    update_conversation,
    append_message,
)
from app.context.builder import build_context
from app.ai.intent_parser import parse_intent
from app.decision import decide
from app.templates import messages as tpl
from app.integrations.whatsapp import send_whatsapp_message
from app.workflows.n8n_client import call_n8n

app = FastAPI(title="AI Booking Simple", version="0.1.2")

# ============================================================
# UTILITIES E HELPER DI FORMATTAZIONE
# ============================================================

def _slot_labels(slots: list) -> list[str]:
    """Prende una lista di slot e restituisce una lista di stringhe formattate."""
    labels = []
    for s in slots:
        if isinstance(s, dict):
            labels.append(s.get("label") or s.get("datetime") or str(s))
        else:
            labels.append(str(s))
    return labels


def _build_reply_after_n8n(context: dict, decision: dict) -> str:
    """Costruisce la risposta testuale dopo l'interrogazione ad n8n."""
    booking = context.get("booking") or {}
    slots = booking.get("candidate_slots") or []
    result = booking.get("result") or {}
    
    if result.get("success") and decision.get("template_key") == "booking_confirmed":
        return tpl.BOOKING_CONFIRMED
    if slots:
        labels = _slot_labels(slots)
        return tpl.showing_slots(labels)
    if result.get("error"):
        return "Si è verificato un problema tecnico. Riprova tra poco oppure scrivi 'operatore'."
    return tpl.NO_SLOTS_FOUND


def _resolve_template(decision: dict, context: dict) -> str:
    """Associa la chiave del template decisa dal motore al testo finale."""
    key = decision.get("template_key")
    collected = context.get("collected_data") or {}
    booking = context.get("booking") or {}
    tenant_info = (context.get("tenant") or {}).get("info") or {}
    ai = context.get("ai") or {}
    entities = ai.get("entities") or {}

    static = tpl.get_template(key) if key else None

    if key == "confirmation_summary":
        return tpl.confirmation_summary(
            service=collected.get("service") or "—",
            date=str((collected.get("preferences") or {}).get("date") or collected.get("selected_slot") or "—"),
            time=str(collected.get("selected_time") or collected.get("selected_slot") or booking.get("selected_slot") or "—"),
            person_name=collected.get("person_name") or "—",
        )
        
    if key == "showing_slots":
        slots = booking.get("candidate_slots") or collected.get("last_slots") or []
        labels = _slot_labels(slots)
        if labels:
            return tpl.showing_slots(labels)
        return tpl.NO_SLOTS_FOUND
        
    if key == "lateral_info":
        info_type = entities.get("info_type")
        msg = combined_text_global if 'combined_text_global' in globals() else ""
        if not msg:
            msg = (context.get("request") or {}).get("message", "").lower()
            
        if info_type == "parking" or "parcheggio" in msg.lower():
            parking = tenant_info.get("parking", "Sì, abbiamo parcheggio.")
            return f"{parking}\n\n{tpl.LATERAL_CONTINUE}"
        if info_type == "price" or "prezzo" in msg.lower() or "costa" in msg.lower():
            return f"I prezzi dipendono dal servizio. Dimmi pure quale ti interessa.\n\n{tpl.LATERAL_CONTINUE}"
        if info_type == "address" or "indirizzo" in msg.lower() or "dove siete" in msg.lower():
            address = tenant_info.get("address", "L'indirizzo è disponibile su richiesta.")
            return f"{address}\n\n{tpl.LATERAL_CONTINUE}"
        if info_type == "hours" or "orari" in msg.lower():
            return f"Gli orari di apertura dipendono dal giorno. Dimmi pure per quale giorno ti serve sapere.\n\n{tpl.LATERAL_CONTINUE}"
        return f"Certo, dimmi pure cosa ti serve sapere.\n\n{tpl.LATERAL_CONTINUE}"

    if static:
        return static
    return tpl.UNCLEAR


# ============================================================
# ROTTE API (HEALTH & HOME)
# ============================================================

@app.get("/")
def home():
    """Risolve il 404 fornendo una rotta iniziale di verifica visiva."""
    return {"status": "running", "message": "Backend WhatsApp AI attivo e funzionante!"}


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.2"}


# ============================================================
# WHATSAPP WEBHOOK VERIFICATION
# ============================================================

@app.get("/webhook/whatsapp")
async def verify_whatsapp(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == Config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("Forbidden", status_code=403)


# ============================================================
# WHATSAPP MESSAGE WEBHOOK
# ============================================================

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    payload = await request.json()
    print("--- WEBHOOK RICEVUTO DA META ---", payload)
    
    message = _extract_message(payload)
    if not message:
        return {"status": "ignored"}

    # Avvio istantaneo della coroutine asincrona
    asyncio.create_task(process_messages([message]))

    return {"status": "accepted"}


def _extract_message(payload: dict) -> dict | None:
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages")
        if not messages:
            return None

        msg = messages[0]
        if msg.get("type") != "text":
            return None

        metadata = value.get("metadata", {})
        ts = msg.get("timestamp")
        received_at = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            if ts else datetime.now(timezone.utc).isoformat()
        )

        return {
            "to": metadata.get("display_phone_number"),
            "from": msg.get("from"),
            "message": msg["text"]["body"],
            "message_id": msg.get("id"),
            "received_at": received_at,
        }
    except (KeyError, IndexError, TypeError):
        return None


# ============================================================
# PIPELINE PRINCIPALE
# ============================================================

async def process_messages(messages: list[dict]):
    if not messages:
        return

    global combined_text_global
    last = messages[-1]
    phone = last["from"]
    business_phone = last["to"]

    combined_text = "\n".join(m["message"].strip() for m in messages if m.get("message"))
    combined_text_global = combined_text
    print(f"=== PROCESS {len(messages)} MSG da {phone} ===")
    print(combined_text)

    # 1. Tenant
    tenant = get_tenant_by_whatsapp_number(business_phone)
    if not tenant:
        print("Tenant non trovato per numero:", business_phone)
        return

    tenant_id = tenant["id"]

    # 2. Customer
    customer = get_or_create_customer(tenant_id, phone)

    # 3. Conversazione
    conversation = get_or_create_conversation(tenant_id, customer["id"], phone)

    # 4. Storico messaggi
    recent = conversation.get("recent_messages") or []
    for m in messages:
        recent = append_message(
            conversation["id"],
            role="user",
            content=m["message"],
            current_messages=recent,
        )
    conversation["recent_messages"] = recent

    # 5. Knowledge
    services = get_services(tenant_id)
    working_hours = get_working_hours(tenant_id)

    # 6. Context
    fake_message = {
        "message": combined_text,
        "message_id": last.get("message_id"),
        "received_at": last.get("received_at"),
        "from": phone,
        "to": business_phone,
    }
    context = build_context(
        tenant=tenant,
        customer=customer,
        conversation=conversation,
        message=fake_message,
        services=services,
        working_hours=working_hours,
    )

    # 7. AI#1 – Intent Extraction
    intent_result = parse_intent(
        message_text=combined_text,
        recent_messages=recent,
        current_workflow=conversation.get("workflow", WORKFLOW_IDLE),
    )
    context["ai"] = intent_result
    print("Intent:", intent_result)

    # 8. Decisione
    decision = decide(intent_result, conversation)
    print("Decision:", decision)

    collected = decision.get("updated_collected") or conversation.get("collected_data") or {}

    update_fields = {
        "workflow": decision["workflow"],
        "step": decision["step"],
        "collected_data": collected,
    }
    update_conversation(conversation["id"], **update_fields)
    conversation.update(update_fields)

    context["conversation"]["workflow"] = decision["workflow"]
    context["conversation"]["step"] = decision["step"]
    context["collected_data"] = collected

    # 9. Azione
    reply_text = None

    if decision["action"] == "request_human":
        reply_text = "Ti metto in contatto con un operatore. Un attimo di pazienza…"

    elif decision["action"] == "call_n8n":
        if decision.get("template_key") == "verifying_availability":
            wa_info = tenant.get("info") or {}
