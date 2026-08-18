"""
Auth helpers – registrazione e login via Supabase Auth.
Usa il client con service_role per creare utenti e poi restituisce
access_token / refresh_token al frontend (cookie httpOnly).
"""

from __future__ import annotations
from fastapi import HTTPException, Response, Request
from app.supabase_client import get_supabase
from app.repositories.onboarding import (
    get_tenant_by_owner,
    create_tenant_for_owner,
)


COOKIE_ACCESS = "sb_access_token"
COOKIE_REFRESH = "sb_refresh_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 giorni


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key=COOKIE_ACCESS,
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        path="/",
    )
    response.set_cookie(
        key=COOKIE_REFRESH,
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(COOKIE_ACCESS, path="/")
    response.delete_cookie(COOKIE_REFRESH, path="/")


def register_user(email: str, password: str) -> dict:
    """
    Crea utente su Supabase Auth + riga tenant.
    Ritorna {user, session, tenant}.
    """
    sb = get_supabase()
    try:
        auth_res = sb.auth.sign_up({"email": email, "password": password})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Registrazione fallita: {e}")

    if not auth_res.user:
        raise HTTPException(status_code=400, detail="Registrazione fallita: nessun utente creato")

    user = auth_res.user
    session = auth_res.session

    # Crea il tenant collegato
    tenant = create_tenant_for_owner(user.id, email)

    return {
        "user": {"id": user.id, "email": user.email},
        "session": {
            "access_token": session.access_token if session else None,
            "refresh_token": session.refresh_token if session else None,
        },
        "tenant": tenant,
    }


def login_user(email: str, password: str) -> dict:
    sb = get_supabase()
    try:
        auth_res = sb.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        raise HTTPException(status_code=401, detail="Email o password non corretti")

    if not auth_res.user or not auth_res.session:
        raise HTTPException(status_code=401, detail="Email o password non corretti")

    user = auth_res.user
    session = auth_res.session

    tenant = get_tenant_by_owner(user.id)
    if not tenant:
        # Utente Auth esistente ma senza tenant → crealo
        tenant = create_tenant_for_owner(user.id, email)

    return {
        "user": {"id": user.id, "email": user.email},
        "session": {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        },
        "tenant": tenant,
    }


def get_current_user(request: Request) -> dict | None:
    """
    Estrae l'utente dal cookie access_token.
    Ritorna {id, email} oppure None.
    """
    token = request.cookies.get(COOKIE_ACCESS)
    if not token:
        return None

    sb = get_supabase()
    try:
        user_res = sb.auth.get_user(token)
        if user_res and user_res.user:
            return {"id": user_res.user.id, "email": user_res.user.email}
    except Exception:
        pass
    return None


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non autenticato")
    return user


def logout(response: Response) -> None:
    _clear_auth_cookies(response)
