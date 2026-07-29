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

-- identities: kalıcı thread kimliği ile dış kimlikler arasındaki eşleme.
-- Zoho lead -> contact dönüşümünde ID değişse de hat kopmaz; aynı telefon,
-- e-posta veya Zoho ID'si her zaman aynı thread'e çözülür.
CREATE TABLE IF NOT EXISTS identities (
    id             bigserial PRIMARY KEY,
    thread_id      text NOT NULL REFERENCES threads (thread_id),
    id_type        text NOT NULL,  -- zoho_lead | zoho_contact | phone | email
    id_value       text NOT NULL,  -- normalize edilmiş hali
    first_seen_at  timestamptz DEFAULT now(),
    last_seen_at   timestamptz DEFAULT now(),
    UNIQUE (id_type, id_value)
);

-- thread_merges: iki hattın birleştirilme kaydı (denetim izi).
CREATE TABLE IF NOT EXISTS thread_merges (
    id                bigserial PRIMARY KEY,
    winner_thread_id  text NOT NULL,
    loser_thread_id   text NOT NULL,
    reason            text,  -- hangi kimlik eşleşmesi tetikledi
    merged_at         timestamptz DEFAULT now()
);

-- blocked_identifiers: kimlik çözümlemede tamamen yok sayılacak
-- tanımlayıcılar (ör. santral numarası, ortak ofis e-postası).
CREATE TABLE IF NOT EXISTS blocked_identifiers (
    id_type   text NOT NULL,
    id_value  text NOT NULL,
    note      text,
    PRIMARY KEY (id_type, id_value)
);

CREATE INDEX IF NOT EXISTS idx_identities_thread ON identities (thread_id);
