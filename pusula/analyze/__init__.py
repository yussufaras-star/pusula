"""Analiz katmanı.

events tablosundaki ham kayıtları LLM (Anthropic API) ile yapılandırılmış
analize dönüştürür: davranışsal tespitler, taahhüt (commitment) çıkarımı,
thread durum güncellemeleri. Her tespit kaynaktan 15 kelimeyi geçmeyen
bir alıntı ile kanıtlanır. body_quality='low' kayıtlar davranışsal
analizden hariç tutulur. Bu paket henüz boş; analiz modülleri sonraki
görevlerde eklenecek.
"""
