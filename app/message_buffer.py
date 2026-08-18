"""
Debounce + Lock per messaggi WhatsApp.

- Raggruppa messaggi ravvicinati dello stesso numero (debounce)
- Evita elaborazioni parallele sullo stesso numero (lock)
"""

import threading
import time
from collections import defaultdict
from typing import Callable

from app.config import Config


# Secondi di attesa dopo l'ultimo messaggio prima di processare
DEBOUNCE_SECONDS = float(
    getattr(Config, "MESSAGE_DEBOUNCE_SECONDS", 10)
)

# Massimo messaggi tenuti in coda per un numero
MAX_BUFFERED_MESSAGES = 6


class MessageBuffer:
    def __init__(self):
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._timers: dict[str, threading.Timer] = {}
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._processing: set[str] = set()
        self._global_lock = threading.Lock()

    def add_message(self, phone: str, message: dict, process_fn: Callable):
        """
        Aggiunge un messaggio al buffer del numero.
        Se è il primo, avvia il timer.
        Se ne arrivano altri, resetta il timer.
        Quando il timer scade → processa tutto insieme.
        """
        with self._global_lock:
            buf = self._buffers[phone]
            buf.append(message)

            # Tieni solo gli ultimi N
            if len(buf) > MAX_BUFFERED_MESSAGES:
                self._buffers[phone] = buf[-MAX_BUFFERED_MESSAGES:]

            # Cancella eventuale timer precedente
            old_timer = self._timers.get(phone)
            if old_timer:
                old_timer.cancel()

            # Nuovo timer
            timer = threading.Timer(
                DEBOUNCE_SECONDS,
                self._flush,
                args=(phone, process_fn),
            )
            timer.daemon = True
            self._timers[phone] = timer
            timer.start()

            print(f"[buffer] {phone}: {len(self._buffers[phone])} msg in coda "
                  f"(attendo {DEBOUNCE_SECONDS}s)")

    def _flush(self, phone: str, process_fn: Callable):
        """Chiamato allo scadere del timer. Processa i messaggi accumulati."""
        with self._global_lock:
            messages = self._buffers.pop(phone, [])
            self._timers.pop(phone, None)

            if not messages:
                return

            # Se già in processing, rimetti in coda e riprova tra poco
            if phone in self._processing:
                print(f"[buffer] {phone}: già in processing, re-accodo {len(messages)} msg")
                self._buffers[phone].extend(messages)
                timer = threading.Timer(2.0, self._flush, args=(phone, process_fn))
                timer.daemon = True
                self._timers[phone] = timer
                timer.start()
                return

            self._processing.add(phone)

        try:
            print(f"[buffer] {phone}: processuo {len(messages)} messaggi insieme")
            process_fn(messages)
        except Exception as e:
            print(f"[buffer] Errore processing {phone}: {e}")
        finally:
            with self._global_lock:
                self._processing.discard(phone)

                # Se nel frattempo sono arrivati altri messaggi, avvia un nuovo ciclo
                if self._buffers.get(phone):
                    timer = threading.Timer(
                        DEBOUNCE_SECONDS,
                        self._flush,
                        args=(phone, process_fn),
                    )
                    timer.daemon = True
                    self._timers[phone] = timer
                    timer.start()


# Istanza globale (una per processo)
message_buffer = MessageBuffer()
