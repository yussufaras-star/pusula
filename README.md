# Pusula

Rexven telesatış operasyonunu her gece analiz eden ve sabah rapor bırakan
asistan. İki muhatabı var: satış temsilcileri (günlük bireysel geri bildirim)
ve satış direktörü (haftalık strateji + proaktif uyarılar).

## Mimari

Pipeline üç katmandan oluşur; katmanlar arası veri akışı Postgres üzerinden
yürür.

```
kaynaklar (çağrı, e-posta, WhatsApp, toplantı, Zoho)
        │
        ▼
  pusula/ingest      olayları çeker, normalize eder, events tablosuna yazar
        │
        ▼
  pusula/analyze     LLM ile yapılandırılmış analiz üretir
        │            (taahhüt çıkarımı, davranışsal tespit, thread durumu)
        ▼
  pusula/deliver     raporları üretir, onaydan geçirir, Cliq'e gönderir
```

Destekleyici bölümler:

- `pusula/db` — şema (`schema.sql`), pydantic modelleri (`models.py`),
  psycopg3 erişim katmanı (`client.py`). ORM yok, düz SQL.
- `pusula/prompts` — tüm LLM promptları .md dosyası olarak burada durur.
- `pusula/zoho` — Zoho CRM ve Cliq API istemcileri.
- `eval` — analiz kalitesini ölçen değerlendirme senaryoları.

Temel ilkeler:

- Üst katmanlar ham transkript okumaz, sadece yapılandırılmış analiz
  çıktısı okur.
- Her davranışsal tespitin yanında kaynaktan 15 kelimeyi geçmeyen bir
  alıntı bulunur; kanıtsız iddia üretilmez.
- `body_quality='low'` kayıtlar davranışsal analizden hariç tutulur.
- Cliq'e giden hiçbir mesaj onaysız gitmez.
- Tüm timestamp'ler Europe/Istanbul; DB'de timestamptz.

## Veri modeli

| Tablo | İçerik |
|---|---|
| `events` | Tüm kanallardan gelen ham olay kayıtları. `(channel, source_ref)` tekil; ingest idempotent. |
| `threads` | Müşteri bazında konuşma zinciri özeti ve durumu. |
| `commitments` | Konuşmalardan çıkarılan taahhütler ve akıbetleri (open/fulfilled/broken/expired). |
| `sync_state` | Kaynak bazında senkronizasyon imleci. |

## Bağımlılıklar

`requirements.txt` üzerinden:

- `psycopg[binary]` + `psycopg-pool` — Postgres erişimi
- `pydantic` — model doğrulama
- `httpx` — Zoho HTTP
- `python-dotenv` — ortam değişkenleri
- `streamlit` — iç operasyon paneli

## Kurulum

1. Python 3.11 ile sanal ortam oluştur:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Ortam değişkenlerini ayarla:

```bash
cp .env.example .env
# .env içine gerçek değerleri yaz (git'e girmez)
```

3. Şemayı Postgres'e (Supabase) uygula:

```bash
psql "$DATABASE_URL" -f pusula/db/schema.sql
```

4. Operasyon paneli (iç kullanım, kimlik doğrulama yok):

```bash
streamlit run app/panel.py
```

Bağlantı `DATABASE_URL_POOLED` (yoksa `DATABASE_URL`). Sorgu önbelleği 15 dakika.
