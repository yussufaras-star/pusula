import { StatTile } from "../components/StatTile";
import { BalanceChart } from "./charts";
import { COMPANY_SHORT, companyColor, day, money, short, signed, usd, when } from "./format";
import type { Overview } from "./types";
import { FxNote, Loading, scopeIds, useFin, type TabProps } from "./ui";

export function OverviewTab({ scope, start, meta, tick }: TabProps) {
  const { data, error } = useFin<Overview>(`/api/finance/overview?company=${scope}&start=${start}`, tick);
  if (!data) return <Loading error={error} />;
  const ids = scopeIds(scope, meta);
  const a = data.alerts;
  const noAccounts = data.companies.every((c) => c.accounts === 0);

  if (noAccounts) {
    return (
      <section className="card">
        <div className="card-body empty">
          Henüz hesap yok. <a href="#/finans/hesaplar">Hesaplar</a> sekmesinden banka ve fintech hesaplarını ekle.
        </div>
      </section>
    );
  }

  const alerts: { tone: "critical" | "warning"; title: string; text: string; href?: string }[] = [];
  if (a.cash_gap) {
    alerts.push({
      tone: "critical",
      title: "Nakit açığı riski",
      text: `Projeksiyona göre ${day(a.cash_gap.week)} haftasında kasa ${usd(a.cash_gap.close)} seviyesine iniyor.`,
      href: "#/finans/projeksiyon",
    });
  }
  if (a.overdue.count) {
    alerts.push({
      tone: "warning",
      title: `${a.overdue.count} planlı kalemin vadesi geçti`,
      text: `Toplam ${usd(a.overdue.usd)}. Ödendiyse kapat, ertelendiyse vadeyi güncelle.`,
      href: "#/finans/projeksiyon",
    });
  }
  for (const s of a.sync_errors) {
    alerts.push({ tone: "warning", title: `${s.name} senkronize olmadı`, text: s.error, href: "#/finans/hesaplar" });
  }
  if (a.stale.length) {
    alerts.push({
      tone: "warning",
      title: "Güncel olmayan hesaplar",
      text: a.stale.map((s) => `${s.name} (son: ${when(s.last_synced_at)})`).join(", "),
      href: "#/finans/hesaplar",
    });
  }
  for (const w of a.warnings) {
    alerts.push({ tone: "warning", title: w.name, text: w.warning });
  }

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <div className="fin-hero">
        <section className="card fin-hero-main">
          <div className="label">{scope === "all" ? "Konsolide nakit" : `${COMPANY_SHORT[scope]} nakit`}</div>
          <div className="fin-hero-value">{usd(data.cash_usd)}</div>
          <div className="muted">
            Dönem başına göre {signed(data.change_usd)} · aktif hesapların güncel bakiyesi, son kurla
          </div>
        </section>
        <div className="fin-cards">
          {data.companies.map((c) => (
            <section key={c.id} className="card fin-co" style={{ borderTopColor: companyColor(c.id) }}>
              <div className="label">{c.name}</div>
              <div className="fin-co-value">{usd(c.cash_usd)}</div>
              <div className="muted">
                {c.currency !== "USD" ? `${money(c.cash_local, c.currency)} · ` : ""}
                {c.accounts} hesap
              </div>
            </section>
          ))}
        </div>
      </div>

      <div className="tiles fin-tiles">
        <StatTile label="Dönem net akışı" value={short(data.net_flow_usd)} expected="transferler hariç" tone={data.net_flow_usd >= 0 ? "good" : "critical"} badge={data.net_flow_usd >= 0 ? "pozitif" : "negatif"} />
        <StatTile
          label="13 haftada en düşük kasa"
          value={data.lowest ? short(data.lowest.close) : "—"}
          expected={data.lowest ? `${day(data.lowest.week)} haftası` : "veri yetersiz"}
          tone={data.lowest && data.lowest.close < 0 ? "critical" : "neutral"}
          badge={data.lowest && data.lowest.close < 0 ? "açık var" : undefined}
        />
        <StatTile label="Aksiyon bekleyen karar" value={data.open_actions} expected="durum: aksiyon" />
        <StatTile label="Senkron sorunu" value={a.sync_errors.length + a.stale.length} expected="hata veya eski veri" tone={a.sync_errors.length ? "warning" : "neutral"} badge={a.sync_errors.length ? "bak" : undefined} />
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Nakit seyri</h2>
            <span className="hint">haftalık, USD · o günün kuruyla</span>
          </div>
          <div className="card-body">
            <BalanceChart history={data.history} ids={ids} />
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Uyarılar</h2>
            <span className="hint">{alerts.length ? `${alerts.length} madde` : "yok"}</span>
          </div>
          <div className="card-body">
            {alerts.length ? (
              <ul className="alerts">
                {alerts.map((al, i) => (
                  <li key={i} className={al.tone}>
                    <span className="ico" aria-hidden>
                      {al.tone === "critical" ? "!" : "•"}
                    </span>
                    <div>
                      <div className="a-title">{al.title}</div>
                      <div className="a-text">
                        {al.text} {al.href ? <a href={al.href}>Git</a> : null}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="empty">Açık uyarı yok.</div>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
