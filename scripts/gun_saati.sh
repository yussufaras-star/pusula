# gun-saati.yml yardımcıları (source edilir). Saatler İstanbul saatidir.

# tetikle SS:DD dosya.yml gecikme_dk
# Saate kadar bekler, iş dosyasını dispatch eder. Saat gecikme payından
# fazla geçmişse atlar (ör. iş geç başladı ya da yedek kopya sırada bekledi).
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
