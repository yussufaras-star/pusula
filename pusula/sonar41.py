"""SONAR-41 satış davranış çerçevesi, Rexven dili.

Kaynak: Arena content/sonar41/framework.json (sürüm 1) + tenants/rexven/sonar.json
(Rexven uyarlaması; kaynağında TASLAK, gözden geçirilecek). Kodlar ve boyutlar sabittir,
yalnız ad ve tanım sektöre uyarlanır. Güncellemek için bu iki dosyadan yeniden üretilir.
"""

from __future__ import annotations

DIMS: dict[str, str] = {
    "K1": "Karşılama ve Teşhis",
    "K2": "Deneyim ve Değer Sunumu",
    "K3": "İtiraz ve Müzakere",
    "K4": "Kapama ve Karar",
    "K5": "Takip, İlişki ve Süreç"
}

BEHAVIORS: dict[str, dict[str, str]] = {
    "B01": {
        "dim": "K1",
        "name": "Açılışta kendini ve amacı tanıtma",
        "def": "İlk cümlelerde adını, Rexven'i ve görüşmenin amacını müşterinin kendi talebine bağlayarak net söyler; \"müsait misiniz / uygun musunuz\" diye sormaz, müşteriyi konuşturan bir soruyla devam eder. Müşteri kendiliğinden müsait olmadığını söylerse tek bir değer cümlesi ya da kısa bir soruyla ilgisini canlı tutar ve geri arama zamanını birlikte belirler (B34)."
    },
    "B02": {
        "dim": "K1",
        "name": "Sıcak ve kişisel karşılama",
        "def": "Müşteriye adıyla hitap eder, ilk temasta güven ve samimiyet hissettirir."
    },
    "B03": {
        "dim": "K1",
        "name": "Temas nedenini öğrenme",
        "def": "Formu neden doldurduğunu ya da toplantıyı neden istediğini açık uçlu soruyla öğrenir."
    },
    "B04": {
        "dim": "K1",
        "name": "Mevcut e-ticaret durumunu keşfetme",
        "def": "Şu an satış yapıp yapmadığını, hangi platform ve tedarikçileri kullandığını, neyin işe yarayıp neyin yaramadığını sorar."
    },
    "B05": {
        "dim": "K1",
        "name": "Kullanım ihtiyacını netleştirme",
        "def": "Hedefini (ek gelir, tam zamanlı iş, kendi markası), ayırabileceği zamanı ve gerçek ihtiyacını somut örneklerle ortaya çıkarır."
    },
    "B06": {
        "dim": "K1",
        "name": "Bütçe ve ödeme yöntemini netleştirme",
        "def": "Bütçe aralığını ve ödeme planı beklentisini incitmeden öğrenir."
    },
    "B07": {
        "dim": "K1",
        "name": "Karar vericiyi ve süreci belirleme",
        "def": "Kararı kimin, nasıl ve kiminle vereceğini öğrenir."
    },
    "B08": {
        "dim": "K1",
        "name": "Değerlendirilen rakipleri öğrenme",
        "def": "Başka hangi tedarikçi, platform, eğitim ya da mentorluk programlarına baktığını sorar."
    },
    "B09": {
        "dim": "K1",
        "name": "Satın alma zamanını öğrenme",
        "def": "Ne zaman başlamak istediğini ve ilk satış için zaman beklentisini netleştirir."
    },
    "B10": {
        "dim": "K2",
        "name": "İhtiyaca bağlı paket önerme",
        "def": "Duyduğu hedef ve duruma göre Plus, Premium ya da Pro'yu önerir; özellik listesi okumaz."
    },
    "B11": {
        "dim": "K2",
        "name": "Özelliği müşteri faydasına çevirme",
        "def": "Her özelliği müşterinin söylediği bir ihtiyaca bağlar."
    },
    "B12": {
        "dim": "K2",
        "name": "Rexven farkını ve deneyimi aktarma",
        "def": "Tedarik ağı, eğitim ve destek farkını gerçek örneklerle somut anlatır."
    },
    "B13": {
        "dim": "K2",
        "name": "Yatırımın getirisi argümanı",
        "def": "Paket ücretini kazanç potansiyeli, zaman tasarrufu ve pahalı hatalardan kaçınma üzerinden ikna edici anlatır; kazanç garantisi vermez."
    },
    "B14": {
        "dim": "K2",
        "name": "Güvenilirlik ve kanıt",
        "def": "Rexven'in güvenilirliğini gerçek satıcı sonuçları ve referanslarla destekler."
    },
    "B15": {
        "dim": "K2",
        "name": "Online toplantı ya da mentör görüşmesi teklif etme",
        "def": "Bir sonraki görüşmeyi (online toplantı, Pro adayında mentör görüşmesi) önerir ve müşterinin hedefine göre kurgular."
    },
    "B16": {
        "dim": "K2",
        "name": "Somut ve görsel sunum",
        "def": "Toplantıda paneli, örnek ürünleri ve gerçek satıcı örneklerini gösterir; telefonda canlı ve somut tasvir eder."
    },
    "B17": {
        "dim": "K2",
        "name": "Rakibi kötülemeden ayrışma",
        "def": "Rakiple kıyasta saygılı kalır, farkı müşteri faydası üzerinden kurar."
    },
    "B18": {
        "dim": "K3",
        "name": "İtirazı dinleme ve kabul etme",
        "def": "Sözü kesmeden dinler, itirazın haklı yanını kabul eder."
    },
    "B19": {
        "dim": "K3",
        "name": "Gerçek itirazı izole etme",
        "def": "Bahane ile gerçek engeli ayırır: başka bir engel var mı diye sorar."
    },
    "B20": {
        "dim": "K3",
        "name": "Fiyat itirazında önce değer",
        "def": "Fiyat itirazında indirime koşmaz, önce değeri yeniden kurar."
    },
    "B21": {
        "dim": "K3",
        "name": "Mevcut sistem ve geçiş itirazı",
        "def": "Mevcut tedarikçisinden ya da önceki yatırımından vazgeçme kaygısını şeffaf gerekçelerle yönetir, geçiş riskini küçültecek adım önerir."
    },
    "B22": {
        "dim": "K3",
        "name": "Erteleme ve kampanya bekleme itirazı",
        "def": "Beklemenin maliyetini ve riskini dürüstçe gösterir."
    },
    "B23": {
        "dim": "K3",
        "name": "Finansman ve ödeme itirazı",
        "def": "Ödeme engelinde alternatif yollar arar ve sunar."
    },
    "B24": {
        "dim": "K3",
        "name": "Karşılıksız taviz vermeme",
        "def": "Taviz vermeden önce karar taahhüdü ister."
    },
    "B25": {
        "dim": "K3",
        "name": "Rakip teklif karşısında sakin kalma",
        "def": "Rakip teklif duyunca panik ve fiyat yarışına girmez."
    },
    "B26": {
        "dim": "K4",
        "name": "Satın alma sinyallerini fark etme",
        "def": "Paket farkı, ödeme, başlangıç tarihi ve ilk adımlar soruları gibi sinyalleri yakalar."
    },
    "B27": {
        "dim": "K4",
        "name": "Deneme kapanışı yapma",
        "def": "Yol ortasında küçük onaylar alır."
    },
    "B28": {
        "dim": "K4",
        "name": "“Düşüneyim”i sorgulama",
        "def": "Neyi düşüneceğini nazikçe sorar, düşünülecek konuyu masada çözer."
    },
    "B29": {
        "dim": "K4",
        "name": "Net sonraki adım önerme",
        "def": "Görüşmeyi tarih ve saatli somut bir adımla bitirir."
    },
    "B30": {
        "dim": "K4",
        "name": "Diğer karar vericiyi sürece katma",
        "def": "Eşi, aileyi ya da iş ortağını online toplantıya veya karar görüşmesine davet eder."
    },
    "B31": {
        "dim": "K4",
        "name": "Kapanış sorusunu açıkça sorma",
        "def": "Uygun anda siparişi ya da kararı açıkça ister."
    },
    "B32": {
        "dim": "K4",
        "name": "Dürüst aciliyet yaratma",
        "def": "Stok, teslim ya da fiyat dönemi gibi gerçek nedenlerle aciliyet kurar."
    },
    "B33": {
        "dim": "K4",
        "name": "Teklifi net ve süreli sunma",
        "def": "Teklifi yazılı, anlaşılır ve geçerlilik süresiyle sunar."
    },
    "B34": {
        "dim": "K5",
        "name": "Takip için zaman taahhüdü alma",
        "def": "Bir sonraki temas için gün ve saati müşteriyle birlikte belirler."
    },
    "B35": {
        "dim": "K5",
        "name": "Takibi önceki görüşmeye bağlama",
        "def": "Takip aramasına önceki görüşmeden somut bir referansla başlar."
    },
    "B36": {
        "dim": "K5",
        "name": "Takipte yeni değer getirme",
        "def": "“Ne düşündünüz?” yerine yeni bilgi, stok ya da çözüm getirir."
    },
    "B37": {
        "dim": "K5",
        "name": "Ulaşılamayan müşteriye çok kanallı temas",
        "def": "Arama, mesaj ve e-postayı planlı biçimde kullanır."
    },
    "B38": {
        "dim": "K5",
        "name": "Kayıp nedenini öğrenme",
        "def": "Müşteri vazgeçse bile gerçek nedeni nazikçe öğrenir."
    },
    "B39": {
        "dim": "K5",
        "name": "CRM'e eyleme dönük not",
        "def": "Notu ihtiyaç, engel, karar verici ve sonraki adımla yazar."
    },
    "B40": {
        "dim": "K5",
        "name": "Kaybetse de ilişkiyi koruma",
        "def": "Kapıyı açık bırakan, saygılı bir kapanış yapar."
    },
    "B41": {
        "dim": "K5",
        "name": "Profesyonel dil, ton ve ritim",
        "def": "Kısa, net, saygılı konuşur; monolog yapmaz, müşteriyi konuşturur."
    }
}

CRM_CRITERIA: dict[str, dict[str, str]] = {
    "C1": {
        "name": "Durum doğruluğu",
        "def": "Seçilen durum ve sonraki adım görüşmede gerçekten olanla birebir uyumlu."
    },
    "C2": {
        "name": "İhtiyaç ve profil",
        "def": "İlgilendiği paket, hedefi, mevcut e-ticaret durumu, bütçe ve ayırabileceği zaman bilgisi kayıtta."
    },
    "C3": {
        "name": "Gerçek engel",
        "def": "Yüzeysel “düşünecek” yerine müşterinin asıl engeli ya da itirazı yazılmış."
    },
    "C4": {
        "name": "Karar süreci",
        "def": "Karar verici, karar zamanı ve karara etki eden kişiler kayıtta."
    },
    "C5": {
        "name": "Sonraki adım ve tarih",
        "def": "Müşteriyle mutabık kalınmış, tarih ve saatli somut bir adım var."
    },
    "C6": {
        "name": "Nesnellik ve devralınabilirlik",
        "def": "Gözlem diliyle yazılmış; başka bir danışman okuyup kaldığı yerden devam edebilir. Kayıp nedeni doğru kategoride, gereksiz yere “Diğer” yok. Özel hayata dair gereksiz bilgi yok."
    }
}

DIM_KEYS: tuple[str, ...] = tuple(DIMS)
CODES: tuple[str, ...] = tuple(BEHAVIORS)
CRM_KEYS: tuple[str, ...] = tuple(CRM_CRITERIA)


def rubric_text() -> str:
    """Analiz talimatına girecek davranış ve CRM ölçütü listesi."""
    lines: list[str] = []
    for key, name in DIMS.items():
        lines.append(f"{key} · {name}")
        for code, b in BEHAVIORS.items():
            if b["dim"] == key:
                lines.append(f"  {code} {b['name']}: {b['def']}")
    lines.append("CRM notu ölçütleri")
    for code, c in CRM_CRITERIA.items():
        lines.append(f"  {code} {c['name']}: {c['def']}")
    return "\n".join(lines)
