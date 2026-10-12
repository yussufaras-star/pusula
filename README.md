# Pusula

Rexven telesatış operasyonunun çağrı, toplantı, Zoho CRM ve Cliq verilerini
Postgres'te birleştiren iç operasyon sistemi. Vercel web paneli mesai içi
KPI'ları ekip ve temsilci seviyesinde gösterir; ölçülebilir standartların
altındaki sonuçlar Cliq üzerinden saatlik olarak dürtülür.

## Mimari

Çalışan veri akışı Postgres merkezlidir. Ingest işleri ortak kayıtları yazar;
Vercel paneli aynı KPI sorgularını web üzerinden sunar ve zamanlanmış
deterministik kurallar Cliq dürtülerini üretir.

```
çağrı / Zoho CRM / Bookings / Verimor
        │
        ▼
  pusula/ingest      kaynakları çeker, normalize eder, Postgres'e yazar
        │
        ▼
  pusula/temas       temas / çevirme / dönüş / ulaşma tanımları (tek kaynak)
  pusula/metrics     tek tip KPI vektörü: her aralık, temsilci, saat/gün kırılımı
  pusula/live        canlı kokpit: bugün, aynı saatin 90 günlük temposu, hareketsizlik
  pusula/panel_*     dönemsel raporlar: huni, dönüşüm, ciro, profiller
        ▼
  pusula/web         FastAPI (oturum + /api/*)  ←  web/ React arayüzü (public/)
        │
        └── saatlik ölçülebilir KPI eşiği → Cliq webhook
```

LLM tabanlı `analyze`/`deliver` katmanları ve e-posta ingestion henüz
çalışan pipeline değildir; ilgili paketler bugün iskelet/gelecek kapsamıdır.
Destekleyici bölümler:

- `pusula/db` — şema (`schema.sql`), pydantic modelleri (`models.py`),
  psycopg3 erişim katmanı (`client.py`). ORM yok, düz SQL.
- `pusula/prompts` — ileride LLM promptları için ayrılmış paket.
- `pusula/zoho` — Zoho CRM ve Cliq API istemcileri.
- `eval` — analiz kalitesini ölçen değerlendirme senaryoları.

Temel ilkeler:

- Üst katmanlar ham transkript okumaz, sadece yapılandırılmış analiz
  çıktısı okur.
- Her davranışsal tespitin yanında kaynaktan 15 kelimeyi geçmeyen bir
  alıntı bulunur; kanıtsız iddia üretilmez.
- `body_quality='low'` kayıtlar davranışsal analizden hariç tutulur.
- CRM'e otomatik yazım yapılmaz. Saatlik KPI dürtmeleri deterministik
  eşiklere bağlıdır ve gönderim durumu veritabanında izlenir.
- Tüm timestamp'ler Europe/Istanbul; DB'de timestamptz.

## Veri modeli

| Tablo | İçerik |
|---|---|
| `events` | Tüm kanallardan gelen ham olay kayıtları. `(channel, source_ref)` tekil; ingest idempotent. |
| `threads` | Müşteri bazında konuşma zinciri özeti ve durumu. |
| `commitments` | Konuşmalardan çıkarılan taahhütler ve akıbetleri (open/fulfilled/broken/expired). |
| `sync_state` | Kaynak bazında senkronizasyon imleci. |
| `leads`, `contacts`, `deals`, `reps` | Zoho CRM satış hunisi, satış ve temsilci boyutları. |
| `identities`, `thread_merges`, blocklists | Müşteri/lead kimlik çözümleme ve dışlama kuralları. |
| `kpi_nudge_sends` | Saatlik KPI mesajları için kişi/saat bazında tekilleştirme ve teslimat durumu. |

## Bağımlılıklar

Temel API ve veri işleme bağımlılıkları `requirements.txt` üzerinden:

- `psycopg[binary]` + `psycopg-pool` — Postgres erişimi
- `pydantic` — model doğrulama
- `httpx` — Zoho HTTP
- `python-dotenv` — ortam değişkenleri
- `fastapi` — web API

Arayüz `web/` altında React + TypeScript (Vite) ile yazılır ve `public/`
klasörüne derlenir. Grafikler harici kütüphane olmadan SVG bileşenleridir.

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

4. Web paneli (yerel):

```bash
pip install uvicorn
python -m uvicorn vercel_app:app --port 8000   # API
npm --prefix web install
npm --prefix web run dev                       # arayüz, /api → 8000
```

Veritabanı olmadan arayüz geliştirmek için `npm --prefix web run dev:mock`
örnek veriyle açılır (yalnız geliştirme sunucusu; derlemeye girmez).
Giriş: `PUSULA_PASSWORDS_JSON` içindeki e-posta/şifre; temsilcinin e-postası
`reps.email` ile eşleşmelidir.

Windows'ta Akıllı Uygulama Denetimi yerel `.node`/`.dll` modüllerini
engelleyebilir; bu yüzden `web/package.json` rollup/esbuild'in WebAssembly
sürümlerini kullanır. Aynı nedenle psycopg yüklenemezse `eval/conftest.py`
veritabanı gerektirmeyen testleri yine çalıştırır.

### Ekranlar

- **Canlı** (`/api/live`): ekip KPI kutuları (arama, ulaşılan, ulaşma oranı,
  randevu, ortalama görüşme süresi, doluluk) bu saate kadar beklenen değerle;
  saat saat grafik; dikkat gerektirenler; temsilci tablosu (durum, hareketsiz
  süre, son faaliyet, son CRM işlemi, tempo, doluluk, saatlik ısı şeridi).
  Dakikada bir yenilenir. Temsilci yalnız kendi kartını ve ekip toplamını görür.
- **Karşılaştır**, **Arena**, **Raporlar**: sonraki aşamalar (`ROADMAP.md`).

Hareketsizlik = mesai içinde son arama bitişi, katılınan toplantı bitişi,
giden WhatsApp veya CRM işleminden bu yana geçen süre; ölçüm ucu son veri
senkronunu aşmaz. 15 dk "boşta", 30 dk "hareketsiz" (`pusula/live.py`).

## Satış operasyonu KPI standartları

Web paneli mevcut ölçüm tanımlarını tekrar kullanır: `events` çağrı ve
randevuları, `leads` Zoho statülerini, `contacts` satışa dönüşümü, `deals`
kazanılan satışları sağlar. Günlük KPI'lar; giden arama, benzersiz lead
ulaşma, dönüş/gelen arama, randevu ve katılım, görüşme süresi, doluluk ve
CRM tazeliğidir. Dönemsel KPI'lar; huni, satışa dönüşüm, satış döngüsü,
temsilci/ekip kırılımı ve cirodur. Lead ulaşma oranında payda en az 5
olmadan oran gösterilmez.

Başlangıç dürtü standartları (`pusula/kpi_standards.py`):

| KPI | Başlangıç standardı | Düşük örneklem davranışı |
|---|---|---|
| Giden arama | Aynı saat ve gün türünün önceki 90 günlük ortalamasının en az %80'i | En az 10 geçmiş iş günü yoksa dürtü yok |
| Lead ulaşma oranı | Aynı saat geçmiş oranının en az %80'i | Güncel ve geçmiş paydada en az 5 lead |
| Toplantı katılımı | En az %70 | En az 5 sonucu girilmiş randevu |
| Toplantı sonucu | Tamamlanan randevuların sonucu CRM'de girilmeli | Herhangi bir kayıtsız sonuç uyarılır |

Bunlar ürün içi başlangıç guardrail'leridir; performans sıralaması veya
kişisel yaptırım değildir. Veri hacmi azsa oranlar için uyarı verilmez.
Eşikler gerçek ekip geçmişiyle kalibre edildikten sonra
`pusula/kpi_standards.py` içinden değiştirilebilir.

`.github/workflows/hourly-kpi-nudges.yml`, GitHub Actions üzerinde hafta içi
10:20–18:20 ve cumartesi 10:20–14:20 (Europe/Istanbul) kontrolü çalıştırır.
Cumartesi 14:00–15:00 son saatin verisi mevcut ingest takviminde 15:07'de
gelir; mesai dışına otomatik dürtü göndermemek için bu son saat bildiriminden
çıkarılır, panelde ingest sonrası görüntülenir. Kişi ve ekip mesajları ayrı ayrı
aynı saat için bir kez gönderilir. `PUSULA_SHADOW_EMAIL` tanımlanırsa gerçek
alıcılar yerine gölge alıcıya gönderilir. Gönderim kaydı
`kpi_nudge_sends` tablosundadır.

## Vercel deploy

1. GitHub deposunu Vercel projesi olarak içe aktarın; kök dizin repo kökü,
   framework Python/FastAPI olarak algılanmalıdır. `vercel.json` derlemede
   `web/` arayüzünü `public/` altına üretir; Vercel `public/` dosyalarını
   CDN'den, `/api/*` isteklerini FastAPI'den sunar.
2. Vercel project settings → Environment Variables kısmına ekleyin:
   - `DATABASE_URL_POOLED`: Supabase transaction pooler bağlantısı (port
     `6543`); gerçek bağlantı dizesini yalnız Vercel secret olarak kaydedin.
   - `PUSULA_PASSWORDS_JSON`: e-posta → şifre JSON nesnesi; ör.
     `{"yusuf.aras@rexven.com":"...","temsilci@rexven.com":"..."}`.
   - `PUSULA_SESSION_SECRET`: en az 32 karakterlik rastgele imzalama anahtarı.
   - `CRON_SECRET`: korumalı cron API rotası için rastgele gizli değer.
3. Supabase SQL Editor'da `pusula/db/schema.sql` dosyasını çalıştırın.
   Böylece `kpi_nudge_sends` gönderim kayıt tablosu oluşturulur. Uygulama
   raporlama veritabanına şema değişikliği uygulandıktan sonra deploy edilmelidir.
4. Deploy edin, ardından `/api/health` adresindeki yapılandırma durumunu
   kontrol edin.

Saatlik dürtü iş akışı GitHub Actions'ta çalışır; GitHub deposunun Actions
secrets alanında `DATABASE_URL_POOLED`, `CLIQ_WEBHOOK_URL` ve isteğe bağlı
`PUSULA_SHADOW_EMAIL` bulunmalıdır. İş akışı UTC cron saatlerini kullanır
(Türkiye UTC+3). Üretim dürtülerini açmadan önce shadow alıcıyla doğrulayın.
Vercel Hobby ücretsizdir ancak yalnızca ticari olmayan kişisel kullanım içindir;
şirket içi satış operasyonunda Hobby kullanılmamalı, uygun plan veya başka bir
uyumlu host seçilmelidir.

## Kod tabanı özeti ve sınırlar

- `pusula/ingest`: Verimor, Zoho CRM/Bookings ve mevcut diğer kaynaklardan
  veri çekip ortak olay/lead modellerine dönüştürür. Çalıştırma zamanları
  `.github/workflows/` altındadır.
- `pusula/db`: Postgres şeması, model ve sorgu/kimlik erişimi.
- `pusula/temas.py`, `pusula/blocks.py`: ortak temas sınıflandırması ve
  İstanbul mesai/blok kuralları.
- `pusula/panel_data.py`, `pusula/panel_ciro.py`, `pusula/panel_profile.py`:
  KPI sorguları ve hesaplamaları.
- `pusula/zoho`: Zoho OAuth/CRM istemcileri; `pusula/block_share.py` Cliq
  gönderim yüzeyi.
- `scripts/`: ingest, raporlama, bakım ve mevcut ayrı günlük dürtü işleri.
- `eval/`: kimlik, panel metrikleri ve saatlik KPI standart testleri.
- `pusula/metrics.py`, `pusula/live.py`, `pusula/web/`: canlı KPI vektörü,
  kokpit ve API. `vercel_app.py` yalnız giriş noktasıdır.
- `web/`: React arayüzü. `analyze/` ve `deliver/` bugün tam ürün pipeline'ı
  olarak tamamlanmış değildir.

Veri akışının doğruluğu ingest'lerin düzenli ve Zoho kayıtlarının güncel
olmasına bağlıdır. Çağrı verisinden cevaplanan çağrı, benzersiz lead erişimi
ve görüşme süresi çıkarılabilir; ancak gerçek iş günü/izin takvimi, lead
kalifikasyon kalitesi, teklif aşaması süresi, marj, kota ve tahmin için
mevcut modelde tam veri yoktur. Dönüşüm metriklerinin alt sınırı
`2026-05-01` ve satış ekibi listesi `pusula/panel_ciro.py` içinde sabittir;
ekip/veri dönemi değiştiğinde ayrıca gözden geçirilmelidir.
