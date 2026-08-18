-- ============================================================
-- AI Booking – Schema onboarding multi-sede + auth
-- Eseguire su Supabase dopo 001_initial_schema.sql
-- ============================================================

-- ------------------------------------------------------------
-- 1. TENANTS – colonne aggiuntive
-- ------------------------------------------------------------
ALTER TABLE public.tenants
  ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS specialty text,
  ADD COLUMN IF NOT EXISTS min_lead_hours integer DEFAULT 2,
  ADD COLUMN IF NOT EXISTS max_appointments_per_day integer DEFAULT 12,
  ADD COLUMN IF NOT EXISTS google_calendar_id text,
  ADD COLUMN IF NOT EXISTS onboarding_completed boolean DEFAULT false;

-- Indice per lookup rapido del proprietario
CREATE INDEX IF NOT EXISTS idx_tenants_owner_id ON public.tenants(owner_id);

-- ------------------------------------------------------------
-- 2. LOCATIONS (sedi)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.locations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name        text NOT NULL,
  city        text,
  address     text,
  active      boolean DEFAULT true,
  sort_order  integer DEFAULT 0,
  created_at  timestamptz DEFAULT now(),
  updated_at  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_locations_tenant ON public.locations(tenant_id);

-- ------------------------------------------------------------
-- 3. WORKING HOURS – aggiungi location_id + active
-- ------------------------------------------------------------
ALTER TABLE public.working_hours
  ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES public.locations(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS active boolean DEFAULT true;

CREATE INDEX IF NOT EXISTS idx_working_hours_tenant_day
  ON public.working_hours(tenant_id, day_of_week);

CREATE INDEX IF NOT EXISTS idx_working_hours_location
  ON public.working_hours(location_id);

-- ------------------------------------------------------------
-- 4. SERVICES – buffer, description, sort_order
-- ------------------------------------------------------------
ALTER TABLE public.services
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS buffer_before integer DEFAULT 0,
  ADD COLUMN IF NOT EXISTS buffer_after integer DEFAULT 5,
  ADD COLUMN IF NOT EXISTS sort_order integer DEFAULT 0;

-- ------------------------------------------------------------
-- 5. CALENDAR EXCEPTIONS
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.calendar_exceptions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  date        date NOT NULL,
  type        text NOT NULL CHECK (type IN ('closed', 'closed_period')),
  start_time  time,
  end_time    time,
  reason      text,
  active      boolean DEFAULT true,
  created_at  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_calendar_exceptions_tenant_date
  ON public.calendar_exceptions(tenant_id, date);

-- ------------------------------------------------------------
-- 6. HOLIDAYS (calendario nazionale)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.holidays (
  id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  country   text NOT NULL DEFAULT 'IT',
  date      date NOT NULL,
  name      text NOT NULL,
  UNIQUE (country, date)
);

CREATE TABLE IF NOT EXISTS public.tenant_holidays (
  tenant_id   uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  holiday_id  uuid NOT NULL REFERENCES public.holidays(id) ON DELETE CASCADE,
  enabled     boolean DEFAULT true,
  PRIMARY KEY (tenant_id, holiday_id)
);

-- Seed festività italiane 2026
INSERT INTO public.holidays (country, date, name) VALUES
  ('IT', '2026-01-01', 'Capodanno'),
  ('IT', '2026-01-06', 'Epifania'),
  ('IT', '2026-04-06', 'Lunedì dell''Angelo'),
  ('IT', '2026-04-25', 'Festa della Liberazione'),
  ('IT', '2026-05-01', 'Festa del Lavoro'),
  ('IT', '2026-06-02', 'Festa della Repubblica'),
  ('IT', '2026-08-15', 'Ferragosto'),
  ('IT', '2026-11-01', 'Ognissanti'),
  ('IT', '2026-12-08', 'Immacolata Concezione'),
  ('IT', '2026-12-25', 'Natale'),
  ('IT', '2026-12-26', 'Santo Stefano')
ON CONFLICT (country, date) DO NOTHING;

-- ------------------------------------------------------------
-- 7. APPOINTMENTS – location_id + service_id
-- ------------------------------------------------------------
ALTER TABLE public.appointments
  ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES public.locations(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS service_id uuid REFERENCES public.services(id) ON DELETE SET NULL;

-- ------------------------------------------------------------
-- 8. Indice WhatsApp (già presente in 001, lo lasciamo)
-- ------------------------------------------------------------
-- CREATE INDEX IF NOT EXISTS idx_tenants_whatsapp_number
--   ON public.tenants ((info->>'whatsapp_number'));
