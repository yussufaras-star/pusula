import { int, minutes, num, pace } from "../format";
import type { LivePayload } from "../types";

type Level = "critical" | "warning" | "good";

interface Item {
  level: Level;
  title: string;
  text: string;
}

const PACE_MIN_EXPECTED = 5;

/** Kokpit verisinden deterministik dikkat listesi. Dürtme motorunun önizlemesi. */
export function attentionItems(data: LivePayload): Item[] {
  const items: Item[] = [];
  if (!data.is_today || !data.in_shift) return items;
  for (const r of data.reps) {
    if (r.on_leave) {
      const parts = [
        r.today.arama ? `${int(r.today.arama)} arama` : "",
        r.today.whatsapp ? `${int(r.today.whatsapp)} WhatsApp` : "",
        r.today.katildi ? `${int(r.today.katildi)} toplantı` : "",
      ].filter(Boolean);
      if (parts.length) {
        items.push({
          level: "warning",
          title: `${r.name} izinli ama bugün faaliyet var`,
          text: `${parts.join(", ")} kaydı; doluluğa sayılmadı. Hat veya hesap başkası tarafından kullanılmış olabilir.`,
        });
      }
      continue;
    }
    if (r.status === "hareketsiz") {
      items.push({
        level: "critical",
        title: `${r.name} ${minutes(r.idle_min)} süredir hareketsiz`,
        text: "Son arama, toplantı veya WhatsApp kaydından bu yana faaliyet görünmüyor.",
      });
    } else if (r.status === "baslamadi") {
      items.push({
        level: "warning",
        title: `${r.name} henüz başlamadı`,
        text: "Bugün kayıtlı arama, toplantı veya mesaj yok.",
      });
    }
    const ratio = pace(r.today.arama, r.expected.arama);
    if (ratio !== null && r.expected.arama >= PACE_MIN_EXPECTED && ratio < 0.8) {
      items.push({
        level: ratio < 0.6 ? "critical" : "warning",
        title: `${r.name} aramada geride`,
        text: `${int(r.today.arama)} arama; bu saate kadar beklenen ${num(r.expected.arama)} (%${Math.round(ratio * 100)}).`,
      });
    }
  }
  const missing = data.team.today.sonuc_girilmedi;
  if (missing > 0) {
    items.push({
      level: "warning",
      title: `${int(missing)} toplantının sonucu girilmemiş`,
      text: "Toplantı sonucu CRM'e kaydedilmeli (katıldı / katılmadı).",
    });
  }
  const order: Record<Level, number> = { critical: 0, warning: 1, good: 2 };
  return items.sort((a, b) => order[a.level] - order[b.level]);
}

export function Attention({ data }: { data: LivePayload }) {
  const items = attentionItems(data);
  if (!data.is_today) {
    return <div className="empty">Geçmiş gün görüntüleniyor; canlı uyarı yok.</div>;
  }
  if (!data.in_shift) {
    return <div className="empty">Mesai dışında.</div>;
  }
  if (!items.length) {
    return (
      <ul className="alerts">
        <li className="good">
          <span className="ico" aria-hidden>
            ✓
          </span>
          <div>
            <div className="a-title">Her şey yolunda</div>
            <div className="a-text">Hareketsiz veya tempo gerisinde temsilci yok.</div>
          </div>
        </li>
      </ul>
    );
  }
  return (
    <ul className="alerts">
      {items.map((item, i) => (
        <li key={i} className={item.level}>
          <span className="ico" aria-hidden>
            {item.level === "critical" ? "!" : "•"}
          </span>
          <div>
            <div className="a-title">{item.title}</div>
            <div className="a-text">{item.text}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}
