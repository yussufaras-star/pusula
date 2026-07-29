"""Veri toplama katmanı (ingest).

Dış kaynaklardan (çağrı kayıtları, e-posta, WhatsApp, toplantı notları,
Zoho aktiviteleri) olayları çeker, normalize eder ve events tablosuna
yazar. Her kaynak kendi ingester modülünde yaşar; senkron durumu
sync_state tablosunda tutulur. Bu paket henüz boş; ingester'lar sonraki
görevlerde eklenecek.
"""
