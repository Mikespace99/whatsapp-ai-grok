"""
Route web: registrazione, login, onboarding, API di salvataggio.
"""

from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from app.web.auth import (
    register_user,
    login_user,
    get_current_user,
    require_user,
    logout,
    _set_auth_cookies,
    _clear_auth_cookies,
)
from app.repositories.onboarding import (
    get_tenant_by_owner,
    get_full_config,
    update_tenant,
    replace_locations,
    replace_working_hours,
    replace_services,
    replace_exceptions,
    set_tenant_holidays,
    get_holidays,
)

router = APIRouter(tags=["web"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Workaround Python 3.14 / Jinja2 cache key bug
templates.env.cache = None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AuthBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class TenantData(BaseModel):
    business_name: str
    specialty: Optional[str] = None
    assistant_name: Optional[str] = "Assistente"
    timezone: Optional[str] = "Europe/Rome"
    language: Optional[str] = "it"
    min_lead_hours: Optional[int] = 2
    max_appointments_per_day: Optional[int] = 12
    slot_search_days: Optional[int] = 30
    phone: Optional[str] = None          # senza +39
    whatsapp_number: Optional[str] = None  # senza +39
    email: Optional[str] = None
    phone_number_id: Optional[str] = None
    access_token: Optional[str] = None
    google_calendar_id: Optional[str] = None
    info: Optional[dict] = None


class LocationIn(BaseModel):
    id: Optional[str] = None
    name: str
    city: Optional[str] = ""
    address: Optional[str] = ""
    active: bool = True
    sort_order: int = 0


class SlotIn(BaseModel):
    location_id: str
    day_of_week: int
    start_time: str
    end_time: str
    active: bool = True


class ServiceIn(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = ""
    duration_minutes: int = 30
    buffer_before: int = 0
    buffer_after: int = 5
    price: Optional[float] = None
    active: bool = True
    sort_order: int = 0


class ExceptionIn(BaseModel):
    date: str
    type: str = "closed"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    reason: Optional[str] = ""


class HolidayState(BaseModel):
    holiday_id: str
    enabled: bool = True


class FullConfigIn(BaseModel):
    tenant: TenantData
    locations: list[LocationIn] = []
    working_hours: list[SlotIn] = []
    services: list[ServiceIn] = []
    exceptions: list[ExceptionIn] = []
    holidays: list[HolidayState] = []
    mark_completed: bool = False


# ---------------------------------------------------------------------------
# Pagine HTML
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if user:
        tenant = get_tenant_by_owner(user["id"])
        if tenant and tenant.get("onboarding_completed"):
            return RedirectResponse("/onboarding", status_code=302)
        return RedirectResponse("/onboarding", status_code=302)
    return RedirectResponse("/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/onboarding", status_code=302)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": None}
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/onboarding", status_code=302)
    return templates.TemplateResponse(
        request=request, name="register.html", context={"error": None}
    )


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="onboarding.html", context={}
    )


@router.get("/logout")
async def logout_route(response: Response):
    logout(response)
    return RedirectResponse("/login", status_code=302)


# ---------------------------------------------------------------------------
# API Auth
# ---------------------------------------------------------------------------

@router.post("/api/auth/register")
async def api_register(body: AuthBody, response: Response):
    result = register_user(body.email, body.password)
    session = result.get("session") or {}
    if session.get("access_token") and session.get("refresh_token"):
        _set_auth_cookies(response, session["access_token"], session["refresh_token"])
    return {
        "ok": True,
        "user": result["user"],
        "tenant_id": result["tenant"]["id"],
        "onboarding_completed": result["tenant"].get("onboarding_completed", False),
    }


@router.post("/api/auth/login")
async def api_login(body: AuthBody, response: Response):
    result = login_user(body.email, body.password)
    session = result.get("session") or {}
    if session.get("access_token") and session.get("refresh_token"):
        _set_auth_cookies(response, session["access_token"], session["refresh_token"])
    return {
        "ok": True,
        "user": result["user"],
        "tenant_id": result["tenant"]["id"],
        "onboarding_completed": result["tenant"].get("onboarding_completed", False),
    }


@router.post("/api/auth/logout")
async def api_logout(response: Response):
    logout(response)
    return {"ok": True}


@router.get("/api/auth/me")
async def api_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non autenticato")
    tenant = get_tenant_by_owner(user["id"])
    return {
        "user": user,
        "tenant": {
            "id": tenant["id"] if tenant else None,
            "business_name": tenant.get("business_name") if tenant else None,
            "onboarding_completed": tenant.get("onboarding_completed", False) if tenant else False,
        },
    }


# ---------------------------------------------------------------------------
# API Config (onboarding)
# ---------------------------------------------------------------------------

@router.get("/api/config")
async def api_get_config(request: Request):
    user = require_user(request)
    tenant = get_tenant_by_owner(user["id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Studio non trovato")
    config = get_full_config(tenant["id"])
    return config


@router.put("/api/config")
async def api_save_config(body: FullConfigIn, request: Request):
    user = require_user(request)
    tenant = get_tenant_by_owner(user["id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Studio non trovato")

    tenant_id = tenant["id"]
    t = body.tenant

    # Costruisci info jsonb
    info = dict(tenant.get("info") or {})
    if t.phone is not None:
        # salviamo solo le cifre; il +39 lo aggiungiamo in lettura se serve
        info["phone"] = t.phone.strip().replace(" ", "").replace("+39", "")
    if t.whatsapp_number is not None:
        wa = t.whatsapp_number.strip().replace(" ", "").replace("+39", "")
        info["whatsapp_number"] = wa
    if t.email is not None:
        info["email"] = t.email
    if t.phone_number_id is not None:
        info["phone_number_id"] = (t.phone_number_id or "").strip()
    if t.access_token is not None:
        # non sovrascrivere con stringa vuota se già presente (evita cancellazioni accidentali)
        tok = (t.access_token or "").strip()
        if tok:
            info["access_token"] = tok
    if t.google_calendar_id is not None:
        gcal = (t.google_calendar_id or "").strip()
        if gcal:
            info["google_calendar_id"] = gcal

    update_payload = {
        "business_name": t.business_name,
        "specialty": t.specialty,
        "assistant_name": t.assistant_name or "Assistente",
        "timezone": t.timezone or "Europe/Rome",
        "language": t.language or "it",
        "min_lead_hours": t.min_lead_hours if t.min_lead_hours is not None else 2,
        "max_appointments_per_day": t.max_appointments_per_day if t.max_appointments_per_day is not None else 12,
        "slot_search_days": t.slot_search_days if t.slot_search_days is not None else 30,
        "info": info,
    }
    if t.google_calendar_id is not None and (t.google_calendar_id or "").strip():
        update_payload["google_calendar_id"] = t.google_calendar_id.strip()
    if body.mark_completed:
        update_payload["onboarding_completed"] = True

    update_tenant(tenant_id, update_payload)

    # Locations
    locs = replace_locations(tenant_id, [loc.model_dump() for loc in body.locations])

    # Mappa id temporanei → id reali (se il frontend ha inviato id fake)
    # Per semplicità assumiamo che il frontend invii gli id reali dopo il primo salvataggio.
    # Working hours
    replace_working_hours(tenant_id, [s.model_dump() for s in body.working_hours])

    # Services
    replace_services(tenant_id, [s.model_dump() for s in body.services])

    # Exceptions
    replace_exceptions(tenant_id, [e.model_dump() for e in body.exceptions])

    # Holidays
    if body.holidays:
        set_tenant_holidays(tenant_id, [h.model_dump() for h in body.holidays])

    return {"ok": True, "tenant_id": tenant_id}


@router.get("/api/holidays")
async def api_holidays():
    return get_holidays("IT")
