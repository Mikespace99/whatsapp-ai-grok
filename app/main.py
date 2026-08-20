import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import Config
from app.constants import WORKFLOW_IDLE, STEP_NONE
from app.repositories.tenant import (
    get_tenant_by_whatsapp_number,
    get_tenant_knowledge,
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
from app.repositories.appointment import create_appointment
from app.web.routes import router as web_router

app = FastAPI(title="AI Booking Simple", version="0.2.0")

# Web UI (login, register, onboarding)
app.include_router(web_router)


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
    n8n_action = decision.get("n8n_action")

    if n8n_action == "create_booking":
        if result.get("success"):
            return tpl.BOOKING_CONFIRMED
        return tpl.BOOKING_FAILED

    # n8n_action == "search_availability" (o non specificato, per retrocompatibilità)
    if slots:
        labels = _slot_labels(slots)
        intro = None
        if booking.get("matched_preferences") is False:
            prefs = context.get("collected_data") or {}
            time_pref = (prefs.get("preferences") or {}).get("time_preference")
            intro = tpl.preference_mismatch_intro(time_pref)
        return tpl.showing_slots(labels, intro=intro)

    if result.get("no_slots"):
        if result.get("search_was_narrow"):
            days = (context.get("tenant") or {}).get("slot_search_days") or 30
            return tpl.no_slots_narrow(days)
        return tpl.NO_SLOTS_WIDE

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
    knowledge = context.get("knowledge") or {}

    if key == "ask_service":
        return tpl.ask_service_with_list(knowledge.get("services"))

    if key == "confirmation_summary":
        slot = collected.get("selected_slot") or {}
        slot_date = slot.get("date") if isinstance(slot, dict) else None
        slot_time = slot.get("time") if isinstance(slot, dict) else None
        slot_label = slot.get("label") if isinstance(slot, dict) else None
        return tpl.confirmation_summary(
            service=collected.get("service") or "—",
            date=slot_date or slot_label or "—",
            time=slot_time or "—",
            person_name=collected.get("person_name") or "—",
        )

    if key == "confirm_slot":
        slot = collected.get("selected_slot") or {}
        label = slot.get("label") if isinstance(slot, dict) else None
        return tpl.confirm_slot(label or "questo slot")

    if key == "no_slots_narrow":
        days = (context.get("tenant") or {}).get("slot_search_days") or 30
        return tpl.no_slots_narrow(days)

    if key == "showing_slots":
        slots = booking.get("candidate_slots") or collected.get("last_slots") or []
        labels = _slot_labels(slots)
        if labels:
            return tpl.showing_slots(labels)
        return tpl.NO_SLOTS_FOUND

    if key == "lateral_info":
        info_type = entities.get("info_type")
        msg = ((context.get("request") or {}).get("message") or "").lower()
        knowledge = context.get("knowledge") or {}
        tenant_ctx = context.get("tenant") or {}

        if info_type == "parking" or "parcheggio" in msg:
            parking = tenant_info.get("parking") or "Per il parcheggio ti consiglio di chiedere in studio."
            return f"{parking}\n\n{tpl.LATERAL_CONTINUE}"
        if info_type == "price" or "prezzo" in msg or "costa" in msg:
            services_text = knowledge.get("services_text") or ""
            if services_text:
                return f"Ecco i servizi e i prezzi:\n\n{services_text}\n\n{tpl.LATERAL_CONTINUE}"
            return f"I prezzi dipendono dal servizio. Dimmi pure quale ti interessa.\n\n{tpl.LATERAL_CONTINUE}"
        if info_type == "address" or "indirizzo" in msg or "dove siete" in msg or "sede" in msg:
            locations_text = knowledge.get("locations_text") or ""
            if locations_text:
                return f"Le nostre sedi:\n\n{locations_text}\n\n{tpl.LATERAL_CONTINUE}"
            address = tenant_info.get("address") or "L'indirizzo è disponibile su richiesta."
            return f"{address}\n\n{tpl.LATERAL_CONTINUE}"
        if info_type == "hours" or "orari" in msg:
            hours_text = knowledge.get("working_hours_text") or ""
            if hours_text:
                return f"Orari di apertura:\n\n{hours_text}\n\n{tpl.LATERAL_CONTINUE}"
            return f"Gli orari dipendono dal giorno. Scrivimi pure per quale giorno ti serve sapere.\n\n{tpl.LATERAL_CONTINUE}"
        if "serviz" in msg:
            services_text = knowledge.get("services_text") or ""
            if services_text:
                return f"I nostri servizi:\n\n{services_text}\n\n{tpl.LATERAL_CONTINUE}"
        specialty = tenant_ctx.get("specialty")
        if specialty and ("specializz" in msg or "cosa fate" in msg or "chi siete" in msg):
            name = tenant_ctx.get("business_name") or "Lo studio"
            return f"{name} – {specialty}.\n\n{tpl.LATERAL_CONTINUE}"
        return f"Certo, dimmi pure cosa ti serve sapere (orari, sedi, servizi, prezzi…).\n\n{tpl.LATERAL_CONTINUE}"

    if static:
        return static
    return tpl.UNCLEAR


# ============================================================
# ROTTE API (HEALTH & HOME)
# ============================================================

@app.get("/api/status")
def api_status():
    return {"status": "running", "message": "Backend WhatsApp AI attivo e funzionante!"}


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


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

def _verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """
    Verifica l'header X-Hub-Signature-256 inviato da Meta.
    Confronto in tempo costante per evitare timing attack.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    raw_body = await request.body()

    # Verifica firma solo se l'app secret è configurato (utile in dev
    # senza doverlo impostare subito, ma va sempre attivato in produzione).
    if Config.WHATSAPP_APP_SECRET:
        signature_header = request.headers.get("x-hub-signature-256")
        if not _verify_meta_signature(raw_body, signature_header, Config.WHATSAPP_APP_SECRET):
            print("--- WEBHOOK RIFIUTATO: firma non valida ---")
            return PlainTextResponse("Forbidden", status_code=403)

    payload = json.loads(raw_body)
    print("--- WEBHOOK RICEVUTO DA META ---", payload)

    message = _extract_message(payload)
    if not message:
        return {"status": "ignored"}

    # Passa dal message_buffer: fa debounce (messaggi ravvicinati uniti) e
    # garantisce che non ci siano due elaborazioni parallele per lo stesso
    # numero. Il flush avviene in un thread separato, così non blocchiamo
    # mai l'event loop principale di FastAPI (parse_intent e call_n8n sono
    # chiamate sincrone/bloccanti).
    message_buffer.add_message(message["from"], message, _process_messages_sync)

    return {"status": "accepted"}


def _process_messages_sync(messages: list[dict]):
    """
    Wrapper sincrono usato dal message_buffer (che gira su threading.Timer,
    quindi in un thread separato dal loop asyncio principale).
    Crea un proprio event loop dedicato a questo batch di messaggi.
    """
    asyncio.run(process_messages(messages))


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

    last = messages[-1]
    phone = last["from"]
    business_phone = last["to"]

    combined_text = "\n".join(m["message"].strip() for m in messages if m.get("message"))
    print(f"=== PROCESS {len(messages)} MSG da {phone} ===")
    print(combined_text)

    # 1. Tenant
    print("[DEBUG 1] Cerco il tenant per il numero business:", business_phone)
    tenant = get_tenant_by_whatsapp_number(business_phone)
    print("[DEBUG 2] Risultato tenant:", "Trovato" if tenant else "NON Trovato", tenant)
    if not tenant:
        print("Tenant non trovato per numero:", business_phone)
        return

    tenant_id = tenant["id"]

    # 2. Customer
    print("[DEBUG 3] Cerco o creo il customer per il numero cliente:", phone)
    customer = get_or_create_customer(tenant_id, phone)
    print("[DEBUG 4] Risultato customer:", customer)

    # 3. Conversazione
    print("[DEBUG 5] Recupero la conversazione a DB...")
    conversation, expired = get_or_create_conversation(
        tenant["id"],
        customer["id"],
        phone,
    )
    print("[DEBUG 6] Risultato conversazione:", conversation, "| expired:", expired)

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

    # 5. Knowledge strutturata (sedi, orari, servizi, chiusure, festività)
    knowledge = get_tenant_knowledge(tenant_id)

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
        knowledge=knowledge,
    )

    print("[DEBUG] calendar_id nel context:", context["tenant"]["info"].get("google_calendar_id"))
    # Se la conversazione precedente era scaduta: avvisa e CONTINUA
    # (non interrompere: elaboriamo comunque il messaggio corrente)
    if expired:
        wa_info = tenant.get("info") or {}
        token = wa_info.get("access_token") or Config.WHATSAPP_TOKEN
        phone_id = wa_info.get("phone_number_id") or Config.WHATSAPP_PHONE_NUMBER_ID

        await send_whatsapp_message(
            phone,
            tpl.CONVERSATION_EXPIRED,
            token,
            phone_id,
        )

        recent = append_message(
            conversation["id"],
            role="assistant",
            content=tpl.CONVERSATION_EXPIRED,
            current_messages=conversation.get("recent_messages"),
        )
        conversation["recent_messages"] = recent

        print("[conversation] Conversazione precedente scaduta: avviata nuova conversazione")

    # 7. AI#1 – Intent Extraction
    print("[DEBUG 7] Invoco parse_intent con OpenAI...")

    intent_result = parse_intent(
        message_text=combined_text,
        recent_messages=recent,
        current_workflow=conversation.get("workflow", WORKFLOW_IDLE),
        current_step=conversation.get("step", STEP_NONE),
    )

    context["ai"] = intent_result
    print("Intent:", intent_result)

    # 8. Decisione
    print("[DEBUG 8] Calcolo la decisione con la state machine...")
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
    print("[DEBUG 9] Eseguo l'azione richiesta...")
    reply_text = None

    if decision["action"] == "request_human":
        reply_text = "Ti metto in contatto con un operatore. Un attimo di pazienza…"

    elif decision["action"] == "call_n8n":
        if decision.get("template_key") == "verifying_availability":
            wa_info = tenant.get("info") or {}
            token = wa_info.get("access_token") or Config.WHATSAPP_TOKEN
            phone_id = wa_info.get("phone_number_id") or Config.WHATSAPP_PHONE_NUMBER_ID
            await send_whatsapp_message(phone, tpl.VERIFYING_AVAILABILITY, token, phone_id)

        # Segnale esplicito per n8n: cosa deve fare esattamente
        # ("search_availability" o "create_booking"). Senza questo,
        # n8n non ha modo di distinguere le due chiamate.
        context.setdefault("booking", {})["action"] = decision.get("n8n_action")

        try:
            context = call_n8n(decision["workflow"], context)
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
                result = booking["result"]
                new_collected["last_booking_result"] = result
                if result.get("no_slots"):
                    # Niente slot: segnaliamo lo stato per la prossima risposta
                    # dell'utente (vuole allargare la ricerca o parlare con
                    # un operatore?). _handle_booking_step lo legge al giro
                    # successivo prima di provare a interpretare uno slot.
                    new_collected["no_slots_state"] = (
                        "offer_widen" if result.get("search_was_narrow") else "offer_operator"
                    )
                    new_collected.pop("last_slots", None)
                else:
                    new_collected.pop("no_slots_state", None)

                # Prenotazione creata con successo su Google Calendar (via n8n)
                # → il backend, unico responsabile del DB, salva la riga.
                if decision.get("n8n_action") == "create_booking" and result.get("success"):
                    slot = booking.get("selected_slot") or new_collected.get("selected_slot") or {}
                    create_appointment(
                        tenant_id=tenant.get("id"),
                        customer_id=customer.get("id"),
                        phone_number=phone,
                        service=new_collected.get("service"),
                        appointment_date=slot.get("date") if isinstance(slot, dict) else None,
                        appointment_time=slot.get("time") if isinstance(slot, dict) else None,
                        google_event_id=result.get("google_event_id"),
                    )

            update_conversation(
                conversation["id"],
                collected_data=new_collected,
                step=decision["step"],
            )
            context["collected_data"] = new_collected
            conversation["collected_data"] = new_collected

    else:
        reply_text = _resolve_template(decision, context)

    # 10. Invia risposta finale su WhatsApp
    print("[DEBUG 10] Invio risposta finale all'utente: ", reply_text)
    if reply_text:
        wa_info = tenant.get("info") or {}
        token = wa_info.get("access_token") or Config.WHATSAPP_TOKEN
        phone_id = wa_info.get("phone_number_id") or Config.WHATSAPP_PHONE_NUMBER_ID

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
