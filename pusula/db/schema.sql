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

-- blocked_domains: e-posta kimliklerinde tamamen yok sayılacak
-- domainler (ör. şirket içi adresler). Sadece e-posta için geçerlidir;
-- telefonda domain kontrolü yoktur.
CREATE TABLE IF NOT EXISTS blocked_domains (
    domain      text PRIMARY KEY,  -- küçük harf, @ olmadan: "rexven.com"
    note        text,
    created_at  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_identities_thread ON identities (thread_id);

-- reps: satış temsilcileri (Zoho kullanıcıları).
-- category TÜRETİLMİŞ bir alandır; her sync'te şöyle hesaplanır:
--   coalesce(category_override, role_category_map[zoho_role], 'other')
-- Kural tabanlı eşleme role_category_map'te yaşar; kişi bazında
-- istisna gerektiğinde category_override doldurulur ve haritayı ezer.
-- category_override ve active elle yönetilir, sync asla dokunmaz.
-- zoho_profile sadece referans amaçlıdır, filtre olarak kullanılmaz.
CREATE TABLE IF NOT EXISTS reps (
    rep_id             text PRIMARY KEY,  -- Zoho user id
    full_name          text NOT NULL,
    email              text,
    zoho_role          text,
    zoho_profile       text,
    category           text NOT NULL DEFAULT 'other'
        CHECK (category IN ('sales', 'consultancy', 'management', 'other')),
    category_override  text  -- doluysa haritayı ezer
        CHECK (category_override IN ('sales', 'consultancy', 'management', 'other')),
    active             boolean NOT NULL DEFAULT true,
    created_at         timestamptz DEFAULT now(),
    updated_at         timestamptz DEFAULT now()
);

-- Şema, category_override'dan önce kurulmuş veritabanlarında da
-- idempotent uygulanabilsin diye kolon ayrıca ALTER ile eklenir.
ALTER TABLE reps ADD COLUMN IF NOT EXISTS category_override text
    CHECK (category_override IN ('sales', 'consultancy', 'management', 'other'));

-- role_category_map: Zoho rol adı -> kategori eşlemesi.
-- reps.category her sync'te bu haritadan yeniden hesaplanır;
-- haritada olmayan roller 'other' sayılır ve sync uyarı basar.
CREATE TABLE IF NOT EXISTS role_category_map (
    zoho_role   text PRIMARY KEY,
    category    text NOT NULL
        CHECK (category IN ('sales', 'consultancy', 'management', 'other')),
    note        text,
    created_at  timestamptz DEFAULT now()
);

-- 'core_telesales' -> 'sales' geçişi: değer kümesinin eski haliyle
-- kurulmuş veritabanlarında kısıtlar tazelenir ve veri taşınır.
-- Yeni kurulumda ve tekrarlanan uygulamada etkisiz (idempotent).
ALTER TABLE reps DROP CONSTRAINT IF EXISTS reps_category_check;
ALTER TABLE reps DROP CONSTRAINT IF EXISTS reps_category_override_check;
ALTER TABLE role_category_map DROP CONSTRAINT IF EXISTS role_category_map_category_check;
UPDATE reps SET category = 'sales' WHERE category = 'core_telesales';
UPDATE reps SET category_override = 'sales' WHERE category_override = 'core_telesales';
UPDATE role_category_map SET category = 'sales' WHERE category = 'core_telesales';
ALTER TABLE reps ADD CONSTRAINT reps_category_check
    CHECK (category IN ('sales', 'consultancy', 'management', 'other'));
ALTER TABLE reps ADD CONSTRAINT reps_category_override_check
    CHECK (category_override IN ('sales', 'consultancy', 'management', 'other'));
ALTER TABLE role_category_map ADD CONSTRAINT role_category_map_category_check
    CHECK (category IN ('sales', 'consultancy', 'management', 'other'));
