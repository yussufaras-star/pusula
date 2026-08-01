-- Rexven seed verisi: hata kodu kataloğu ve segment tanımları.
-- scripts/seed_org.py ile yüklenir; iki kez çalıştırılabilir
-- (çakışmada tanım alanları tazelenir, bug_codes.active'e dokunulmaz).
-- Kaynak: ROADMAP.md'deki hata kodları bölümü.

-- Çağrı içi kodlar (intra_call).
INSERT INTO bug_codes (org_id, code, scope, title, description) VALUES
    ('rexven', '0x0001', 'intra_call', 'Erken fiyat açılışı',
     'Müşteri fiyatı sormadan temsilci fiyatı kendiliğinden açtı.'),
    ('rexven', '0x0002', 'intra_call', 'Kapanış denemesi yok',
     'Görüşme boyunca temsilci hiçbir kapanış denemesi yapmadı.'),
    ('rexven', '0x0003', 'intra_call', 'İtirazın üstüne gidilmedi',
     'Müşteri itirazı duyuldu ama temsilci üstüne gitmedi, itiraz yanıtsız kaldı.'),
    ('rexven', '0x0004', 'intra_call', 'Keşifsiz sunum',
     'Keşif soruları sorulmadan doğrudan ürün sunumuna geçildi.'),
    ('rexven', '0x0005', 'intra_call', 'Sonraki adım belirsiz',
     'Görüşme sonunda sonraki adım tarih veya eylem olarak netleştirilmedi, belirsiz bırakıldı.'),
    ('rexven', '0x0006', 'intra_call', 'Konuşma dengesi bozuk',
     'Temsilci toplam konuşma süresinin %70''inden fazlasını kullandı.'),
    ('rexven', '0x0007', 'intra_call', 'Müşterinin sözü kesildi',
     'Temsilci müşterinin sözünü üç veya daha fazla kez kesti.'),
    ('rexven', '0x0008', 'intra_call', 'Fayda anlatılmadı',
     'Ürün özellikleri anlatıldı ama müşteriye sağlayacağı fayda anlatılmadı.'),
    ('rexven', '0x0009', 'intra_call', 'Karar verici teyidi yok',
     'Görüşülen kişinin karar verici olduğu teyit edilmedi.'),
    ('rexven', '0x000A', 'intra_call', 'İstenmeden indirim',
     'Müşteri indirim istemeden temsilci indirim teklif etti.')
ON CONFLICT (org_id, code) DO UPDATE SET
    scope = EXCLUDED.scope,
    title = EXCLUDED.title,
    description = EXCLUDED.description;

-- Kanal arası kodlar (cross_channel).
INSERT INTO bug_codes (org_id, code, scope, title, description) VALUES
    ('rexven', '0x0010', 'cross_channel', 'Materyal sözü tutulmadı',
     'Söz verilen materyal 24 saat içinde gönderilmedi.'),
    ('rexven', '0x0011', 'cross_channel', 'Yanıt gecikmesi',
     'Müşteri mesajına mesai saatleri içinde 4 saatten uzun sürede yanıt verilmedi.'),
    ('rexven', '0x0012', 'cross_channel', 'Toplantı sonrası teyit yok',
     'Toplantı sonrasında özet veya teyit maili gönderilmedi.'),
    ('rexven', '0x0013', 'cross_channel', 'Takip zinciri koptu',
     'Sıcak Çekirdek segmentindeki müşteride takip zinciri 7 günden uzun süre koptu.'),
    ('rexven', '0x0014', 'cross_channel', 'Kanal terk',
     'Müşteri WhatsApp''tan yazdı, dönüş aynı kanaldan yapılmadı.'),
    ('rexven', '0x0015', 'cross_channel', 'No-show sonrası plansızlık',
     'Müşteri toplantıya gelmedi, yeniden planlama girişimi yapılmadı.'),
    ('rexven', '0x0016', 'cross_channel', 'Toplantı öncesi hazırlık teması yok',
     'Toplantı ayarlandı ama öncesinde hazırlık amaçlı bir temas kurulmadı.'),
    ('rexven', '0x0017', 'cross_channel', 'Teklif takipsiz',
     'Teklif gönderildi, 5 gün içinde takip teması yapılmadı.')
ON CONFLICT (org_id, code) DO UPDATE SET
    scope = EXCLUDED.scope,
    title = EXCLUDED.title,
    description = EXCLUDED.description;

-- Segment tanımları.
INSERT INTO segments (org_id, key, label, sort_order) VALUES
    ('rexven', 'hot_core', 'Sıcak Çekirdek', 1),
    ('rexven', 'warming', 'Isıtma Havuzu', 2),
    ('rexven', 'unverified', 'Doğrulanmamış Statü', 3),
    ('rexven', 'archive', 'Arşiv', 4)
ON CONFLICT (org_id, key) DO UPDATE SET
    label = EXCLUDED.label,
    sort_order = EXCLUDED.sort_order;

-- Calls sonuç picklist → kanonik outcome (gerçek Zoho değerleri).
INSERT INTO call_outcomes (org_id, raw_value, outcome_key, category, is_progress, sort_order) VALUES
    ('rexven', 'Yanıt yok/Meşgul',   'no_answer',      'not_reached', false, 10),
    ('rexven', 'Geciken',            'delayed',        'pending',     false, 20),
    ('rexven', 'Geri Dönüş',         'callback',       'pending',     false, 30),
    ('rexven', 'Bilgi Almak İstedi', 'info_requested', 'positive',    false, 40),
    ('rexven', 'Sunum Yapıldı',      'demo_done',      'positive',    true,  50),
    ('rexven', 'Randevu Alındı',     'meeting_booked', 'positive',    true,  60),
    ('rexven', 'İlgilenmiyorum',     'not_interested', 'negative',    false, 70),
    ('rexven', 'İptal Edildi',       'cancelled',      'negative',    false, 80)
ON CONFLICT (org_id, raw_value) DO UPDATE SET
    outcome_key = EXCLUDED.outcome_key,
    category = EXCLUDED.category,
    is_progress = EXCLUDED.is_progress,
    sort_order = EXCLUDED.sort_order;

-- Outgoing_Call_Status teknik durum (satış sonucu değil).
INSERT INTO call_statuses (org_id, raw_value, status_key) VALUES
    ('rexven', 'Tamamlandı', 'connected'),
    ('rexven', 'Geciken', 'overdue'),
    ('rexven', 'Planlandı', 'scheduled'),
    ('rexven', 'İptal Edildi', 'cancelled')
ON CONFLICT (org_id, raw_value) DO UPDATE SET
    status_key = EXCLUDED.status_key;
