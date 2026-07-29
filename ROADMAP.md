# Pusula — Yol Haritası

## Amaç
Rexven telesatış operasyonunun çok kanallı verisini toplayıp iki çıktı üretmek:
temsilcilere günlük davranışsal geri bildirim, direktöre haftalık strateji
ve proaktif uyarı.

## Temel ilkeler
- Analiz birimi çağrı değil, **thread** (bir ilişkinin tüm kanallardaki geçmişi).
- Kanıtsız iddia yok. Her davranışsal tespitin yanında ≤15 kelimelik alıntı.
- Karşı-olgusal tahmin yok. "Şunu yapsaydın %X artardı" cümlesi kurulmaz.
  Yüzde ancak öncesi/sonrası ölçümle verilir.
- Anomali tespiti deterministik koddur, LLM değildir. LLM sadece
  tespit edilmiş sapmayı cümleye döker.
- Cliq'e onaysız mesaj gitmez (kalibrasyon dönemi boyunca).

## Katmanlar

### Ingestion
| Kaynak | Yol | Durum |
|---|---|---|
| Çağrı kaydı + transkript | Verimor CDR + faster-whisper | Stage 1 mevcut, taşınacak |
| Çağrı metadata | CRM Calls | Bekliyor |
| Toplantı | CRM Events + Bookings | Bekliyor |
| Mail | Zoho Mail API (lead/contact filtresi zorunlu) | Bekliyor |
| WhatsApp | Zoho Flow → webhook (geriye dönük veri yok) | Bekliyor |

### Analiz geçişleri
- **Pass 0 — Anomali tarayıcı.** Deterministik. Hareketli ortalama + sapma.
  Eşik aşımında uyarı üretir. Pusula'nın proaktif tarafı.
- **Pass 1 — Olay çıkarımı.** Her event → yapılandırılmış JSON. Kanala göre
  farklı prompt, ortak çıktı şeması.
- **Pass 1.5 — Söz eşleştirme.** LLM değil, kod. Açık commitments'ı
  sonraki olaylarla eşleştirir.
- **Pass 2 — Thread durumu.** Sadece hareket görmüş hatlar.
- **Pass 3 — Temsilci günlük geri bildirimi.** Çok kanallı, tek odak maddesi.
- **Pass 4 — Haftalık strateji.** Agregasyon + segment hareketi + VoC.

### Üst katmanlar (ingestion sonrası)
- `rep_profile_snapshot` — haftalık yetkinlik zaman serisi.
  Kümülatif gelişim haritası. SONAR-41 ile karşılaştırma köprüsü:
  beyan edilen yetkinlik vs. gözlenen davranış.
- **Müşteri Sesi (VoC)** — itiraz tiplerinin Lead_Source kırılımı.
  Pazarlama bütçesi kararlarını besler.
- **Huni sürtünmesi** — segment/aşamada geçen süre trendi.
- **Arena köprüsü** — tekrar eden hata koduna göre senaryo ataması.

## Kapsam dışı (şimdilik)
- **Forecasting** — en az bir çeyrek kapanmış sonuç verisi birikmeden yapılmaz.
- **CRM'e otomatik yazma** — yapılacaksa taslak + onay modeliyle. Faz 2.
- **Oyunlaştırma / liderlik tablosu** — 4 kişilik ekipte ters etki yapar.
- **Kullanıcı konuşma tarzı taklidi** — iki register yeter: temsilci, direktör.

## Hata kodları

### Çağrı içi
0x0001 Fiyatı müşteri sormadan temsilci açtı
0x0002 Kapanış denemesi yok
0x0003 İtiraz duyulup üstüne gidilmedi
0x0004 Keşif yok, doğrudan sunuma geçildi
0x0005 Sonraki adım belirsiz bırakıldı
0x0006 Temsilci konuşma süresinin %70'inden fazlasını kullandı
0x0007 Müşterinin sözü kesildi (3+ kez)
0x0008 Ürün anlatıldı, fayda anlatılmadı
0x0009 Karar verici teyidi alınmadı
0x000A İndirim istenmeden teklif edildi

### Kanal arası
0x0010 Söz verilen materyal gönderilmedi (24 saat)
0x0011 Müşteri mesajına yanıt gecikmesi (mesai içi 4 saat+)
0x0012 Toplantı sonrası özet/teyit maili yok
0x0013 Sıcak Çekirdek'te takip zinciri koptu (7 gün+)
0x0014 Kanal terk — müşteri WhatsApp'tan yazdı, aynı kanaldan dönülmedi
0x0015 No-show sonrası yeniden planlama girişimi yok
0x0016 Toplantı ayarlandı, öncesinde hazırlık teması yok
0x0017 Teklif gönderildi, takip yok (5 gün+)

## Sıra
1. ~~DB iskeleti~~ ✅
2. ~~Kimlik çözümleme~~ ✅
3. Zoho auth + CRM istemcisi
4. Ingester arayüzü
5. Stage 1'i (Verimor) repoya taşı, events'e yaz
6. CRM Calls ingester
7. Pass 1 + golden set (20 çağrı, elle etiketli)
8. Pass 1.5 söz eşleştirme
9. Pass 3 + onay kuyruğu
10. Cliq teslimatı
11. Mail ingester
12. WhatsApp webhook
13. Pass 0 anomali + Pass 4 strateji
14. Üst katmanlar

## KVKK notu
Çalışan e-posta ve mesaj içeriğinin sistematik analizi yapılıyor.
GmbH tarafı da kapsamda. Ekibe yazılı aydınlatma yapılmadan
mail ve WhatsApp ingester'ları canlıya alınmaz.
