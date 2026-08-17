from functools import lru_cache
from supabase import create_client, Client
from app.config import Config


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """
    Client Supabase cachato (singleton): evita di aprire una nuova
    connessione/istanza a ogni chiamata a un repository.
    """
    if not Config.SUPABASE_URL or not Config.SUPABASE_KEY:
        raise ValueError("SUPABASE_URL e SUPABASE_SERVICE_KEY devono essere impostate")
    return create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
