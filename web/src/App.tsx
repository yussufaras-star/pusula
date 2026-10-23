import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Logo } from "./components/Logo";
import { FinancePage, type FinanceTab } from "./finance/FinancePage";
import { CeoSummaryPage } from "./pages/CeoSummary";
import { BoardPage } from "./pages/Board";
import { ComparePage } from "./pages/Compare";
import { ConversationsPage } from "./pages/Conversations";
import { LivePage } from "./pages/Live";
import { LoginPage } from "./pages/Login";
import { ReportsPage } from "./pages/Reports";
import type { Me } from "./types";

// Pusula iki bölümdür: satış operasyonu ve finans operasyonu.
// Finans yalnız me.finance true olan yöneticide görünür; sunucu da ayrıca korur.
type Area = "satis" | "finans";

type SalesRoute = "canli" | "karsilastir" | "pano" | "raporlar" | "gorusmeler" | "ceo";
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

  const loadMe = useCallback(() => {
    api<Me>("/api/me")
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  useEffect(loadMe, [loadMe]);
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
        ) : route === "finans/ozet" ? (
          <CeoSummaryPage
            endpoint="/api/finance-summary"
            title="Finans yönetici özeti"
            sub="Her sabah son iş gününden beri nakit, giriş-çıkış ve uyarılar Cliq'te #headquarters kanalına gider · tutarlar USD"
          />
        ) : (
          // Tek bileşen: sekme değişince şirket ve tarih seçimi korunur
          <FinancePage tab={route.slice("finans/".length) as FinanceTab} />
        )}
      </main>
    </div>
  );
}
