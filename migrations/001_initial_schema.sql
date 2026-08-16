-- ============================================================
-- AI Booking Simple – Schema iniziale multi-tenant
-- ============================================================

-- TENANTS
CREATE TABLE IF NOT EXISTS public.tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_name text NOT NULL,
  assistant_name text DEFAULT 'Assistente',
  timezone text DEFAULT 'Europe/Rome',
  language text DEFAULT 'it',
  slot_search_days integer DEFAULT 30,
  info jsonb DEFAULT '{}'::jsonb,   -- address, parking, email, phone, whatsapp_number...
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- CUSTOMERS
CREATE TABLE IF NOT EXISTS public.customers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  phone_number text NOT NULL,
  full_name text,
  email text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE (tenant_id, phone_number)
);

-- WORKING HOURS
CREATE TABLE IF NOT EXISTS public.working_hours (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  day_of_week integer NOT NULL CHECK (day_of_week >= 1 AND day_of_week <= 7),
  start_time time NOT NULL,
  end_time time NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- SERVICES
CREATE TABLE IF NOT EXISTS public.services (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  duration_minutes integer DEFAULT 30,
  price numeric,
  active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- CONVERSATIONS
CREATE TABLE IF NOT EXISTS public.conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  customer_id uuid NOT NULL REFERENCES public.customers(id) ON DELETE CASCADE,
  phone_number text NOT NULL,
  status text NOT NULL DEFAULT 'active',          -- active | closed
  workflow text NOT NULL DEFAULT 'idle',
  step text NOT NULL DEFAULT 'none',
  collected_data jsonb DEFAULT '{}'::jsonb,
  recent_messages jsonb DEFAULT '[]'::jsonb,
  retry_count integer DEFAULT 0,
  timeout_at timestamptz,
  last_message_at timestamptz,
  created_at timestamptz DEFAULT now(),
  closed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_conversations_active
  ON public.conversations (tenant_id, phone_number, status);

CREATE INDEX IF NOT EXISTS idx_conversations_timeout
  ON public.conversations (last_message_at)
  WHERE status = 'active';

-- APPOINTMENTS
CREATE TABLE IF NOT EXISTS public.appointments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  customer_id uuid REFERENCES public.customers(id),
  phone_number text,
  service text,
  appointment_date date,
  appointment_time time,
  status text DEFAULT 'confirmed',   -- confirmed | cancelled | completed
  notes text,
  google_event_id text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_appointments_tenant_date
  ON public.appointments (tenant_id, appointment_date);

-- Indice per ricerca tenant per numero WhatsApp (campo JSON)
-- Rende la query info->>'whatsapp_number' veloce anche con molti tenant
CREATE INDEX IF NOT EXISTS idx_tenants_whatsapp_number
  ON public.tenants ((info->>'whatsapp_number'));
