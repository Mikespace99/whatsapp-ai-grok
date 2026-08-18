"""
Logica di decisione del Backend.
Decide workflow, step e prossima azione in base a:
- intent corrente
- stato della conversazione

Principi:
- Le preferenze del cliente non sono vincolanti
- Slot filling implicito: se i dati arrivano già nel primo messaggio, si saltano gli step inutili
- Domande laterali non cambiano workflow/step
"""

from copy import deepcopy

from app.constants import (
    WORKFLOW_IDLE,
    WORKFLOW_BOOKING,
    WORKFLOW_RESCHEDULE,
    WORKFLOW_CANCEL,
    WORKFLOW_INFO,
    WORKFLOW_REQUEST_HUMAN,
    STEP_NONE,
    STEP_ASKING_SERVICE,
    STEP_ASKING_DATE,
    STEP_ASKING_TIME,
    STEP_SHOWING_SLOTS,
    STEP_CONFIRMING_SLOT,
    STEP_ASKING_PERSON_NAME,
    STEP_CONFIRMING,
    STEP_COMPLETED,
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
    INTENT_TO_WORKFLOW,
    LATERAL_INTENTS,
    N8N_ACTION_SEARCH_AVAILABILITY,
    N8N_ACTION_CREATE_BOOKING,
)


def decide(intent_result: dict, conversation: dict) -> dict:
    """
    Ritorna un dict con la decisione:
    {
      "workflow": str,
      "step": str,
      "action": str,          # "reply_template" | "call_n8n" | "reply_ai" | "request_human"
      "template_key": str|None,
      "is_lateral": bool,
      "change_workflow": bool,
      "message_hint": str|None,
      "updated_collected": dict
    }
    """
    intent = intent_result.get("intent", INTENT_UNCLEAR)
    confidence = float(intent_result.get("confidence") or 0.0)
    entities = intent_result.get("entities") or {}
    preferences = intent_result.get("preferences") or {}

    current_workflow = conversation.get("workflow", WORKFLOW_IDLE)
    current_step = conversation.get("step", STEP_NONE)
    collected = conversation.get("collected_data") or {}

    decision = {
        "workflow": current_workflow,
        "step": current_step,
        "action": "reply_template",
        "template_key": None,
        "is_lateral": False,
        "change_workflow": False,
        "message_hint": None,
        "updated_collected": deepcopy(collected),
        "n8n_action": None,  # "search_availability" | "create_booking" (solo se action == "call_n8n")
    }

    # --------------------------------------------------
    # 1. Confidence bassa → unclear
    # --------------------------------------------------
    if confidence < 0.6 or intent == INTENT_UNCLEAR:
        decision["template_key"] = "unclear"
        return decision

    # --------------------------------------------------
    # 2. Saluto (solo se siamo idle)
    # --------------------------------------------------
    if intent == INTENT_GREETING and current_workflow == WORKFLOW_IDLE:
        decision["template_key"] = "welcome"
        return decision

    # --------------------------------------------------
    # 3. Richiesta umano
    # --------------------------------------------------
    if intent == INTENT_REQUEST_HUMAN:
        decision["workflow"] = WORKFLOW_REQUEST_HUMAN
        decision["step"] = STEP_NONE
        decision["action"] = "request_human"
        decision["change_workflow"] = True
        return decision

    # --------------------------------------------------
    # 4. Abbandono esplicito
    # --------------------------------------------------
    if intent == INTENT_ABANDON:
        decision["workflow"] = WORKFLOW_IDLE
        decision["step"] = STEP_NONE
        decision["template_key"] = "abandoned"
        decision["change_workflow"] = True
        decision["updated_collected"] = {}
        return decision

    # --------------------------------------------------
    # 5. Domanda laterale mentre c'è un flusso attivo
    # --------------------------------------------------
    if intent in LATERAL_INTENTS and current_workflow not in (WORKFLOW_IDLE, WORKFLOW_INFO):
        decision["is_lateral"] = True
        decision["template_key"] = "lateral_info"
        decision["message_hint"] = "lateral"
        # NON cambiamo workflow/step, NON tocchiamo collected_data
        return decision

    # --------------------------------------------------
    # 6. Nuovo intent che richiede un workflow diverso
    # --------------------------------------------------
    target_workflow = INTENT_TO_WORKFLOW.get(intent)

    if target_workflow and target_workflow != current_workflow:
        decision["workflow"] = target_workflow
        decision["change_workflow"] = True
        decision["updated_collected"] = {}  # reset del flusso precedente

        # Merge subito eventuali entities/preferences del messaggio corrente
        # (slot filling implicito fin dal primo turno)
        _merge_entities_and_preferences(
            decision["updated_collected"], entities, preferences
        )

        if target_workflow == WORKFLOW_BOOKING:
            # Passiamo subito al gestore booking così può saltare gli step
            # di cui abbiamo già i dati
            decision["step"] = STEP_ASKING_SERVICE  # verrà eventualmente avanzato
            return _handle_booking_step(
                decision, intent, entities, preferences,
                STEP_ASKING_SERVICE, decision["updated_collected"]
            )

        if target_workflow == WORKFLOW_RESCHEDULE:
            decision["step"] = STEP_ASKING_DATE
            decision["template_key"] = "ask_reschedule"
            return decision

        if target_workflow == WORKFLOW_CANCEL:
            decision["step"] = STEP_ASKING_DATE
            decision["template_key"] = "ask_cancel"
            return decision

        if target_workflow == WORKFLOW_INFO:
            decision["step"] = STEP_NONE
            decision["template_key"] = "info"
            return decision

        return decision

    # --------------------------------------------------
    # 7. Siamo già dentro un workflow → gestori dedicati
    # --------------------------------------------------
    if current_workflow == WORKFLOW_BOOKING:
        return _handle_booking_step(
            decision, intent, entities, preferences, current_step, collected
        )

    # TODO: _handle_reschedule_step / _handle_cancel_step

    # --------------------------------------------------
    # 8. Default
    # --------------------------------------------------
    if current_workflow == WORKFLOW_IDLE:
        decision["template_key"] = "welcome"
    else:
        decision["template_key"] = "unclear"

    return decision


# ============================================================
# HELPERS
# ============================================================

def _merge_entities_and_preferences(updated: dict, entities: dict, preferences: dict):
    """Merge sicuro (senza sporcare oggetti condivisi)."""
    if entities.get("service"):
        updated["service"] = entities["service"]
    if entities.get("person_name"):
        updated["person_name"] = entities["person_name"]
    if entities.get("slot_number") is not None:
        updated["slot_number"] = entities["slot_number"]
    if entities.get("selected_time"):
        updated["selected_time"] = entities["selected_time"]

    # Copia esplicita delle preferenze per evitare shallow-copy issues
    prefs = dict(updated.get("preferences") or {})
    for k, v in (preferences or {}).items():
        if v is not None:
            prefs[k] = v
    if prefs:
        updated["preferences"] = prefs


def _handle_booking_step(
    decision: dict,
    intent: str,
    entities: dict,
    preferences: dict,
    current_step: str,
    collected: dict,
) -> dict:
    """
    Avanza il flusso di booking.
    Supporta slot-filling implicito: se i dati ci sono già, salta gli step.
    """
    updated = deepcopy(collected)
    _merge_entities_and_preferences(updated, entities, preferences)
    decision["updated_collected"] = updated

    service = updated.get("service")
    prefs = updated.get("preferences") or {}
    has_date_pref = bool(
        prefs.get("date") or prefs.get("period") or prefs.get("date_from")
    )
    person_name = updated.get("person_name")
    slot_number = updated.get("slot_number")
    selected_time = updated.get("selected_time")

    # --------------------------------------------------
    # Step: chiedere servizio
    # --------------------------------------------------
    if current_step in (STEP_NONE, STEP_ASKING_SERVICE) or not service:
        if not service:
            decision["step"] = STEP_ASKING_SERVICE
            decision["template_key"] = "ask_service"
            return decision
        # Abbiamo il servizio → proseguiamo verso la data
        current_step = STEP_ASKING_DATE

    # --------------------------------------------------
    # Step: chiedere data / preferenza temporale
    # --------------------------------------------------
    if current_step in (STEP_ASKING_DATE, STEP_ASKING_TIME):
        if service and has_date_pref:
            # Dati sufficienti → cerca slot
            decision["step"] = STEP_SHOWING_SLOTS
            decision["action"] = "call_n8n"
            decision["n8n_action"] = N8N_ACTION_SEARCH_AVAILABILITY
            decision["template_key"] = "verifying_availability"
            return decision
        else:
            decision["step"] = STEP_ASKING_DATE
            decision["template_key"] = "ask_date"
            return decision

    # --------------------------------------------------
    # Step: mostriamo gli slot e aspettiamo la scelta
    # --------------------------------------------------
    if current_step == STEP_SHOWING_SLOTS:
        no_slots_state = updated.get("no_slots_state")

        # --- Caso: la ricerca precedente non ha trovato nulla, stiamo
        #     aspettando "vuoi allargare?" / "vuoi un operatore?" ---
        if no_slots_state == "offer_widen":
            if intent in (INTENT_CONFIRM, INTENT_AFFIRM):
                updated.pop("no_slots_state", None)
                prefs = dict(updated.get("preferences") or {})
                prefs["ignore_preferences"] = True
                updated["preferences"] = prefs
                decision["updated_collected"] = updated
                decision["action"] = "call_n8n"
                decision["n8n_action"] = N8N_ACTION_SEARCH_AVAILABILITY
                decision["template_key"] = "verifying_availability"
                return decision
            if intent == INTENT_DENY:
                decision["workflow"] = WORKFLOW_IDLE
                decision["step"] = STEP_NONE
                decision["template_key"] = "widen_declined"
                decision["updated_collected"] = {}
                decision["change_workflow"] = True
                return decision
            # Risposta non chiara → ripeti la stessa domanda
            decision["step"] = STEP_SHOWING_SLOTS
            decision["template_key"] = "no_slots_narrow"
            return decision

        if no_slots_state == "offer_operator":
            if intent in (INTENT_CONFIRM, INTENT_AFFIRM):
                decision["workflow"] = WORKFLOW_REQUEST_HUMAN
                decision["step"] = STEP_NONE
                decision["action"] = "request_human"
                decision["change_workflow"] = True
                return decision
            decision["workflow"] = WORKFLOW_IDLE
            decision["step"] = STEP_NONE
            decision["template_key"] = "widen_declined"
            decision["updated_collected"] = {}
            decision["change_workflow"] = True
            return decision

        # --- Caso normale: l'utente sta scegliendo tra gli slot mostrati ---
        last_slots = updated.get("last_slots") or []

        resolved_slot = None
        invalid_choice = False

        if slot_number is not None:
            # "il secondo" → indice 1-based
            try:
                idx = int(slot_number) - 1
            except (TypeError, ValueError):
                idx = -1
            if 0 <= idx < len(last_slots):
                resolved_slot = last_slots[idx]
            else:
                invalid_choice = True

        elif selected_time:
            # Es. "alle 16:00" → cerchiamo lo slot con lo stesso orario
            wanted = str(selected_time).strip()
            for s in last_slots:
                slot_time = s.get("time") if isinstance(s, dict) else None
                if slot_time and (slot_time == wanted or wanted in (s.get("label") or "")):
                    resolved_slot = s
                    break
            if resolved_slot is None:
                invalid_choice = True

        elif intent in (INTENT_CONFIRM, INTENT_AFFIRM) and len(last_slots) == 1:
            # Un solo slot disponibile e il cliente conferma genericamente
            resolved_slot = last_slots[0]

        if resolved_slot is not None:
            updated["selected_slot"] = resolved_slot
            decision["updated_collected"] = updated
            decision["step"] = STEP_CONFIRMING_SLOT
            decision["template_key"] = "confirm_slot"
            return decision

        if invalid_choice:
            decision["step"] = STEP_SHOWING_SLOTS
            decision["template_key"] = "slot_invalid"
            return decision

        # L'utente non ha ancora scelto → rimaniamo qui
        decision["step"] = STEP_SHOWING_SLOTS
        decision["template_key"] = "showing_slots"
        return decision

    # --------------------------------------------------
    # Step: conferma dello slot scelto (checkpoint 1/2)
    # --------------------------------------------------
    if current_step == STEP_CONFIRMING_SLOT:
        if intent in (INTENT_CONFIRM, INTENT_AFFIRM):
            decision["updated_collected"] = updated
            decision["step"] = STEP_ASKING_PERSON_NAME
            decision["template_key"] = "ask_person_name"
            return decision
        if intent == INTENT_DENY:
            # Torna alla lista già mostrata, senza rifare la ricerca
            updated.pop("selected_slot", None)
            decision["updated_collected"] = updated
            decision["step"] = STEP_SHOWING_SLOTS
            decision["template_key"] = "showing_slots"
            return decision
        # Risposta non chiara → richiedi di nuovo la conferma
        decision["step"] = STEP_CONFIRMING_SLOT
        decision["template_key"] = "confirm_slot"
        return decision

    # --------------------------------------------------
    # Step: chiedere nome (checkpoint 2/2, poi si blocca subito)
    # --------------------------------------------------
    if current_step == STEP_ASKING_PERSON_NAME:
        if person_name:
            decision["updated_collected"] = updated
            decision["step"] = STEP_COMPLETED
            decision["action"] = "call_n8n"
            decision["n8n_action"] = N8N_ACTION_CREATE_BOOKING
            decision["template_key"] = "booking_confirmed"
            return decision
        decision["step"] = STEP_ASKING_PERSON_NAME
        decision["template_key"] = "ask_person_name"
        return decision

    # --------------------------------------------------
    # Fallback
    # --------------------------------------------------
    decision["template_key"] = "unclear"
    return decision
