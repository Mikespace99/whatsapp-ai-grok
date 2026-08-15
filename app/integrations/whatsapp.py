"""
Client per inviare messaggi WhatsApp tramite Cloud API.
Versione sincrona, robusta, con gestione errori.
"""

import logging
import httpx
from app.config import Config

logger = logging.getLogger(__name__)

# Versione Graph API stabile
GRAPH_API_VERSION = "v21.0"


def send_whatsapp_message(to_phone: str, text: str) -> dict | None:
    """
    Invia un messaggio di testo.
    Ritorna la risposta di Meta in caso di successo, None in caso di errore.
    NON solleva eccezioni verso il chiamante.
    """
    if not Config.WHATSAPP_TOKEN or not Config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error("[whatsapp] Token o Phone Number ID mancanti")
        return None

    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{Config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {Config.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)

            print("=== WHATSAPP SEND ===")
            print(f"Status: {resp.status_code}")
            print(f"Body: {resp.text[:500]}")

            if resp.status_code >= 400:
                logger.error(
                    f"[whatsapp] Errore Meta {resp.status_code}: {resp.text}"
                )
                return None

            return resp.json()

    except httpx.TimeoutException as e:
        logger.error(f"[whatsapp] Timeout: {e}")
        return None
    except httpx.RequestError as e:
        logger.error(f"[whatsapp] Errore di rete: {e}")
        return None
    except Exception as e:
        logger.error(f"[whatsapp] Errore imprevisto: {e}")
        return None
