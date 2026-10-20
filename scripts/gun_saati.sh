# gun-saati.yml yardımcıları (source edilir). Saatler İstanbul saatidir.

# tetikle SS:DD dosya.yml gecikme_dk
# Saate kadar bekler, iş dosyasını dispatch eder. Saat gecikme payından
# fazla geçmişse ya da o saat için zaten tetiklendiyse atlar.
tetikle() {
  local saat="$1" dosya="$2" pay="${3:-10}"
  local hedef simdi
  hedef=$(TZ=Europe/Istanbul date -d "today $saat" +%s)
  simdi=$(date +%s)
  if [ "$simdi" -gt $((hedef + pay * 60)) ]; then
    echo "atlandı: $saat $dosya (saati geçti)"
    return 0
  fi
  if [ "$hedef" -gt "$simdi" ]; then
    sleep $((hedef - simdi))
  fi
  # Yedek kopya (ör. gecikmiş cron) sırada bekleyip pay içinde başlarsa aynı
  # kart ikinci kez gider (19 Ekim 18:30). Saatten 2 dk önceden beri iptal
  # edilmemiş bir çalışma varsa tekrar tetiklenmez.
  local n
  n=$(gh run list --workflow "$dosya" --limit 20 --json createdAt,conclusion \
    --jq "[.[] | select(.conclusion != \"cancelled\") | select((.createdAt | fromdateiso8601) >= $((hedef - 120)))] | length") || n=0
  if [ "${n:-0}" -gt 0 ]; then
    echo "atlandı: $saat $dosya (zaten tetiklenmiş)"
    return 0
  fi
  echo "tetikleniyor: $saat $dosya"
  gh workflow run "$dosya" --ref main || echo "::warning::$dosya tetiklenemedi ($saat)"
}

# kart SS:DD — kart görselleri; pazar gönderilmez, 60 dk gecikmeye kadar gider.
kart() {
  if [ "$(TZ=Europe/Istanbul date +%u)" = "7" ]; then
    echo "atlandı: $1 kart (pazar)"
    return 0
  fi
  tetikle "$1" card-snapshots.yml 60
}
