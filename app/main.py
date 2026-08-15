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
from app.message_buffer import message_buffer

app = FastAPI(title="AI Booking Simple", version="0.1.2")

# ============================================================
# HEALTH
# ============================================================
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
    
    # LOG DI EMERGENZA: Questo ti garantisce di vedere i log su Render non appena Meta tocca il server
    print("--- WEBHOOK RICEVUTO DA META ---", payload)
    
    message = _extract_message(payload)
    if not message:
        return {"status": "ignored"}

    phone = message["from"]
    # Passiamo la funzione di callback adattata per l'esecuzione asincrona
    message_buffer.add_message(phone, message, lambda msgs: asyncio.run(process_messages(msgs)))

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
# PIPELINE PRINCIPALE (Convertita in ASYNC)
# ============================================================
async def process_messages(messages: list[dict]):
    if not messages:
        return

    last = messages[-1]
    phone = last["from"]
    business_phone = last["to"]

    combined_text = "\n".join(m["message"].strip() for m in messages if m.get("message"))
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

    # 7. AI#1 – Intent (Rimosso il parametro inesistente timezone_str per evitare il TypeError)
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
            # Aggiunto await per l'invio asincrono
            await send_whatsapp_message(phone, tpl.VERIFYING_AVAILABILITY, Config.META_TOKEN, Config.PHONE_ID)

        try:
            # Aggiunto await per il client n8n asincrono
            context = await call_n8n(decision["workflow"], context)
        except Exception as e:
            print(f"[main] Errore call_n8n: {e}")
            context.setdefault("booking", {})["result"] = {
                "success": False,
                "error": str(e),
            }

        reply_text = _build_reply_after_n8n(context, decision)

        booking = context.get("booking") or {}
        if booking:
            new_collected = dict(collected)
            if booking.get("candidate_slots"):
                new_collected["last_slots"] = booking["candidate_slots"]
            if booking.get("selected_slot"):
                new_collected["selected_slot"] = booking["selected_slot"]
            if booking.get("result"):
                new_collected["last_booking_result"] = booking["result"]

            update_conversation(
                conversation["id"],
                collected_data=new_collected,
                step=decision["step"],
            )
            context["collected_data"] = new_collected
            conversation["collected_data"] = new_collected

    else:
        reply_text = _resolve_template(decision, context)

    # 10. Invia risposta (Aggiunto await e passaggio credenziali dinamiche se disponibili)
    if reply_text:
        # Recupera le credenziali del tenant dal dizionario per mantenere il multi-tenant isolato
        wa_info = tenant.get("info") or {}
        token = wa_info.get("access_token") or Config.META_TOKEN
        phone_id = wa_info.get("phone_number_id") or Config.PHONE_ID
        
        send_result = await send_whatsapp_message(phone, reply_text, token, phone_id)
        if send_result is None:
            print(f"[main] Invio WhatsApp FALLITO per {phone}. Salvo comunque nello storico.")

        append_message(
            conversation["id"],
            role="assistant",
            content=reply_text,
            current_messages=conversation.get("recent_messages"),
        )
    print("=== DONE ===")

# ============================================================
# TEMPLATE RESOLUTION
# ============================================================
def _resolve_template(decision: dict, context: dict) -> str:
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
        msg = (context.get("request") or {}).get("message", "").lower()

    @app.get("/")
    def home():
      return {"status": "running", "message": "Backend WhatsApp AI attivo!"}

