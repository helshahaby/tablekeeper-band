-- Tablekeeper schema. Portable PostgreSQL (>= 14). No hosted-platform roles or auth schema.
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE CHECK (email = lower(email)),
  password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  token_hash text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

CREATE TABLE user_roles (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('admin','staff','user')),
  PRIMARY KEY (user_id, role)
);

CREATE TABLE restaurants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  name text NOT NULL,
  cuisine text NOT NULL,
  city text NOT NULL,
  timezone text NOT NULL,
  opens_at time NOT NULL,
  closes_at time NOT NULL,
  slot_minutes int NOT NULL DEFAULT 90,
  description text NOT NULL DEFAULT ''
);

CREATE TABLE dining_tables (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  label text NOT NULL,
  seats int NOT NULL CHECK (seats > 0),
  UNIQUE (restaurant_id, label)
);

CREATE TABLE bookings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  table_id uuid NOT NULL REFERENCES dining_tables(id) ON DELETE CASCADE,
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  guest_name text NOT NULL,
  party_size int NOT NULL CHECK (party_size > 0),
  slot tstzrange NOT NULL,
  status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','cancelled')),
  idempotency_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, idempotency_key),
  CONSTRAINT no_double_booking EXCLUDE USING gist (table_id WITH =, slot WITH &&) WHERE (status = 'confirmed')
);
CREATE INDEX bookings_user_idx ON bookings(user_id);

-- Slots generated in restaurant-local wall time and converted to UTC (DST-safe).
CREATE FUNCTION available_slots(_restaurant_id uuid, _date date, _party int)
RETURNS TABLE (starts_at timestamptz, free_tables int)
LANGUAGE sql STABLE AS $$
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

-- The single write path. Storage constraints are the source of truth.
CREATE FUNCTION book_table(_user_id uuid, _restaurant_id uuid, _starts_at timestamptz, _party int,
  _guest_name text, _idempotency_key text, _table_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  r restaurants%ROWTYPE;
  existing bookings%ROWTYPE;
  local_t time;
  rng tstzrange;
  cand uuid;
  new_id uuid;
BEGIN
  IF _user_id IS NULL THEN RAISE EXCEPTION 'not signed in'; END IF;
  IF _idempotency_key IS NULL OR length(_idempotency_key) < 8 THEN RAISE EXCEPTION 'invalid request key'; END IF;

  SELECT * INTO existing FROM bookings WHERE user_id = _user_id AND idempotency_key = _idempotency_key;
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
      VALUES (cand, r.id, _user_id, left(coalesce(nullif(trim(_guest_name),''),'Guest'),80), _party, rng, _idempotency_key)
      RETURNING id INTO new_id;
      RETURN jsonb_build_object('status','confirmed','booking_id',new_id,'table_id',cand);
    EXCEPTION
      WHEN exclusion_violation THEN CONTINUE;
      WHEN unique_violation THEN
        SELECT * INTO existing FROM bookings WHERE user_id = _user_id AND idempotency_key = _idempotency_key;
        RETURN jsonb_build_object('status','replayed','booking_id',existing.id,'table_id',existing.table_id);
    END;
  END LOOP;
  RETURN jsonb_build_object('status','conflict','reason','no table free at that time');
END $$;

CREATE FUNCTION cancel_booking(_booking_id uuid, _user_id uuid, _is_staff boolean)
RETURNS boolean LANGUAGE plpgsql AS $$
BEGIN
  UPDATE bookings SET status = 'cancelled'
  WHERE id = _booking_id AND status = 'confirmed' AND (user_id = _user_id OR _is_staff);
  RETURN FOUND;
END $$;

-- Factory evidence tables
CREATE TABLE factory_stages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  number int UNIQUE NOT NULL,
  title text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','done')),
  summary text NOT NULL DEFAULT ''
);
CREATE TABLE seats (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  role text NOT NULL,
  model text NOT NULL DEFAULT '',
  mandate text NOT NULL,
  sort int NOT NULL DEFAULT 0
);
CREATE TABLE handoffs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage_number int NOT NULL,
  from_seat text NOT NULL,
  to_seat text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('plan','change','evidence','check')),
  summary text NOT NULL,
  result text NOT NULL DEFAULT 'info' CHECK (result IN ('pass','fail','info')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cost_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage_number int NOT NULL,
  seat text NOT NULL,
  tokens bigint NOT NULL DEFAULT 0,
  minutes numeric NOT NULL DEFAULT 0,
  usd numeric NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE recovery_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage_number int NOT NULL,
  caught_by text NOT NULL,
  problem text NOT NULL,
  fix text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE stress_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempts int NOT NULL,
  confirmed int NOT NULL,
  conflicts int NOT NULL,
  replayed int NOT NULL,
  errors int NOT NULL,
  duration_ms int NOT NULL,
  passed boolean NOT NULL,
  engine text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
