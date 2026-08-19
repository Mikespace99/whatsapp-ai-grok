"""
Logica di decisione del Backend.

Decide:
- workflow
- step
- prossima azione
- template di risposta
- dati raccolti

Principi:
- Le preferenze del cliente sono criteri di ricerca.
- Lo slot scelto è distinto dalle preferenze.
- Gli slot precedentemente mostrati diventano invalidi
  quando il cliente chiede una nuova disponibilità.
- Slot filling implicito: se i dati sono già presenti,
  gli step inutili vengono saltati.
- Le domande laterali non cambiano workflow/step.
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
    INTENT_CHANGE_AVAILABILITY,
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


# ============================================================
# ENTRY POINT
# ============================================================

def decide(intent_result: dict, conversation: dict) -> dict:
    """
    Decide la prossima transizione della conversazione.

    Ritorna:

    {
        "workflow": str,
        "step": str,
        "action": str,
        "template_key": str | None,
        "is_lateral": bool,
        "change_workflow": bool,
        "message_hint": str | None,
        "updated_collected": dict,
        "n8n_action": str | None,
    }
    """

    intent = intent_result.get(
        "intent",
        INTENT_UNCLEAR,
    )

    try:
        confidence = float(
            intent_result.get("confidence") or 0.0
        )
    except (TypeError, ValueError):
        confidence = 0.0

    entities = intent_result.get("entities") or {}
    preferences = intent_result.get("preferences") or {}

    current_workflow = conversation.get(
        "workflow",
        WORKFLOW_IDLE,
    )

    current_step = conversation.get(
        "step",
        STEP_NONE,
    )

    collected = conversation.get(
        "collected_data"
    ) or {}

    decision = {
        "workflow": current_workflow,
        "step": current_step,
        "action": "reply_template",
        "template_key": None,
        "is_lateral": False,
        "change_workflow": False,
        "message_hint": None,
        "updated_collected": deepcopy(collected),
        "n8n_action": None,
    }

    # ========================================================
    # 1. CONFIDENCE BASSA
    # ========================================================

    if confidence < 0.6 or intent == INTENT_UNCLEAR:
        decision["template_key"] = "unclear"
        return decision

    # ========================================================
    # 2. SALUTO
    # ========================================================

    if (
        intent == INTENT_GREETING
        and current_workflow == WORKFLOW_IDLE
    ):
        decision["template_key"] = "welcome"
        return decision

    # ========================================================
    # 3. RICHIESTA OPERATORE
    # ========================================================

    if intent == INTENT_REQUEST_HUMAN:
        decision["workflow"] = WORKFLOW_REQUEST_HUMAN
        decision["step"] = STEP_NONE
        decision["action"] = "request_human"
        decision["change_workflow"] = True
        return decision

    # ========================================================
    # 4. ABBANDONO
    # ========================================================

    if intent == INTENT_ABANDON:
        decision["workflow"] = WORKFLOW_IDLE
        decision["step"] = STEP_NONE
        decision["template_key"] = "abandoned"
        decision["change_workflow"] = True
        decision["updated_collected"] = {}
        return decision

    # ========================================================
    # 5. CAMBIO DISPONIBILITÀ
    #
    # Caso fondamentale:
    #
    # "Nessuno va bene, prova domani"
    #
    # oppure:
    #
    # "Nessuno di questi va bene"
    # ========================================================

    if (
        intent == INTENT_CHANGE_AVAILABILITY
        and current_workflow == WORKFLOW_BOOKING
    ):
        return _handle_change_availability(
            decision=decision,
            preferences=preferences,
            current_step=current_step,
            collected=collected,
        )

    # ========================================================
    # 6. DOMANDA LATERALE
    # ========================================================

    if (
        intent in LATERAL_INTENTS
        and current_workflow
        not in (WORKFLOW_IDLE, WORKFLOW_INFO)
    ):
        decision["is_lateral"] = True
        decision["template_key"] = "lateral_info"
        decision["message_hint"] = "lateral"
        return decision

    # ========================================================
    # 7. INTENT CHE APRE UN ALTRO WORKFLOW
    # ========================================================

    target_workflow = INTENT_TO_WORKFLOW.get(intent)

    if (
        target_workflow
        and target_workflow != current_workflow
    ):
        decision["workflow"] = target_workflow
        decision["change_workflow"] = True

        # Nuovo workflow → reset del precedente.
        decision["updated_collected"] = {}

        _merge_entities_and_preferences(
            decision["updated_collected"],
            entities,
            preferences,
        )

        # ----------------------------------------------------
        # BOOKING
        # ----------------------------------------------------

        if target_workflow == WORKFLOW_BOOKING:
            decision["step"] = STEP_ASKING_SERVICE

            return _handle_booking_step(
                decision=decision,
                intent=intent,
                entities=entities,
                preferences=preferences,
                current_step=STEP_ASKING_SERVICE,
                collected=decision["updated_collected"],
            )

        # ----------------------------------------------------
        # RESCHEDULE
        # ----------------------------------------------------

        if target_workflow == WORKFLOW_RESCHEDULE:
            decision["step"] = STEP_ASKING_DATE
            decision["template_key"] = "ask_reschedule"
            return decision

        # ----------------------------------------------------
        # CANCEL
        # ----------------------------------------------------

        if target_workflow == WORKFLOW_CANCEL:
            decision["step"] = STEP_ASKING_DATE
            decision["template_key"] = "ask_cancel"
            return decision

        # ----------------------------------------------------
        # INFO
        # ----------------------------------------------------

        if target_workflow == WORKFLOW_INFO:
            decision["step"] = STEP_NONE
            decision["template_key"] = "info"
            return decision

        return decision

    # ========================================================
    # 8. WORKFLOW BOOKING ATTIVO
    # ========================================================

    if current_workflow == WORKFLOW_BOOKING:
        return _handle_booking_step(
            decision=decision,
            intent=intent,
            entities=entities,
            preferences=preferences,
            current_step=current_step,
            collected=collected,
        )

    # ========================================================
    # 9. DEFAULT
    # ========================================================

    if current_workflow == WORKFLOW_IDLE:
        decision["template_key"] = "welcome"
    else:
        decision["template_key"] = "unclear"

    return decision


# ============================================================
# CHANGE AVAILABILITY
# ============================================================

def _handle_change_availability(
    decision: dict,
    preferences: dict,
    current_step: str,
    collected: dict,
) -> dict:
    """
    Gestisce il caso in cui il cliente rifiuta gli slot
    correnti e vuole cercare una nuova disponibilità.

    Esempi:

    "Nessuno va bene, prova domani"
        → nuova ricerca immediata

    "Prova venerdì pomeriggio"
        → nuova ricerca immediata

    "Nessuno di questi va bene"
        → chiedi una nuova preferenza temporale
    """

    updated = deepcopy(collected)

    # --------------------------------------------------------
    # Gli slot precedenti non sono più validi.
    # --------------------------------------------------------

    updated.pop("selected_slot", None)
    updated.pop("slot_number", None)
    updated.pop("selected_time", None)

    # --------------------------------------------------------
    # Invalida esplicitamente gli slot precedenti.
    #
    # Non li cancelliamo necessariamente dal DB/conversation:
    # li rendiamo inutilizzabili logicamente.
    # --------------------------------------------------------

    updated["last_slots"] = []
    updated["slot_context_status"] = "invalidated"

    # --------------------------------------------------------
    # Sostituiamo le preferenze temporali.
    #
    # Il servizio invece rimane quello corrente.
    # --------------------------------------------------------

    old_preferences = dict(
        updated.get("preferences") or {}
    )

    new_preferences = _replace_search_preferences(
        old_preferences,
        preferences,
    )

    updated["preferences"] = new_preferences

    # --------------------------------------------------------
    # Registriamo che stiamo cercando una nuova disponibilità.
    # --------------------------------------------------------

    updated["slot_context_status"] = "searching"

    decision["updated_collected"] = updated
    decision["step"] = STEP_SHOWING_SLOTS

    # --------------------------------------------------------
    # Abbiamo una nuova preferenza?
    #
    # Sì → ricerca immediata.
    # No → chiediamo quando cercare.
    # --------------------------------------------------------

    if _has_search_preference(new_preferences):
        decision["action"] = "call_n8n"
        decision["n8n_action"] = N8N_ACTION_SEARCH_AVAILABILITY
        decision["template_key"] = "verifying_availability"
        return decision

    # --------------------------------------------------------
    # Nessuna nuova preferenza:
    #
    # "Nessuno va bene."
    #
    # Non possiamo chiamare N8N con gli stessi criteri.
    # --------------------------------------------------------

    decision["step"] = STEP_ASKING_DATE
    decision["template_key"] = "ask_date"

    return decision


# ============================================================
# BOOKING STEP HANDLER
# ============================================================

def _handle_booking_step(
    decision: dict,
    intent: str,
    entities: dict,
    preferences: dict,
    current_step: str,
    collected: dict,
) -> dict:
    """
    Gestisce l'avanzamento del workflow booking.
    """

    updated = deepcopy(collected)

    _merge_entities_and_preferences(
        updated,
        entities,
        preferences,
    )

    decision["updated_collected"] = updated

    service = updated.get("service")

    prefs = updated.get(
        "preferences"
    ) or {}

    has_date_pref = bool(
        prefs.get("date")
        or prefs.get("date_from")
        or prefs.get("period")
    )

    person_name = updated.get(
        "person_name"
    )

    slot_number = updated.get(
        "slot_number"
    )

    selected_time = updated.get(
        "selected_time"
    )

    # ========================================================
    # STEP: ASKING SERVICE
    # ========================================================

    if (
        current_step in (
            STEP_NONE,
            STEP_ASKING_SERVICE,
        )
        or not service
    ):
        if not service:
            decision["step"] = STEP_ASKING_SERVICE
            decision["template_key"] = "ask_service"
            return decision

        current_step = STEP_ASKING_DATE

    # ========================================================
    # STEP: ASKING DATE / TIME
    # ========================================================

    if current_step in (
        STEP_ASKING_DATE,
        STEP_ASKING_TIME,
    ):
        if service and has_date_pref:
            decision["step"] = STEP_SHOWING_SLOTS
            decision["action"] = "call_n8n"
            decision["n8n_action"] = (
                N8N_ACTION_SEARCH_AVAILABILITY
            )
            decision["template_key"] = (
                "verifying_availability"
            )
            return decision

        decision["step"] = STEP_ASKING_DATE
        decision["template_key"] = "ask_date"
        return decision

    # ========================================================
    # STEP: SHOWING SLOTS
    # ========================================================

    if current_step == STEP_SHOWING_SLOTS:

        # ----------------------------------------------------
        # Caso: ricerca precedente senza risultati.
        # ----------------------------------------------------

        no_slots_state = updated.get(
            "no_slots_state"
        )

        if no_slots_state == "offer_widen":

            if intent in (
                INTENT_CONFIRM,
                INTENT_AFFIRM,
            ):
                updated.pop(
                    "no_slots_state",
                    None,
                )

                prefs = dict(
                    updated.get("preferences")
                    or {}
                )

                prefs["ignore_preferences"] = True
                updated["preferences"] = prefs

                updated["slot_context_status"] = (
                    "searching"
                )

                decision["updated_collected"] = updated
                decision["action"] = "call_n8n"
                decision["n8n_action"] = (
                    N8N_ACTION_SEARCH_AVAILABILITY
                )
                decision["template_key"] = (
                    "verifying_availability"
                )
                return decision

            if intent == INTENT_DENY:
                decision["workflow"] = WORKFLOW_IDLE
                decision["step"] = STEP_NONE
                decision["template_key"] = (
                    "widen_declined"
                )
                decision["updated_collected"] = {}
                decision["change_workflow"] = True
                return decision

            decision["step"] = STEP_SHOWING_SLOTS
            decision["template_key"] = "no_slots_narrow"
            return decision

        # ----------------------------------------------------
        # Caso: proposta operatore.
        # ----------------------------------------------------

        if no_slots_state == "offer_operator":

            if intent in (
                INTENT_CONFIRM,
                INTENT_AFFIRM,
            ):
                decision["workflow"] = (
                    WORKFLOW_REQUEST_HUMAN
                )
                decision["step"] = STEP_NONE
                decision["action"] = "request_human"
                decision["change_workflow"] = True
                return decision

            decision["workflow"] = WORKFLOW_IDLE
            decision["step"] = STEP_NONE
            decision["template_key"] = (
                "widen_declined"
            )
            decision["updated_collected"] = {}
            decision["change_workflow"] = True
            return decision

        # ----------------------------------------------------
        # Scelta di uno slot.
        # ----------------------------------------------------

        last_slots = (
            updated.get("last_slots")
            or []
        )

        resolved_slot = None
        invalid_choice = False

        # ----------------------------------------------------
        # Scelta tramite numero
        # ----------------------------------------------------

        if slot_number is not None:

            try:
                idx = int(slot_number) - 1
            except (
                TypeError,
                ValueError,
            ):
                idx = -1

            if 0 <= idx < len(last_slots):
                resolved_slot = last_slots[idx]
            else:
                invalid_choice = True

        # ----------------------------------------------------
        # Scelta tramite orario
        # ----------------------------------------------------

        elif selected_time:

            wanted = str(
                selected_time
            ).strip()

            for slot in last_slots:

                if not isinstance(
                    slot,
                    dict,
                ):
                    continue

                slot_time = slot.get(
                    "time"
                )

                slot_label = (
                    slot.get("label")
                    or ""
                )

                if (
                    slot_time
                    and (
                        slot_time == wanted
                        or wanted in slot_label
                    )
                ):
                    resolved_slot = slot
                    break

            if resolved_slot is None:
                invalid_choice = True

        # ----------------------------------------------------
        # Unico slot + conferma generica
        # ----------------------------------------------------

        elif (
            intent in (
                INTENT_CONFIRM,
                INTENT_AFFIRM,
            )
            and len(last_slots) == 1
        ):
            resolved_slot = last_slots[0]

        # ----------------------------------------------------
        # Slot trovato
        # ----------------------------------------------------

        if resolved_slot is not None:

            updated["selected_slot"] = (
                resolved_slot
            )

            updated["slot_context_status"] = (
                "selected"
            )

            decision["updated_collected"] = updated
            decision["step"] = (
                STEP_CONFIRMING_SLOT
            )
            decision["template_key"] = (
                "confirm_slot"
            )
            return decision

        # ----------------------------------------------------
        # Scelta non valida
        # ----------------------------------------------------

        if invalid_choice:

            decision["step"] = (
                STEP_SHOWING_SLOTS
            )
            decision["template_key"] = (
                "slot_invalid"
            )
            return decision

        # ----------------------------------------------------
        # Nessuna scelta ancora effettuata
        # ----------------------------------------------------

        decision["step"] = (
            STEP_SHOWING_SLOTS
        )
        decision["template_key"] = (
            "showing_slots"
        )
        return decision

    # ========================================================
    # STEP: CONFIRMING SLOT
    # ========================================================

    if current_step == STEP_CONFIRMING_SLOT:

        if intent in (
            INTENT_CONFIRM,
            INTENT_AFFIRM,
        ):
            decision["updated_collected"] = updated
            decision["step"] = (
                STEP_ASKING_PERSON_NAME
            )
            decision["template_key"] = (
                "ask_person_name"
            )
            return decision

        if intent == INTENT_DENY:

            updated.pop(
                "selected_slot",
                None,
            )

            updated["slot_context_status"] = (
                "active"
            )

            decision["updated_collected"] = updated
            decision["step"] = (
                STEP_SHOWING_SLOTS
            )
            decision["template_key"] = (
                "showing_slots"
            )
            return decision

        decision["step"] = (
            STEP_CONFIRMING_SLOT
        )
        decision["template_key"] = (
            "confirm_slot"
        )
        return decision

    # ========================================================
    # STEP: ASKING PERSON NAME
    # ========================================================

    if current_step == STEP_ASKING_PERSON_NAME:

        if person_name:

            decision["updated_collected"] = updated
            decision["step"] = STEP_COMPLETED
            decision["action"] = "call_n8n"
            decision["n8n_action"] = (
                N8N_ACTION_CREATE_BOOKING
            )
            decision["template_key"] = (
                "booking_confirmed"
            )
            return decision

        decision["step"] = (
            STEP_ASKING_PERSON_NAME
        )
        decision["template_key"] = (
            "ask_person_name"
        )
        return decision

    # ========================================================
    # FALLBACK
    # ========================================================

    decision["template_key"] = "unclear"
    return decision


# ============================================================
# HELPERS
# ============================================================

def _merge_entities_and_preferences(
    updated: dict,
    entities: dict,
    preferences: dict,
):
    """
    Merge sicuro di entities e preferences.
    """

    if entities.get("service"):
        updated["service"] = (
            entities["service"]
        )

    if entities.get("person_name"):
        updated["person_name"] = (
            entities["person_name"]
        )

    if entities.get("slot_number") is not None:
        updated["slot_number"] = (
            entities["slot_number"]
        )

    if entities.get("selected_time"):
        updated["selected_time"] = (
            entities["selected_time"]
        )

    prefs = dict(
        updated.get("preferences")
        or {}
    )

    for key, value in (
        preferences or {}
    ).items():

        if value is not None:
            prefs[key] = value

    if prefs:
        updated["preferences"] = prefs


def _replace_search_preferences(
    old_preferences: dict,
    new_preferences: dict,
) -> dict:
    """
    Sostituisce i criteri temporali della ricerca.

    Il cliente sta chiedendo una nuova disponibilità:
    i vecchi criteri temporali non devono contaminare
    la nuova ricerca.

    Manteniamo eventuali preferenze tecniche già presenti,
    ma sostituiamo i criteri temporali.
    """

    result = dict(old_preferences)

    temporal_keys = {
        "date",
        "date_from",
        "date_to",
        "period",
        "time_preference",
        "exact_time",
    }

    for key in temporal_keys:
        result.pop(key, None)

    for key, value in (
        new_preferences or {}
    ).items():

        if value is not None:
            result[key] = value

    return result


def _has_search_preference(
    preferences: dict,
) -> bool:
    """
    Determina se abbiamo abbastanza informazione
    temporale per effettuare una nuova ricerca.
    """

    return bool(
        preferences.get("date")
        or preferences.get("date_from")
        or preferences.get("period")
        or preferences.get("time_preference")
        or preferences.get("exact_time")
    )
