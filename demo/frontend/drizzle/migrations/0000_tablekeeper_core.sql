CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TYPE public.app_role AS ENUM ('admin', 'staff', 'user');

CREATE TABLE public.user_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  role public.app_role NOT NULL,
  UNIQUE (user_id, role)
);
GRANT SELECT ON public.user_roles TO authenticated;
GRANT ALL ON public.user_roles TO service_role;
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "read own roles" ON public.user_roles FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE OR REPLACE FUNCTION public.has_role(_user_id uuid, _role public.app_role)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM public.user_roles WHERE user_id = _user_id AND role = _role)
$$;

CREATE TABLE public.restaurants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  name text NOT NULL,
  cuisine text NOT NULL,
  city text NOT NULL,
  timezone text NOT NULL,
  opens_at time NOT NULL DEFAULT '17:00',
  closes_at time NOT NULL DEFAULT '23:00',
  slot_minutes int NOT NULL DEFAULT 90,
  description text NOT NULL DEFAULT ''
);
GRANT SELECT ON public.restaurants TO anon, authenticated;
GRANT ALL ON public.restaurants TO service_role;
ALTER TABLE public.restaurants ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public read restaurants" ON public.restaurants FOR SELECT TO anon, authenticated USING (true);

CREATE TABLE public.dining_tables (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id uuid NOT NULL REFERENCES public.restaurants(id) ON DELETE CASCADE,
  label text NOT NULL,
  seats int NOT NULL CHECK (seats > 0),
  UNIQUE (restaurant_id, label)
);
GRANT SELECT ON public.dining_tables TO anon, authenticated;
GRANT ALL ON public.dining_tables TO service_role;
ALTER TABLE public.dining_tables ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public read tables" ON public.dining_tables FOR SELECT TO anon, authenticated USING (true);

CREATE TABLE public.bookings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  table_id uuid NOT NULL REFERENCES public.dining_tables(id) ON DELETE CASCADE,
  restaurant_id uuid NOT NULL REFERENCES public.restaurants(id) ON DELETE CASCADE,
  user_id uuid NOT NULL,
  guest_name text NOT NULL,
  party_size int NOT NULL CHECK (party_size > 0),
  slot tstzrange NOT NULL,
  status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','cancelled')),
  idempotency_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, idempotency_key),
  CONSTRAINT no_double_booking EXCLUDE USING gist (table_id WITH =, slot WITH &&) WHERE (status = 'confirmed')
);
CREATE INDEX bookings_user_idx ON public.bookings(user_id);
GRANT SELECT ON public.bookings TO authenticated;
GRANT ALL ON public.bookings TO service_role;
ALTER TABLE public.bookings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "own bookings" ON public.bookings FOR SELECT TO authenticated USING (user_id = auth.uid());
CREATE POLICY "staff read bookings" ON public.bookings FOR SELECT TO authenticated USING (public.has_role(auth.uid(), 'staff') OR public.has_role(auth.uid(), 'admin'));

-- Availability: slots generated in restaurant-local wall time, converted to UTC (DST-safe)
CREATE OR REPLACE FUNCTION public.available_slots(_restaurant_id uuid, _date date, _party int)
RETURNS TABLE (starts_at timestamptz, free_tables int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  WITH r AS (SELECT * FROM restaurants WHERE id = _restaurant_id),
  local_slots AS (
    SELECT ((_date + t::time) AT TIME ZONE r.timezone) AS s, r.slot_minutes AS m
    FROM r, generate_series(
      (_date + r.opens_at)::timestamp,
      (_date + r.closes_at)::timestamp - make_interval(mins => r.slot_minutes),
      interval '30 minutes') AS t
  )
  SELECT ls.s, (
    SELECT count(*)::int FROM dining_tables dt
    WHERE dt.restaurant_id = _restaurant_id AND dt.seats >= _party
      AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.table_id = dt.id AND b.status = 'confirmed'
        AND b.slot && tstzrange(ls.s, ls.s + make_interval(mins => ls.m), '[)'))
  )
  FROM local_slots ls
  WHERE ls.s > now()
  ORDER BY ls.s
$$;
GRANT EXECUTE ON FUNCTION public.available_slots(uuid, date, int) TO anon, authenticated;

-- The single write path. Database constraints are the source of truth.
CREATE OR REPLACE FUNCTION public.book_table(_restaurant_id uuid, _starts_at timestamptz, _party int, _guest_name text, _idempotency_key text, _table_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  uid uuid := auth.uid();
  r restaurants%ROWTYPE;
  existing bookings%ROWTYPE;
  local_t time;
  rng tstzrange;
  cand uuid;
  new_id uuid;
BEGIN
  IF uid IS NULL THEN RAISE EXCEPTION 'not signed in'; END IF;
  IF _idempotency_key IS NULL OR length(_idempotency_key) < 8 THEN RAISE EXCEPTION 'invalid request key'; END IF;

  SELECT * INTO existing FROM bookings WHERE user_id = uid AND idempotency_key = _idempotency_key;
  IF FOUND THEN RETURN jsonb_build_object('status','replayed','booking_id',existing.id,'table_id',existing.table_id); END IF;

  SELECT * INTO r FROM restaurants WHERE id = _restaurant_id;
  IF NOT FOUND THEN RETURN jsonb_build_object('status','rejected','reason','unknown restaurant'); END IF;
  IF _party < 1 OR _party > 20 THEN RETURN jsonb_build_object('status','rejected','reason','invalid party size'); END IF;
  IF _starts_at <= now() THEN RETURN jsonb_build_object('status','rejected','reason','time is in the past'); END IF;
  local_t := (_starts_at AT TIME ZONE r.timezone)::time;
  IF local_t < r.opens_at OR local_t + make_interval(mins => r.slot_minutes) > r.closes_at THEN
    RETURN jsonb_build_object('status','rejected','reason','outside opening hours');
  END IF;
  rng := tstzrange(_starts_at, _starts_at + make_interval(mins => r.slot_minutes), '[)');

  FOR cand IN
    SELECT dt.id FROM dining_tables dt
    WHERE dt.restaurant_id = r.id AND dt.seats >= _party AND (_table_id IS NULL OR dt.id = _table_id)
    ORDER BY dt.seats, dt.label
  LOOP
    BEGIN
      INSERT INTO bookings (table_id, restaurant_id, user_id, guest_name, party_size, slot, idempotency_key)
      VALUES (cand, r.id, uid, left(coalesce(nullif(trim(_guest_name),''),'Guest'),80), _party, rng, _idempotency_key)
      RETURNING id INTO new_id;
      RETURN jsonb_build_object('status','confirmed','booking_id',new_id,'table_id',cand);
    EXCEPTION
      WHEN exclusion_violation THEN CONTINUE;
      WHEN unique_violation THEN
        SELECT * INTO existing FROM bookings WHERE user_id = uid AND idempotency_key = _idempotency_key;
        RETURN jsonb_build_object('status','replayed','booking_id',existing.id,'table_id',existing.table_id);
    END;
  END LOOP;
  RETURN jsonb_build_object('status','conflict','reason','no table free at that time');
END $$;
REVOKE EXECUTE ON FUNCTION public.book_table(uuid, timestamptz, int, text, text, uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.book_table(uuid, timestamptz, int, text, text, uuid) TO authenticated;

CREATE OR REPLACE FUNCTION public.cancel_booking(_booking_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE bookings SET status = 'cancelled'
  WHERE id = _booking_id AND status = 'confirmed'
    AND (user_id = auth.uid() OR public.has_role(auth.uid(),'staff') OR public.has_role(auth.uid(),'admin'));
  RETURN FOUND;
END $$;
REVOKE EXECUTE ON FUNCTION public.cancel_booking(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.cancel_booking(uuid) TO authenticated;

-- Factory evidence tables
CREATE TABLE public.factory_stages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  number int UNIQUE NOT NULL,
  title text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','done')),
  summary text NOT NULL DEFAULT ''
);
CREATE TABLE public.seats (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  role text NOT NULL,
  model text NOT NULL DEFAULT '',
  mandate text NOT NULL,
  sort int NOT NULL DEFAULT 0
);
CREATE TABLE public.handoffs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage_number int NOT NULL,
  from_seat text NOT NULL,
  to_seat text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('plan','change','evidence','check')),
  summary text NOT NULL,
  result text NOT NULL DEFAULT 'pass' CHECK (result IN ('pass','fail','info')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.cost_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage_number int NOT NULL,
  seat text NOT NULL,
  tokens bigint NOT NULL DEFAULT 0,
  minutes numeric NOT NULL DEFAULT 0,
  usd numeric NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.recovery_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage_number int NOT NULL,
  caught_by text NOT NULL,
  problem text NOT NULL,
  fix text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.stress_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempts int NOT NULL,
  confirmed int NOT NULL,
  conflicts int NOT NULL,
  replayed int NOT NULL,
  errors int NOT NULL,
  duration_ms int NOT NULL,
  passed boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['factory_stages','seats','handoffs','cost_entries','recovery_events','stress_runs'] LOOP
    EXECUTE format('GRANT SELECT ON public.%I TO anon, authenticated', t);
    EXECUTE format('GRANT INSERT, UPDATE, DELETE ON public.%I TO authenticated', t);
    EXECUTE format('GRANT ALL ON public.%I TO service_role', t);
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY "public read" ON public.%I FOR SELECT TO anon, authenticated USING (true)', t);
    EXECUTE format('CREATE POLICY "signed-in insert" ON public.%I FOR INSERT TO authenticated WITH CHECK (auth.uid() IS NOT NULL)', t);
    EXECUTE format('CREATE POLICY "signed-in update" ON public.%I FOR UPDATE TO authenticated USING (auth.uid() IS NOT NULL)', t);
    EXECUTE format('CREATE POLICY "signed-in delete" ON public.%I FOR DELETE TO authenticated USING (auth.uid() IS NOT NULL)', t);
  END LOOP;
END $$;

-- Seed data
INSERT INTO public.restaurants (slug, name, cuisine, city, timezone, opens_at, closes_at, slot_minutes, description) VALUES
('ember-oak','Ember & Oak','Wood-fire Nordic','Stockholm','Europe/Stockholm','17:00','23:00',90,'Open-flame cooking with seasonal Swedish produce.'),
('canal-no-9','Canal No. 9','Modern bistro','London','Europe/London','12:00','22:30',90,'Small plates by the water.'),
('hudson-lantern','Hudson Lantern','New American','New York','America/New_York','17:30','23:30',90,'Late-night counter with a raw bar.'),
('kiri-ya','Kiri-ya','Kaiseki','Tokyo','Asia/Tokyo','18:00','22:00',120,'Eight seats, one seasonal tasting menu.');

INSERT INTO public.dining_tables (restaurant_id, label, seats)
SELECT r.id, x.label, x.seats FROM public.restaurants r
CROSS JOIN (VALUES ('T1',2),('T2',2),('T3',4),('T4',4),('T5',6)) AS x(label, seats)
WHERE r.slug <> 'kiri-ya';
INSERT INTO public.dining_tables (restaurant_id, label, seats)
SELECT r.id, x.label, x.seats FROM public.restaurants r
CROSS JOIN (VALUES ('Counter A',4),('Counter B',4)) AS x(label, seats) WHERE r.slug = 'kiri-ya';

INSERT INTO public.factory_stages (number, title, status, summary) VALUES
(1,'Stage 1 — Plan & core invariant','done','Planner split the work; implementer shipped the booking path; verifier proved no overlap under parallel load.'),
(2,'Stage 2 — Retries & idempotency','done','Repeated requests return the original result instead of creating new records.'),
(3,'Stage 3 — Time zones & DST','in_progress','Local wall-clock slots converted to a universal clock; DST boundary cases under test.'),
(4,'Stage 4 — Clean-room clone & offline container','pending','Package the service to build and serve with no outbound network.');

INSERT INTO public.seats (name, role, model, mandate, sort) VALUES
('Foreman','Planner','','Break the brief into small, verifiable tasks. Define acceptance checks before work starts. Never write production code.',1),
('Builder','Implementer','','Take one task at a time. Make the smallest change that satisfies its checks. Attach evidence of what changed and why.',2),
('Inspector','Reviewer','','Read every change against its task. Reject work that lacks evidence or widens scope. Explain each rejection in one sentence.',3),
('Auditor','Verifier','','Run the checks independently of the builder. Try to break the result with adversarial inputs. Report pass or fail with reproducible proof.',4);

INSERT INTO public.handoffs (stage_number, from_seat, to_seat, kind, summary, result) VALUES
(1,'Foreman','Builder','plan','Task list with acceptance checks for the core invariant.','info'),
(1,'Builder','Inspector','change','Write path routed through a single transactional operation.','info'),
(1,'Inspector','Builder','check','Rejected: invariant enforced in application code only.','fail'),
(1,'Builder','Auditor','evidence','Invariant moved into storage constraints; logs attached.','info'),
(1,'Auditor','Foreman','check','Parallel load test: exactly one success per contested resource.','pass'),
(2,'Auditor','Foreman','check','Replayed requests return the original record.','pass');

INSERT INTO public.cost_entries (stage_number, seat, tokens, minutes, usd) VALUES
(1,'Foreman',42000,6,0.21),(1,'Builder',188000,24,1.12),(1,'Inspector',61000,8,0.34),(1,'Auditor',73000,11,0.41),
(2,'Builder',96000,13,0.58),(2,'Auditor',38000,6,0.22);

INSERT INTO public.recovery_events (stage_number, caught_by, problem, fix) VALUES
(1,'Inspector','Check-then-write race allowed overlapping records under load.','Moved the rule into a storage-level exclusion constraint and re-ran the load test.'),
(2,'Auditor','Client retries after a timeout created duplicate records.','Required a per-request key; repeats now return the first result.');
