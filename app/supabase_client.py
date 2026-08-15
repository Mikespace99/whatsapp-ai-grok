from supabase import create_client, Client
from app.config import Config


def get_supabase() -> Client:
    if not Config.SUPABASE_URL or not Config.SUPABASE_KEY:
        raise ValueError("SUPABASE_URL e SUPABASE_SERVICE_KEY devono essere impostate")
    return create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
