-- Pusula veritabanı şeması.
-- Tüm timestamp'ler timestamptz; uygulama katmanı Europe/Istanbul kullanır.

-- events: tüm kanallardan gelen ham olay kayıtları.
-- (channel, source_ref) tekilliği ile aynı kaynak kaydın iki kez
-- yazılması engellenir (idempotent ingest).
CREATE TABLE IF NOT EXISTS events (
    id            bigserial PRIMARY KEY,
    thread_id     text,
    channel       text CHECK (channel IN ('call', 'email', 'whatsapp', 'meeting', 'note', 'task')),
    direction     text CHECK (direction IN ('inbound', 'outbound', 'internal')),
    rep_id        text,
    occurred_at   timestamptz,
    source_ref    text,
    body          text,
    body_quality  text CHECK (body_quality IN ('low', 'medium', 'high')),
    meta          jsonb,
    created_at    timestamptz DEFAULT now(),
    UNIQUE (channel, source_ref)
);

-- threads: müşteri/aday bazında konuşma zinciri özeti ve durumu.
CREATE TABLE IF NOT EXISTS threads (
    thread_id               text PRIMARY KEY,
    segment                 text,
    owner_rep_id            text,
    first_touch_at          timestamptz,
    last_touch_at           timestamptz,
    touch_count_by_channel  jsonb,
    state                   jsonb,
    created_at              timestamptz DEFAULT now()
);

-- commitments: konuşmalardan çıkarılan taahhütler ve akıbetleri.
-- quote: taahhüdün kaynaktaki kanıt alıntısı.
CREATE TABLE IF NOT EXISTS commitments (
    id                  bigserial PRIMARY KEY,
    thread_id           text,
    source_event_id     bigint REFERENCES events (id),
    text                text,
    quote               text,
    due_at              timestamptz,
    fulfilled_event_id  bigint REFERENCES events (id),
    status              text CHECK (status IN ('open', 'fulfilled', 'broken', 'expired')),
    created_at          timestamptz DEFAULT now()
);

-- sync_state: kaynak bazında senkronizasyon imleci.
CREATE TABLE IF NOT EXISTS sync_state (
    source_name     text PRIMARY KEY,
    last_synced_at  timestamptz,
    last_cursor     text,
    updated_at      timestamptz
);

CREATE INDEX IF NOT EXISTS idx_events_thread_occurred ON events (thread_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_rep_occurred ON events (rep_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_commitments_status_due ON commitments (status, due_at);
