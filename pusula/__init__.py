"""Pusula — Rexven satış operasyonu analiz asistanı.

Her gece çalışan pipeline: veri toplar (ingest), analiz eder (analyze)
ve sabah raporlarını hazırlar (deliver). Katmanlar arası veri akışı
Postgres üzerinden yürür; üst katmanlar ham transkript okumaz, sadece
yapılandırılmış analiz çıktısı okur.
"""
