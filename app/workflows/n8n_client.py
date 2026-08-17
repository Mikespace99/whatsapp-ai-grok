"""
Client per chiamare i webhook n8n.
Passa il Context e si aspetta un aggiornamento soprattutto della sezione booking.

Nota architetturale:
process_messages gira in un thread (debounce). Per questo usiamo un client
sincrono con timeout corto. Se n8n non risponde in tempo, falliamo in modo
controllato e lasciamo che main.py mostri il fallback tecnico.
"""

import logging
import httpx
from app.config import Config
from app.constants import (
    WORKFLOW_BOOKING,
    WORKFLOW_RESCHEDULE,
    WORKFLOW_CANCEL,
)

logger = logging.getLogger(__name__)

# Timeout corto: meglio fallire e rispondere all'utente che tenere thread appesi
N8N_TIMEOUT_SECONDS = 15.0


def _get_webhook(workflow: str) -> str | None:
    mapping = {
        WORKFLOW_BOOKING: Config.N8N_BOOKING_WEBHOOK,
        WORKFLOW_RESCHEDULE: Config.N8N_RESCHEDULE_WEBHOOK,
        WORKFLOW_CANCEL: Config.N8N_CANCEL_WEBHOOK,
    }
    return mapping.get(workflow) or Config.N8N_AVAILABILITY_WEBHOOK


def _safe_merge_booking(context: dict, data: dict) -> dict:
    """
    Aggiorna SOLO la sezione booking (e campi noti).
    Non fa mai context.update(data) cieco per evitare di corrompere
    tenant / conversation / collected_data.
    """
    booking = dict(context.get("booking") or {})

    if not isinstance(data, dict):
        booking["result"] = {
            "success": False,
            "error": "invalid_n8n_payload",
        }
        context["booking"] = booking
        return context

    # Caso 1: n8n restituisce { "booking": { ... } }
    if isinstance(data.get("booking"), dict):
        incoming = data["booking"]
        for key in ("candidate_slots", "selected_slot", "matched_preferences", "result"):
            if key in incoming:
                booking[key] = incoming[key]
        context["booking"] = booking
        return context

    # Caso 2: n8n restituisce direttamente i campi booking a root
    if any(k in data for k in ("candidate_slots", "selected_slot", "result", "matched_preferences")):
        for key in ("candidate_slots", "selected_slot", "matched_preferences", "result"):
            if key in data:
                booking[key] = data[key]
        context["booking"] = booking
        return context

    # Caso 3: payload sconosciuto → non tocchiamo il context originale
    logger.warning(f"[n8n] Payload non riconosciuto, ignoro merge: keys={list(data.keys())}")
    booking["result"] = {
        "success": False,
        "error": "unrecognized_n8n_payload",
        "keys": list(data.keys()),
    }
    context["booking"] = booking
    return context


def call_n8n(workflow: str, context: dict) -> dict:
    """
    Chiama il webhook n8n corrispondente al workflow.
    Ritorna sempre un context (anche in caso di errore).
    NON solleva eccezioni verso il chiamante.
    """
    url = _get_webhook(workflow)
    if not url:
        logger.error(f"[n8n] Nessun webhook configurato per workflow={workflow}")
        context.setdefault("booking", {})["result"] = {
            "success": False,
            "error": "webhook_not_configured",
        }
        return context

    try:
        with httpx.Client(timeout=N8N_TIMEOUT_SECONDS) as client:
            resp = client.post(url, json=context)

            if resp.status_code >= 400:
                logger.error(f"[n8n] HTTP {resp.status_code}: {resp.text[:500]}")
                context.setdefault("booking", {})["result"] = {
                    "success": False,
                    "error": f"http_{resp.status_code}",
                }
                return context

            try:
                data = resp.json()
            except Exception:
                logger.error("[n8n] Risposta non JSON")
                context.setdefault("booking", {})["result"] = {
                    "success": False,
                    "error": "invalid_json",
                }
                return context

            return _safe_merge_booking(context, data)

    except httpx.TimeoutException:
        logger.error(f"[n8n] Timeout dopo {N8N_TIMEOUT_SECONDS}s")
        context.setdefault("booking", {})["result"] = {
            "success": False,
            "error": "timeout",
        }
        return context
    except httpx.RequestError as e:
        logger.error(f"[n8n] Errore di rete: {e}")
        context.setdefault("booking", {})["result"] = {
            "success": False,
            "error": f"network: {e}",
        }
        return context
    except Exception as e:
        logger.error(f"[n8n] Errore imprevisto: {e}")
        context.setdefault("booking", {})["result"] = {
            "success": False,
            "error": str(e),
        }
        return context
