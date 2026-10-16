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
    status_changed_at timestamptz,
    status_changed_source text
        CHECK (status_changed_source IS NULL OR status_changed_source IN (
            'gercek', 'tahmini'
        )),
    created_at        timestamptz DEFAULT now(),
    PRIMARY KEY (org_id, lead_id),
    FOREIGN KEY (org_id, thread_id) REFERENCES threads (org_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_leads_thread ON leads (org_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_leads_assigned_at ON leads (org_id, assigned_at);
CREATE INDEX IF NOT EXISTS idx_leads_pusula_state
    ON leads (org_id, pusula_state, owner_rep_id);
CREATE INDEX IF NOT EXISTS idx_leads_karar_bekleyen
    ON leads (org_id, owner_rep_id, status_changed_at)
    WHERE status = 'Düşünmek İstiyor';

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
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status_changed_at timestamptz;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status_changed_source text;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS zoho_removed_at timestamptz;  -- Zoho'da silinmiş/dönüştürülmüş (lead_sync)
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_changed_source_check;
ALTER TABLE leads ADD CONSTRAINT leads_status_changed_source_check
    CHECK (status_changed_source IS NULL OR status_changed_source IN (
        'gercek', 'tahmini'
    ));
CREATE INDEX IF NOT EXISTS idx_leads_karar_bekleyen
    ON leads (org_id, owner_rep_id, status_changed_at)
    WHERE status = 'Düşünmek İstiyor';

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

-- rep_snapshots: temsilci günlük metrikleri (haftalık karşılaştırma).
-- scripts/take_snapshot.py yazar; (snapshot_date, rep_id) upsert.
CREATE TABLE IF NOT EXISTS rep_snapshots (
    snapshot_date   date NOT NULL,
    rep_id          text NOT NULL,
    bekleyen_lead   integer NOT NULL DEFAULT 0,
    kayip_randevu   integer NOT NULL DEFAULT 0,
    acik_taahhut    integer NOT NULL DEFAULT 0,
    tutulan_hafta   integer NOT NULL DEFAULT 0,
    bozulan_hafta   integer NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_date, rep_id)
);

-- nudges.nudge_type bilinen değerler (serbest text; yeni tip eklenince güncelle):
-- pencere_aciliyor | kayip_randevu | gecikmis_taahhut | planlanmis_arama | karar_bekleyen | blok_ozeti

-- Hourly KPI messages are idempotent by rep/team and Istanbul work-hour slot.
CREATE TABLE IF NOT EXISTS kpi_nudge_sends (
    org_id       text NOT NULL DEFAULT 'rexven',
    rep_id       text NOT NULL,
    nudge_date   date NOT NULL,
    nudge_hour   smallint NOT NULL CHECK (nudge_hour BETWEEN 0 AND 23),
    scope        text NOT NULL CHECK (scope IN ('person', 'team')),
    status       text NOT NULL CHECK (status IN ('sending', 'sent', 'failed')),
    sent_at      timestamptz,
    failure      text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, rep_id, nudge_date, nudge_hour, scope)
);

-- Temsilcinin CRM işlemleri (not, görev, lead/deal/contact oluşturma ve son
-- değişiklik). Kaynak: pusula/ingest/crm_actions.py; canlı ingest de yoksa
-- aynı tanımla oluşturur.
CREATE TABLE IF NOT EXISTS crm_actions (
    org_id        text NOT NULL DEFAULT 'rexven',
    module        text NOT NULL,
    record_id     text NOT NULL,
    action        text NOT NULL CHECK (action IN ('created', 'modified')),
    acted_at      timestamptz NOT NULL,
    actor_rep_id  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, module, record_id, action, acted_at)
);
CREATE INDEX IF NOT EXISTS idx_crm_actions_actor_time
    ON crm_actions (org_id, actor_rep_id, acted_at);

-- ===========================================================================
-- Finans modülü (fin_*): Rexven LLC (USD), GmbH (EUR), TR (TRY).
-- Yalnız yönetici okur/yazar; erişim API'de (pusula/web/auth.finance_user)
-- zorlanır. Tutarlar kendi para biriminde numeric(18,2), işaretli
-- (+ giriş, − çıkış). USD çevirisi fin_fx_rates ile yapılır.
-- Banka kimlik bilgileri hiçbir tabloda tutulmaz: fin_accounts.credential_key
-- yalnız ortam değişkeni adının son ekidir (MERCURY_TOKEN_<KEY> vb.).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS fin_companies (
    id              text PRIMARY KEY,  -- 'llc' | 'gmbh' | 'tr'
    name            text NOT NULL,
    country         text NOT NULL,
    base_currency   text NOT NULL CHECK (base_currency ~ '^[A-Z]{3}$'),
    -- Açıklama veya karşı tarafta geçince şirketler arası sayılan kalıplar (ILIKE).
    match_patterns  text[] NOT NULL DEFAULT '{}',
    sort            int NOT NULL DEFAULT 0
);

INSERT INTO fin_companies (id, name, country, base_currency, match_patterns, sort) VALUES
    ('llc',  'Rexven LLC',  'US', 'USD', ARRAY['%rexven llc%'], 1),
    ('gmbh', 'Rexven GmbH', 'DE', 'EUR', ARRAY['%rexven gmbh%'], 2),
    ('tr',   'Rexven TR',   'TR', 'TRY', ARRAY['%rexven%teknoloji%', '%rexven%a.ş%', '%rexven%ltd%'], 3)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS fin_accounts (
    id                   bigserial PRIMARY KEY,
    company_id           text NOT NULL REFERENCES fin_companies (id),
    name                 text NOT NULL,
    institution          text NOT NULL,
    type                 text NOT NULL DEFAULT 'bank'
        CHECK (type IN ('bank', 'fintech', 'payment', 'card', 'shipping', 'other')),
    currency             text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    -- 'csv' ekstre yüklemesi, 'manual' elle bakiye; diğerleri API senkronu.
    provider             text NOT NULL DEFAULT 'manual'
        CHECK (provider IN ('mercury', 'wise', 'stripe', 'revolut', 'kuveytturk', 'garanti', 'csv', 'manual')),
    -- Sağlayıcıdaki hesap kimliği. Wise: '<profileId>:<balanceId>'.
    provider_account_id  text,
    -- Ortam değişkeni son eki (ör. LLC → MERCURY_TOKEN_LLC). Değer değil, ad.
    credential_key       text CHECK (credential_key ~ '^[A-Z0-9_]+$'),
    current_balance      numeric(18, 2) NOT NULL DEFAULT 0,
    balance_at           timestamptz,
    last_synced_at       timestamptz,
    sync_error           text,  -- son senkron hatası; başarılı senkron temizler
    sync_warning         text,  -- ör. Wise SCA: yalnız bakiye güncellendi
    active               boolean NOT NULL DEFAULT true,
    sort                 int NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (provider IN ('csv', 'manual') OR credential_key IS NOT NULL)
);

-- Geçiş: yeni API sağlayıcıları (Kuveyt Türk, Garanti) için kısıt güncellenir.
ALTER TABLE fin_accounts DROP CONSTRAINT IF EXISTS fin_accounts_provider_check;
ALTER TABLE fin_accounts ADD CONSTRAINT fin_accounts_provider_check
    CHECK (provider IN ('mercury', 'wise', 'stripe', 'revolut', 'kuveytturk', 'garanti', 'csv', 'manual'));

CREATE TABLE IF NOT EXISTS fin_categories (
    id    text PRIMARY KEY,
    name  text NOT NULL,
    kind  text NOT NULL CHECK (kind IN ('in', 'out', 'transfer')),
    sort  int NOT NULL DEFAULT 0
);

INSERT INTO fin_categories (id, name, kind, sort) VALUES
    ('satis',       'Satış / Platform geliri',  'in',        1),
    ('proje',       'Proje geliri',             'in',        2),
    ('diger_gir',   'Diğer giriş',              'in',        3),
    ('tedarikci',   'Tedarikçi',                'out',      10),
    ('kargo',       'Kargo / Lojistik',         'out',      11),
    ('maas',        'Maaş + SGK + Vergi',       'out',      12),
    ('vergi',       'Vergi / KDV',              'out',      13),
    ('reklam',      'Reklam / Pazarlama',       'out',      14),
    ('yazilim',     'Yazılım / Abonelik',       'out',      15),
    ('danismanlik', 'Danışmanlık / Hukuk',      'out',      16),
    ('banka',       'Banka / Komisyon',         'out',      17),
    ('diger_cik',   'Diğer çıkış',              'out',      18),
    ('ic_transfer', 'Şirketler arası transfer', 'transfer', 20),
    ('hesap_arasi', 'Kendi hesapları arası',    'transfer', 21)
ON CONFLICT (id) DO NOTHING;

-- Hareketler. (account_id, external_id) tekil: senkron ve ekstre yüklemesi
-- ON CONFLICT DO NOTHING ile yazar, tekrar yükleme çift kayıt üretmez.
-- user_edited_at doluysa kategori / şirketler arası alanları kullanıcı
-- düzeltmiştir; otomatik eşleştirme bu satıra dokunmaz.
CREATE TABLE IF NOT EXISTS fin_transactions (
    id                       bigserial PRIMARY KEY,
    account_id               bigint NOT NULL REFERENCES fin_accounts (id) ON DELETE CASCADE,
    company_id               text NOT NULL REFERENCES fin_companies (id),
    booked_at                date NOT NULL,
    amount                   numeric(18, 2) NOT NULL,  -- işaretli: + giriş, − çıkış
    currency                 text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    description              text NOT NULL DEFAULT '',
    counterparty             text,
    category_id              text REFERENCES fin_categories (id),
    counterparty_company_id  text REFERENCES fin_companies (id),  -- doluysa şirketler arası
    external_id              text NOT NULL,  -- sağlayıcı id veya ekstre satır hash'i
    source                   text NOT NULL CHECK (source IN ('api', 'csv', 'manual')),
    user_edited_at           timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, external_id),
    CHECK (counterparty_company_id IS NULL OR counterparty_company_id <> company_id)
);
CREATE INDEX IF NOT EXISTS idx_fin_transactions_company_date
    ON fin_transactions (company_id, booked_at);
CREATE INDEX IF NOT EXISTS idx_fin_transactions_ic
    ON fin_transactions (counterparty_company_id) WHERE counterparty_company_id IS NOT NULL;

-- Kurlar: 1 birim para = usd_rate USD. USD her zaman 1, tabloda tutulmaz.
CREATE TABLE IF NOT EXISTS fin_fx_rates (
    date      date NOT NULL,
    currency  text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    usd_rate  numeric(18, 8) NOT NULL CHECK (usd_rate > 0),
    source    text NOT NULL DEFAULT 'ecb',
    PRIMARY KEY (date, currency)
);

-- Planlı nakit (projeksiyon): vadeli alacak/borç ve tekrarlayan kalemler.
-- amount yönsüz pozitif; işaret direction'dan gelir.
CREATE TABLE IF NOT EXISTS fin_planned_items (
    id            bigserial PRIMARY KEY,
    company_id    text NOT NULL REFERENCES fin_companies (id),
    direction     text NOT NULL CHECK (direction IN ('in', 'out')),
    title         text NOT NULL,
    counterparty  text,
    amount        numeric(18, 2) NOT NULL CHECK (amount >= 0),
    currency      text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    due_date      date NOT NULL,
    recurrence    text NOT NULL DEFAULT 'none' CHECK (recurrence IN ('none', 'weekly', 'monthly')),
    recur_until   date,
    probability   numeric(3, 2) NOT NULL DEFAULT 1 CHECK (probability BETWEEN 0 AND 1),
    status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
    category_id   text REFERENCES fin_categories (id),
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (recur_until IS NULL OR recur_until >= due_date)
);
CREATE INDEX IF NOT EXISTS idx_fin_planned_items_open
    ON fin_planned_items (company_id, due_date) WHERE status = 'open';

-- Haftalık bilanço: şirket × hafta (pazartesi). fx, kesinleştirmede
-- kilitlenen kurlardır ({"TRY": 0.0291, "EUR": 1.08} → usd_rate).
CREATE TABLE IF NOT EXISTS fin_snapshots (
    id          bigserial PRIMARY KEY,
    company_id  text NOT NULL REFERENCES fin_companies (id),
    week_date   date NOT NULL,
    locked      boolean NOT NULL DEFAULT false,
    locked_at   timestamptz,
    fx          jsonb NOT NULL DEFAULT '{}',
    notes       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, week_date),
    CHECK (extract(isodow FROM week_date) = 1)
);

CREATE TABLE IF NOT EXISTS fin_snapshot_lines (
    id           bigserial PRIMARY KEY,
    snapshot_id  bigint NOT NULL REFERENCES fin_snapshots (id) ON DELETE CASCADE,
    side         text NOT NULL CHECK (side IN ('asset', 'liability')),
    group_name   text NOT NULL,
    label        text NOT NULL,
    amount       numeric(18, 2) NOT NULL DEFAULT 0,
    currency     text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    -- Satırın kaynağı: hesap bakiyesi, açık planlı kalem, şirketler arası
    -- net transfer veya elle giriş. Yeni haftaya yalnız 'manual' kopyalanır.
    origin       text NOT NULL DEFAULT 'manual',
    -- Bilanço bölümü; side ile tutarlı (aktif: dönen/duran, pasif: KV/UV/özkaynak).
    section      text,
    sort         int NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fin_snapshot_lines_snapshot ON fin_snapshot_lines (snapshot_id);

-- Geçiş: bölüm kolonu ve genişleyen kaynak listesi (eski kurulumlar için).
ALTER TABLE fin_snapshot_lines ADD COLUMN IF NOT EXISTS section text;
ALTER TABLE fin_snapshot_lines DROP CONSTRAINT IF EXISTS fin_snapshot_lines_origin_check;
ALTER TABLE fin_snapshot_lines ADD CONSTRAINT fin_snapshot_lines_origin_check
    CHECK (origin IN ('account', 'planned', 'intercompany', 'manual'));
ALTER TABLE fin_snapshot_lines DROP CONSTRAINT IF EXISTS fin_snapshot_lines_section_check;
ALTER TABLE fin_snapshot_lines ADD CONSTRAINT fin_snapshot_lines_section_check
    CHECK (
        section IS NULL
        OR (side = 'asset' AND section IN ('current_assets', 'noncurrent_assets'))
        OR (side = 'liability' AND section IN ('current_liabilities', 'longterm_liabilities', 'equity'))
    );

-- Kilitli haftanın satırları değiştirilemez. UPDATE'te satır başka haftaya
-- taşınıyorsa hem eski hem yeni hafta kontrol edilir.
CREATE OR REPLACE FUNCTION fin_snapshot_lines_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM fin_snapshots
        WHERE locked
          AND id IN (
              CASE WHEN TG_OP <> 'DELETE' THEN NEW.snapshot_id END,
              CASE WHEN TG_OP <> 'INSERT' THEN OLD.snapshot_id END
          )
    ) THEN
        RAISE EXCEPTION 'Bu hafta kesinleştirildi, satırlar değiştirilemez';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS fin_snapshot_lines_guard ON fin_snapshot_lines;
CREATE TRIGGER fin_snapshot_lines_guard
    BEFORE INSERT OR UPDATE OR DELETE ON fin_snapshot_lines
    FOR EACH ROW EXECUTE FUNCTION fin_snapshot_lines_guard();

-- Kilitli hafta güncellenemez, silinemez, kilidi açılamaz.
-- Kilitlenirken locked_at damgalanır.
CREATE OR REPLACE FUNCTION fin_snapshots_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF OLD.locked THEN
        RAISE EXCEPTION 'Bu hafta kesinleştirildi, değiştirilemez';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF NEW.locked THEN
        NEW.locked_at := now();
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS fin_snapshots_guard ON fin_snapshots;
CREATE TRIGGER fin_snapshots_guard
    BEFORE UPDATE OR DELETE ON fin_snapshots
    FOR EACH ROW EXECUTE FUNCTION fin_snapshots_guard();

-- Stratejik kararlar. company_id NULL = grup geneli.
CREATE TABLE IF NOT EXISTS fin_decisions (
    id            bigserial PRIMARY KEY,
    company_id    text REFERENCES fin_companies (id),
    title         text NOT NULL,
    description   text NOT NULL DEFAULT '',
    owner         text,
    status        text NOT NULL DEFAULT 'acik'
        CHECK (status IN ('acik', 'aksiyon', 'devam', 'tamam', 'iptal')),
    priority      text NOT NULL DEFAULT 'orta' CHECK (priority IN ('yuksek', 'orta', 'dusuk')),
    due_date      date,
    impact_usd    numeric(18, 2),  -- tahmini yıllık nakit etkisi, + / −
    meeting_date  date,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION fin_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS fin_decisions_touch ON fin_decisions;
CREATE TRIGGER fin_decisions_touch
    BEFORE UPDATE ON fin_decisions
    FOR EACH ROW EXECUTE FUNCTION fin_touch_updated_at();

-- Otomatik kategori kuralları. pattern sadeleştirilmiş metindir (küçük harf,
-- Türkçe harfler ASCII, tek boşluk). exact: karşı taraf (yoksa açıklama)
-- birebir; contains: açıklama + karşı tarafta geçer. Öncelik: kullanıcı
-- kuralı > sistem kuralı; aynı kaynakta exact > contains. Kullanıcının
-- elle düzelttiği hareket kurallardan etkilenmez.
CREATE TABLE IF NOT EXISTS fin_category_rules (
    id           bigserial PRIMARY KEY,
    company_id   text REFERENCES fin_companies (id),  -- NULL = tüm şirketler
    direction    text NOT NULL DEFAULT 'any' CHECK (direction IN ('in', 'out', 'any')),
    match_type   text NOT NULL CHECK (match_type IN ('exact', 'contains')),
    pattern      text NOT NULL CHECK (length(pattern) >= 2),
    category_id  text NOT NULL REFERENCES fin_categories (id),
    source       text NOT NULL DEFAULT 'user' CHECK (source IN ('system', 'user')),
    label        text,  -- kullanıcıya gösterilen özgün yazım
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_category_rules
    ON fin_category_rules (coalesce(company_id, ''), direction, match_type, pattern);

-- Hangi kuralın kategori verdiği; kural silinince bu satırlar geri alınır.
ALTER TABLE fin_transactions ADD COLUMN IF NOT EXISTS category_rule_id bigint;

-- Geçiş: kelime ortasında eşleşmesin diye tam kelimeye çevrilen sistem kalıpları; eklemeden önce
-- (ör. 'canva' → 'Odd Canvas Llc' yanlış eşleşmesi).
UPDATE fin_category_rules SET pattern = pattern || ' '
WHERE source = 'system' AND match_type = 'contains' AND pattern IN ('canva', 'notion', 'deel');

INSERT INTO fin_category_rules (direction, match_type, pattern, category_id, source)
SELECT v.direction, 'contains', v.pattern, v.category_id, 'system'
FROM (VALUES
    ('in', 'stripe', 'satis'), ('in', 'payoneer', 'satis'), ('in', 'paypal', 'satis'),
    ('in', 'amazon', 'satis'), ('in', 'shopify', 'satis'), ('in', 'etsy', 'satis'),
    ('in', 'trendyol', 'satis'), ('in', 'hepsiburada', 'satis'), ('in', 'iyzico', 'satis'),
    ('in', 'gumroad', 'satis'), ('in', 'paddle', 'satis'),
    ('out', 'refund for', 'satis'), ('out', 'chargeback', 'satis'),
    ('out', 'internal revenue service', 'vergi'), ('out', 'irs usataxpymt', 'vergi'),
    ('out', 'franchise tax', 'vergi'), ('out', 'sales tax', 'vergi'), ('out', 'finanzamt', 'vergi'),
    ('out', 'vergi', 'vergi'), ('out', 'kdv', 'vergi'), ('out', 'gib ', 'vergi'),
    ('out', 'sgk', 'maas'), ('out', 'gusto', 'maas'), ('out', 'payroll', 'maas'), ('out', 'deel ', 'maas'),
    ('out', 'rippling', 'maas'), ('out', 'maas', 'maas'), ('out', 'gehalt', 'maas'), ('out', 'lohn', 'maas'),
    ('out', 'facebk', 'reklam'), ('out', 'facebook', 'reklam'), ('out', 'meta ads', 'reklam'),
    ('out', 'google ads', 'reklam'), ('out', 'tiktok', 'reklam'), ('out', 'linkedin', 'reklam'),
    ('out', 'amazon web services', 'yazilim'), ('out', 'aws ', 'yazilim'), ('out', 'google workspace', 'yazilim'),
    ('out', 'gsuite', 'yazilim'), ('out', 'microsoft', 'yazilim'), ('out', 'adobe', 'yazilim'),
    ('out', 'slack', 'yazilim'), ('out', 'notion ', 'yazilim'), ('out', 'zoom.us', 'yazilim'),
    ('out', 'atlassian', 'yazilim'), ('out', 'github', 'yazilim'), ('out', 'openai', 'yazilim'),
    ('out', 'anthropic', 'yazilim'), ('out', 'canva ', 'yazilim'), ('out', 'figma', 'yazilim'),
    ('out', 'hubspot', 'yazilim'), ('out', 'zoho', 'yazilim'), ('out', 'godaddy', 'yazilim'),
    ('out', 'namecheap', 'yazilim'), ('out', 'cloudflare', 'yazilim'), ('out', 'vercel', 'yazilim'),
    ('out', 'supabase', 'yazilim'), ('out', 'shopify', 'yazilim'),
    ('out', 'dhl', 'kargo'), ('out', 'ups ', 'kargo'), ('out', 'fedex', 'kargo'), ('out', 'usps', 'kargo'),
    ('out', 'aras kargo', 'kargo'), ('out', 'yurtici', 'kargo'), ('out', 'mng kargo', 'kargo'),
    ('out', 'ptt', 'kargo'), ('out', 'shipentegra', 'kargo'), ('out', 'easyship', 'kargo'),
    ('out', 'shipbob', 'kargo'), ('out', 'shippo', 'kargo'),
    ('out', 'transfer fee', 'banka'), ('out', 'wire fee', 'banka'), ('out', 'service fee', 'banka'),
    ('out', 'bank fee', 'banka'), ('out', 'komisyon', 'banka'), ('out', 'commission', 'banka'),
    ('out', 'law ', 'danismanlik'), ('out', 'legal', 'danismanlik'), ('out', 'hukuk', 'danismanlik'),
    ('out', 'avukat', 'danismanlik'), ('out', 'consult', 'danismanlik'), ('out', 'muhasebe', 'danismanlik'),
    ('out', 'accounting', 'danismanlik'), ('out', 'cpa ', 'danismanlik'), ('out', 'noter', 'danismanlik')
) AS v(direction, pattern, category_id)
ON CONFLICT DO NOTHING;

-- RLS açık, politika yok: Supabase anon/authenticated rolleri finans
-- tablolarını okuyamaz; uygulama tablo sahibi olarak bağlanır.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'fin_companies', 'fin_accounts', 'fin_categories', 'fin_transactions', 'fin_fx_rates',
        'fin_planned_items', 'fin_snapshots', 'fin_snapshot_lines', 'fin_decisions',
        'fin_category_rules'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;
