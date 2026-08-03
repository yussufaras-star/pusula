-- Pusula veritabanı şeması.
-- Tüm timestamp'ler timestamptz; uygulama katmanı org'un timezone'unu
-- (bugün Europe/Istanbul) kullanır.
--
-- org_id: çok kiracılılık BUGÜN kurulmuyor; tüm veri tablolarında
-- org_id taşınması, ileride başka satış operasyonlarının aynı şemayla
-- çalışabilmesi için. Varsayılan 'rexven'; uygulama katmanı değeri
-- pusula/config.py'den (ORG_ID) okur.
--
-- Dosya hem sıfırdan hem mevcut veritabanına uygulanabilir: CREATE'ler
-- son hâli tanımlar, sondaki geçiş blokları eski kurulumları aynı hâle
-- getirir (idempotent).

-- orgs: Pusula'yı kullanan organizasyonlar.
CREATE TABLE IF NOT EXISTS orgs (
    org_id      text PRIMARY KEY,
    name        text NOT NULL,
    timezone    text NOT NULL DEFAULT 'Europe/Istanbul',
    created_at  timestamptz DEFAULT now()
);

-- segments: org'un huni segmentleri (ör. hot_core / Sıcak Çekirdek).
-- Tanımlar koda değil veriye yazılır; seed dosyasıyla yüklenir.
CREATE TABLE IF NOT EXISTS segments (
    org_id      text NOT NULL,
    key         text NOT NULL,  -- 'hot_core'
    label       text NOT NULL,  -- 'Sıcak Çekirdek'
    sort_order  int NOT NULL,
    PRIMARY KEY (org_id, key)
);

-- bug_codes: davranışsal hata kodu kataloğu (ROADMAP'teki 0x kodları).
-- description LLM'e verilecek tanımdır; kodlar koda gömülmez.
CREATE TABLE IF NOT EXISTS bug_codes (
    org_id       text NOT NULL,
    code         text NOT NULL,  -- '0x0001'
    scope        text NOT NULL CHECK (scope IN ('intra_call', 'cross_channel')),
    title        text NOT NULL,  -- kısa ad
    description  text NOT NULL,  -- LLM'e verilecek tanım
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, code)
);

-- events: tüm kanallardan gelen ham olay kayıtları.
-- (org_id, channel, source_ref) tekilliği ile aynı kaynak kaydın iki
-- kez yazılması engellenir (idempotent ingest).
CREATE TABLE IF NOT EXISTS events (
    id            bigserial PRIMARY KEY,
    org_id        text NOT NULL DEFAULT 'rexven',
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
    UNIQUE (org_id, channel, source_ref)
);

-- threads: müşteri/aday bazında konuşma zinciri özeti ve durumu.
-- segment, org'un segments tablosundaki bir key'e referans verir.
CREATE TABLE IF NOT EXISTS threads (
    org_id                  text NOT NULL DEFAULT 'rexven',
    thread_id               text NOT NULL,
    segment                 text,
    owner_rep_id            text,
    first_touch_at          timestamptz,
    last_touch_at           timestamptz,
    touch_count_by_channel  jsonb,
    state                   jsonb,
    created_at              timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, thread_id),
    FOREIGN KEY (org_id, segment) REFERENCES segments (org_id, key)
);

-- commitments: konuşmalardan çıkarılan taahhütler ve akıbetleri.
-- quote: taahhüdün kaynaktaki kanıt alıntısı.
CREATE TABLE IF NOT EXISTS commitments (
    id                  bigserial PRIMARY KEY,
    org_id              text NOT NULL DEFAULT 'rexven',
    thread_id           text,
    source_event_id     bigint REFERENCES events (id),
    text                text,
    quote               text,
    due_at              timestamptz,
    fulfilled_event_id  bigint REFERENCES events (id),
    status              text CHECK (status IN ('open', 'fulfilled', 'broken', 'expired')),
    created_at          timestamptz DEFAULT now()
);

-- sync_state: org + kaynak bazında senkronizasyon imleci.
CREATE TABLE IF NOT EXISTS sync_state (
    org_id          text NOT NULL DEFAULT 'rexven',
    source_name     text NOT NULL,
    last_synced_at  timestamptz,
    last_cursor     text,
    updated_at      timestamptz,
    PRIMARY KEY (org_id, source_name)
);

CREATE INDEX IF NOT EXISTS idx_events_thread_occurred ON events (thread_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_rep_occurred ON events (rep_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_commitments_status_due ON commitments (status, due_at);

-- identities: kalıcı thread kimliği ile dış kimlikler arasındaki eşleme.
-- Zoho lead -> contact dönüşümünde ID değişse de hat kopmaz; aynı telefon,
-- e-posta veya Zoho ID'si her zaman aynı org içindeki aynı thread'e çözülür.
CREATE TABLE IF NOT EXISTS identities (
    id             bigserial PRIMARY KEY,
    org_id         text NOT NULL DEFAULT 'rexven',
    thread_id      text NOT NULL,
    id_type        text NOT NULL,  -- zoho_lead | zoho_contact | phone | email
    id_value       text NOT NULL,  -- normalize edilmiş hali
    first_seen_at  timestamptz DEFAULT now(),
    last_seen_at   timestamptz DEFAULT now(),
    UNIQUE (org_id, id_type, id_value),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);

-- thread_merges: iki hattın birleştirilme kaydı (denetim izi).
CREATE TABLE IF NOT EXISTS thread_merges (
    id                bigserial PRIMARY KEY,
    org_id            text NOT NULL DEFAULT 'rexven',
    winner_thread_id  text NOT NULL,
    loser_thread_id   text NOT NULL,
    reason            text,  -- hangi kimlik eşleşmesi tetikledi
    merged_at         timestamptz DEFAULT now()
);

-- blocked_identifiers: kimlik çözümlemede tamamen yok sayılacak
-- tanımlayıcılar (ör. santral numarası, ortak ofis e-postası).
CREATE TABLE IF NOT EXISTS blocked_identifiers (
    org_id    text NOT NULL DEFAULT 'rexven',
    id_type   text NOT NULL,
    id_value  text NOT NULL,
    note      text,
    PRIMARY KEY (org_id, id_type, id_value)
);

-- blocked_domains: e-posta kimliklerinde tamamen yok sayılacak
-- domainler (ör. şirket içi adresler). Sadece e-posta için geçerlidir;
-- telefonda domain kontrolü yoktur.
CREATE TABLE IF NOT EXISTS blocked_domains (
    org_id      text NOT NULL DEFAULT 'rexven',
    domain      text NOT NULL,  -- küçük harf, @ olmadan: "rexven.com"
    note        text,
    created_at  timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, domain)
);

CREATE INDEX IF NOT EXISTS idx_identities_thread ON identities (thread_id);

-- leads: Zoho Leads özeti (48s / 3 arama penceresi için).
-- assigned_at = Created_Time; Zoho metadata'da ayrı Owner atama alanı yok.
-- status = Zoho Lead_Status (salt okunur; Pusula yazmaz).
-- pusula_state = yerel durum otomasyonu (Zoho'ya yazılmaz).
CREATE TABLE IF NOT EXISTS leads (
    org_id            text NOT NULL DEFAULT 'rexven',
    lead_id           text NOT NULL,
    thread_id         text,
    status            text,
    owner_rep_id      text,
    assigned_at       timestamptz,
    source            text,
    full_name         text,  -- Zoho Full_Name; identities'e yazılmaz
    pusula_state      text
        CHECK (pusula_state IS NULL OR pusula_state IN (
            'active', 'stale', 'aging', 'archived', 'nurture', 'closed'
        )),
    pusula_state_at   timestamptz,
    created_at        timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, lead_id),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_leads_thread ON leads (org_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_leads_assigned_at ON leads (org_id, assigned_at);
CREATE INDEX IF NOT EXISTS idx_leads_pusula_state
    ON leads (org_id, pusula_state, owner_rep_id);

-- contacts: Zoho Contacts (Deal → Contact → Lead zinciri için).
-- lead_id Zoho'da doğrudan yok; thread üzerindeki zoho_lead kimliğinden çözülür.
CREATE TABLE IF NOT EXISTS contacts (
    org_id         text NOT NULL DEFAULT 'rexven',
    contact_id     text NOT NULL,
    lead_id        text,
    thread_id      text,
    created_at     timestamptz,
    owner_rep_id   text,
    full_name      text,  -- Zoho Full_Name; identities'e yazılmaz
    PRIMARY KEY (org_id, contact_id),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_contacts_thread ON contacts (org_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_contacts_lead ON contacts (org_id, lead_id);

-- deals: Zoho Deals (satış). cycle_start_at = bağlı lead'in Zoho Created_Time
-- (leads.assigned_at). Nisan 2026 toplu taşıma lead'lerinde
-- cycle_start_reliable = false.
CREATE TABLE IF NOT EXISTS deals (
    org_id                 text NOT NULL DEFAULT 'rexven',
    deal_id                text NOT NULL,
    contact_id             text,
    lead_id                text,
    thread_id              text,
    stage                  text,
    amount                 numeric,
    created_at             timestamptz,
    closed_at              timestamptz,
    owner_rep_id           text,
    source                 text,
    cycle_start_at         timestamptz,
    cycle_start_reliable   boolean,
    PRIMARY KEY (org_id, deal_id),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_deals_owner_created
    ON deals (org_id, owner_rep_id, created_at);
CREATE INDEX IF NOT EXISTS idx_deals_thread ON deals (org_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_deals_contact ON deals (org_id, contact_id);

-- reps: satış temsilcileri (Zoho kullanıcıları).
-- category TÜRETİLMİŞ bir alandır; her sync'te şöyle hesaplanır:
--   coalesce(category_override, role_category_map[zoho_role], 'other')
-- Kural tabanlı eşleme role_category_map'te yaşar; kişi bazında
-- istisna gerektiğinde category_override doldurulur ve haritayı ezer.
-- category_override ve active elle yönetilir, sync asla dokunmaz.
-- zoho_profile sadece referans amaçlıdır, filtre olarak kullanılmaz.
CREATE TABLE IF NOT EXISTS reps (
    org_id             text NOT NULL DEFAULT 'rexven',
    rep_id             text NOT NULL,  -- Zoho user id
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
    updated_at         timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, rep_id)
);

-- Şema, category_override'dan önce kurulmuş veritabanlarında da
-- idempotent uygulanabilsin diye kolon ayrıca ALTER ile eklenir.
ALTER TABLE reps ADD COLUMN IF NOT EXISTS category_override text
    CHECK (category_override IN ('sales', 'consultancy', 'management', 'other'));

-- role_category_map: Zoho rol adı -> kategori eşlemesi.
-- reps.category her sync'te bu haritadan yeniden hesaplanır;
-- haritada olmayan roller 'other' sayılır ve sync uyarı basar.
CREATE TABLE IF NOT EXISTS role_category_map (
    org_id      text NOT NULL DEFAULT 'rexven',
    zoho_role   text NOT NULL,
    category    text NOT NULL
        CHECK (category IN ('sales', 'consultancy', 'management', 'other')),
    note        text,
    created_at  timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, zoho_role)
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

-- org_id geçişi: org_id'siz kurulmuş veritabanlarında kolonlar eklenir
-- (mevcut satırlar 'rexven' olur) ve PK/UNIQUE/FK kısıtları org_id'yi
-- içerecek şekilde yeniden kurulur. Sıfırdan kurulumda CREATE'ler zaten
-- son hâli verdiği için blok aynı kısıtları yeniden kurar; veri kaybı
-- veya davranış değişikliği yoktur (idempotent).
ALTER TABLE events              ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE threads             ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE commitments         ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE sync_state          ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE identities          ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE thread_merges       ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE blocked_identifiers ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE blocked_domains     ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE reps                ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE role_category_map   ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE leads               ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'rexven';
ALTER TABLE leads               ADD COLUMN IF NOT EXISTS full_name text;
ALTER TABLE leads               ADD COLUMN IF NOT EXISTS pusula_state text;
ALTER TABLE leads               ADD COLUMN IF NOT EXISTS pusula_state_at timestamptz;
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_pusula_state_check;
ALTER TABLE leads ADD CONSTRAINT leads_pusula_state_check
    CHECK (pusula_state IS NULL OR pusula_state IN (
        'active', 'stale', 'aging', 'archived', 'nurture', 'closed'
    ));
CREATE INDEX IF NOT EXISTS idx_leads_pusula_state
    ON leads (org_id, pusula_state, owner_rep_id);

-- contacts / deals: mevcut DB'lere idempotent ekleme.
CREATE TABLE IF NOT EXISTS contacts (
    org_id         text NOT NULL DEFAULT 'rexven',
    contact_id     text NOT NULL,
    lead_id        text,
    thread_id      text,
    created_at     timestamptz,
    owner_rep_id   text,
    full_name      text,
    PRIMARY KEY (org_id, contact_id),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS full_name text;
CREATE INDEX IF NOT EXISTS idx_contacts_thread ON contacts (org_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_contacts_lead ON contacts (org_id, lead_id);

CREATE TABLE IF NOT EXISTS deals (
    org_id                 text NOT NULL DEFAULT 'rexven',
    deal_id                text NOT NULL,
    contact_id             text,
    lead_id                text,
    thread_id              text,
    stage                  text,
    amount                 numeric,
    created_at             timestamptz,
    closed_at              timestamptz,
    owner_rep_id           text,
    source                 text,
    cycle_start_at         timestamptz,
    cycle_start_reliable   boolean,
    PRIMARY KEY (org_id, deal_id),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_deals_owner_created
    ON deals (org_id, owner_rep_id, created_at);
CREATE INDEX IF NOT EXISTS idx_deals_thread ON deals (org_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_deals_contact ON deals (org_id, contact_id);

-- threads PK'sı değişeceği için ona bağımlı FK önce bırakılır.
ALTER TABLE identities DROP CONSTRAINT IF EXISTS identities_thread_id_fkey;
ALTER TABLE identities DROP CONSTRAINT IF EXISTS identities_org_id_thread_id_fkey;
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_org_id_thread_id_fkey;
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_thread_id_fkey;

-- threads: PK (org_id, thread_id) + segment FK'sı.
ALTER TABLE threads DROP CONSTRAINT IF EXISTS threads_org_id_segment_fkey;
ALTER TABLE threads DROP CONSTRAINT IF EXISTS threads_pkey;
ALTER TABLE threads ADD CONSTRAINT threads_pkey PRIMARY KEY (org_id, thread_id);
ALTER TABLE threads ADD CONSTRAINT threads_org_id_segment_fkey
    FOREIGN KEY (org_id, segment) REFERENCES segments (org_id, key);

-- identities: tekillik ve threads FK'sı org bazlı.
ALTER TABLE identities DROP CONSTRAINT IF EXISTS identities_id_type_id_value_key;
ALTER TABLE identities DROP CONSTRAINT IF EXISTS identities_org_id_id_type_id_value_key;
ALTER TABLE identities ADD CONSTRAINT identities_org_id_id_type_id_value_key
    UNIQUE (org_id, id_type, id_value);
ALTER TABLE identities ADD CONSTRAINT identities_org_id_thread_id_fkey
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id);

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_pkey;
ALTER TABLE leads ADD CONSTRAINT leads_pkey PRIMARY KEY (org_id, lead_id);
ALTER TABLE leads ADD CONSTRAINT leads_org_id_thread_id_fkey
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id);

-- events: ingest tekilliği org bazlı.
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_channel_source_ref_key;
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_org_id_channel_source_ref_key;
ALTER TABLE events ADD CONSTRAINT events_org_id_channel_source_ref_key
    UNIQUE (org_id, channel, source_ref);

-- Kalan PK genişletmeleri (bunlara referans veren FK yok).
ALTER TABLE sync_state DROP CONSTRAINT IF EXISTS sync_state_pkey;
ALTER TABLE sync_state ADD CONSTRAINT sync_state_pkey
    PRIMARY KEY (org_id, source_name);
ALTER TABLE blocked_identifiers DROP CONSTRAINT IF EXISTS blocked_identifiers_pkey;
ALTER TABLE blocked_identifiers ADD CONSTRAINT blocked_identifiers_pkey
    PRIMARY KEY (org_id, id_type, id_value);
ALTER TABLE blocked_domains DROP CONSTRAINT IF EXISTS blocked_domains_pkey;
ALTER TABLE blocked_domains ADD CONSTRAINT blocked_domains_pkey
    PRIMARY KEY (org_id, domain);
ALTER TABLE reps DROP CONSTRAINT IF EXISTS reps_pkey;
ALTER TABLE reps ADD CONSTRAINT reps_pkey
    PRIMARY KEY (org_id, rep_id);
ALTER TABLE role_category_map DROP CONSTRAINT IF EXISTS role_category_map_pkey;
ALTER TABLE role_category_map ADD CONSTRAINT role_category_map_pkey
    PRIMARY KEY (org_id, zoho_role);

-- zoho_schema_snapshot: CRM alan metadata anlık görüntüsü.
-- scripts/zoho_schema_check.py ile karşılaştırılır; picklist kayması
-- (yeniden adlandırma vb.) sessizce bozulmasın diye drift raporu üretir.
CREATE TABLE IF NOT EXISTS zoho_schema_snapshot (
    org_id           text NOT NULL DEFAULT 'rexven',
    module           text NOT NULL,
    api_name         text NOT NULL,
    field_label      text,
    data_type        text,
    is_custom        boolean,
    picklist_values  jsonb,  -- picklist ise [{display_value, actual_value}, ...]
    captured_at      timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, module, api_name)
);

-- call_outcomes: Zoho Calls sonuç picklist → kanonik outcome_key.
-- Ham değerler koda gömülmez; seed_rexven.sql ile doldurulur.
-- crm_calls ingester başında tek sorguda cache'ler.
CREATE TABLE IF NOT EXISTS call_outcomes (
    org_id        text NOT NULL DEFAULT 'rexven',
    raw_value     text NOT NULL,  -- Zoho'daki ham picklist değeri
    outcome_key   text NOT NULL,  -- kanonik anahtar (no_answer, demo_done, ...)
    category      text NOT NULL
        CHECK (category IN ('reached', 'not_reached', 'positive', 'negative', 'pending')),
    is_progress   boolean NOT NULL DEFAULT false,
    sort_order    int,
    PRIMARY KEY (org_id, raw_value)
);

-- call_statuses: Outgoing_Call_Status teknik durum (satış sonucu değil).
-- Örn. 'Tamamlandı' = connected. call_outcomes ile karıştırılmaz.
CREATE TABLE IF NOT EXISTS call_statuses (
    org_id      text NOT NULL DEFAULT 'rexven',
    raw_value   text NOT NULL,
    status_key  text NOT NULL,  -- connected | no_answer | failed | unknown
    PRIMARY KEY (org_id, raw_value)
);

