import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Logo } from "./components/Logo";
import { PasswordModal } from "./components/PasswordModal";
import { FinancePage, type FinanceTab } from "./finance/FinancePage";
import { CeoSummaryPage } from "./pages/CeoSummary";
import { BoardPage } from "./pages/Board";
import { ComparePage } from "./pages/Compare";
import { ConversationsPage } from "./pages/Conversations";
import { LivePage } from "./pages/Live";
import { LoginPage } from "./pages/Login";
import { ReportsPage } from "./pages/Reports";
import { UsersPage } from "./pages/Users";
import type { Me } from "./types";

// Pusula iki bölümdür: satış operasyonu ve finans operasyonu.
// Finans yalnız me.finance true olan yöneticide görünür; sunucu da ayrıca korur.
type Area = "satis" | "finans";

type SalesRoute = "canli" | "karsilastir" | "pano" | "raporlar" | "gorusmeler" | "ceo" | "kullanicilar";
type FinanceRoute =
  | "finans/genel"
  | "finans/akis"
  | "finans/duzenli"
  | "finans/projeksiyon"
  | "finans/sirketler-arasi"
  | "finans/bilanco"
  | "finans/vergi"
  | "finans/kararlar"
  | "finans/hesaplar"
  | "finans/ozet";
type Route = SalesRoute | FinanceRoute;

interface NavItem {
  route: Route;
  label: string;
  soon?: boolean;
  admin?: boolean;
}

const NAV: Record<Area, NavItem[]> = {
  satis: [
    { route: "canli", label: "Canlı" },
    { route: "karsilastir", label: "Karşılaştır" },
    { route: "pano", label: "OKRs" },
    { route: "raporlar", label: "Raporlar" },
    { route: "gorusmeler", label: "Görüşme Analizleri" },
    { route: "ceo", label: "Yönetici özeti", admin: true },
    { route: "kullanicilar", label: "Kullanıcılar", admin: true },
  ],
  finans: [
    { route: "finans/genel", label: "Genel" },
    { route: "finans/ozet", label: "Yönetici özeti" },
    { route: "finans/akis", label: "Nakit akışı" },
    { route: "finans/duzenli", label: "Düzenli" },
    { route: "finans/projeksiyon", label: "Projeksiyon" },
    { route: "finans/sirketler-arasi", label: "Şirketler arası" },
    { route: "finans/bilanco", label: "Bilanço" },
    { route: "finans/vergi", label: "Vergi" },
    { route: "finans/kararlar", label: "Kararlar" },
    { route: "finans/hesaplar", label: "Hesaplar" },
  ],
};

const AREA_LABEL: Record<Area, string> = {
  satis: "Satış operasyonu",
  finans: "Finans operasyonu",
};

function areaOf(route: Route): Area {
  return route.startsWith("finans/") ? "finans" : "satis";
}

function readRoute(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash === "finans") return "finans/genel";
  const all = [...NAV.satis, ...NAV.finans];
  return all.find((n) => n.route === hash)?.route ?? "canli";
}

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [hashRoute, setRoute] = useState<Route>(readRoute);
  const [pwOpen, setPwOpen] = useState(false);

  const loadMe = useCallback(() => {
    api<Me>("/api/me")
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  useEffect(loadMe, [loadMe]);
  // Kullanım ölçümü: sekme önde ve kişi son 5 dk içinde fare / klavye kullandıysa dakikada bir sinyal.
  const signedIn = me ? me.email : null;
  const viewing = Boolean(me?.viewer);
  useEffect(() => {
    if (!signedIn || viewing) return;
    let last = Date.now();
    const mark = () => {
      last = Date.now();
    };
    const events = ["mousemove", "keydown", "scroll", "click", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, mark, { passive: true }));
    const id = window.setInterval(() => {
      if (document.visibilityState !== "visible" || Date.now() - last > 5 * 60_000) return;
      api("/api/activity", { method: "POST", body: JSON.stringify({ page: readRoute() }) }).catch(() => undefined);
    }, 60_000);
    return () => {
      window.clearInterval(id);
      events.forEach((e) => window.removeEventListener(e, mark));
    };
  }, [signedIn, viewing]);
  // Yöneticinin verdiği ilk şifreyle giren temsilciden kendi şifresini belirlemesi istenir.
  useEffect(() => {
    if (me?.must_change) setPwOpen(true);
  }, [me?.must_change]);
  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (me === undefined) return null;
  if (me === null) return <LoginPage onDone={loadMe} />;

  async function logout() {
    await api("/api/logout", { method: "POST" }).catch(() => undefined);
    setMe(null);
  }

  const canFinance = me.finance === true;
  // Yetkisiz kullanıcı finans adresini elle yazarsa satışa düşer.
  const route: Route = areaOf(hashRoute) === "finans" && !canFinance ? "canli" : hashRoute;
  const area = areaOf(route);

  return (
    <div className="shell">
      {me.viewer ? (
        <div className="view-banner" role="status">
          <span>
            <strong>{me.full_name ?? me.email}</strong> olarak görüntülüyorsunuz · temsilcinin gördüğü panel, değişiklik yapılamaz
          </span>
          <button
            className="btn"
            onClick={() =>
              api("/api/view-as/stop", { method: "POST" })
                .then(() => {
                  window.location.hash = "#/kullanicilar";
                  window.location.reload();
                })
                .catch(() => undefined)
            }
          >
            Yönetici ekranına dön
          </button>
        </div>
      ) : null}
      <header className="topbar">
        <div className="brand">
          <Logo /> Pusula
        </div>
        {canFinance ? (
          <div className="area-switch" role="tablist" aria-label="Bölüm">
            {(Object.keys(AREA_LABEL) as Area[]).map((a) => (
              <a
                key={a}
                role="tab"
                href={`#/${NAV[a][0].route}`}
                aria-selected={area === a}
              >
                {AREA_LABEL[a]}
              </a>
            ))}
          </div>
        ) : null}
        <nav className="nav">
          {NAV[area].filter((n) => !n.admin || me.role === "admin").map((n) => (
            <a key={n.route} href={`#/${n.route}`} aria-current={route === n.route ? "page" : undefined}>
              {n.label}
              {n.soon ? <span className="soon">yakında</span> : null}
            </a>
          ))}
        </nav>
        <div className="spacer" />
        <div className="user">
          <span>{me.full_name ?? me.email}</span>
          {me.own_password ? (
            <button className="btn" onClick={() => setPwOpen(true)}>
              Şifremi değiştir
            </button>
          ) : null}
          <button className="btn" onClick={logout}>
            Çıkış
          </button>
        </div>
      </header>
      <main className="main">
        {route === "canli" ? (
          <LivePage me={me} />
        ) : route === "karsilastir" ? (
          <ComparePage me={me} />
        ) : route === "raporlar" ? (
          <ReportsPage me={me} />
        ) : route === "gorusmeler" ? (
          <ConversationsPage me={me} />
        ) : route === "pano" ? (
          <BoardPage me={me} />
        ) : route === "ceo" ? (
          <CeoSummaryPage />
        ) : route === "kullanicilar" ? (
          me.role === "admin" ? <UsersPage /> : <LivePage me={me} />
        ) : route === "finans/ozet" ? (
          <CeoSummaryPage
            endpoint="/api/finance-summary"
            title="Finans operasyonu yönetici özeti"
            sub="Her sabah son iş gününden beri nakit, giriş-çıkış ve uyarılar Cliq'te #headquarters kanalına gider · tutarlar USD"
          />
        ) : (
          // Tek bileşen: sekme değişince şirket ve tarih seçimi korunur
          <FinancePage tab={route.slice("finans/".length) as FinanceTab} />
        )}
      </main>
      {pwOpen ? <PasswordModal first={me.must_change === true} onClose={() => setPwOpen(false)} onDone={loadMe} /> : null}
    </div>
  );
}
