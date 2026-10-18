import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Logo } from "./components/Logo";
import { FinancePage, type FinanceTab } from "./finance/FinancePage";
import { ComparePage } from "./pages/Compare";
import { LivePage } from "./pages/Live";
import { LoginPage } from "./pages/Login";
import { ReportsPage } from "./pages/Reports";
import type { Me } from "./types";

// Pusula iki bölümdür: satış operasyonu ve finans operasyonu.
// Finans yalnız me.finance true olan yöneticide görünür; sunucu da ayrıca korur.
type Area = "satis" | "finans";

type SalesRoute = "canli" | "karsilastir" | "pano" | "raporlar";
type FinanceRoute =
  | "finans/genel"
  | "finans/akis"
  | "finans/projeksiyon"
  | "finans/sirketler-arasi"
  | "finans/bilanco"
  | "finans/vergi"
  | "finans/kararlar"
  | "finans/hesaplar";
type Route = SalesRoute | FinanceRoute;

interface NavItem {
  route: Route;
  label: string;
  soon?: boolean;
}

const NAV: Record<Area, NavItem[]> = {
  satis: [
    { route: "canli", label: "Canlı" },
    { route: "karsilastir", label: "Karşılaştır" },
    { route: "pano", label: "Ekip Panosu", soon: true },
    { route: "raporlar", label: "Raporlar" },
  ],
  finans: [
    { route: "finans/genel", label: "Genel bakış" },
    { route: "finans/akis", label: "Nakit akışı" },
    { route: "finans/projeksiyon", label: "Projeksiyon" },
    { route: "finans/sirketler-arasi", label: "Şirketler arası" },
    { route: "finans/bilanco", label: "Haftalık bilanço" },
    { route: "finans/vergi", label: "Cüzdan ve vergi" },
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
          {NAV[area].map((n) => (
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
        ) : area === "finans" ? (
          // Tek bileşen: sekme değişince şirket ve tarih seçimi korunur
          <FinancePage tab={route.slice("finans/".length) as FinanceTab} />
        ) : (
          <Soon route={route as SoonRoute} />
        )}
      </main>
    </div>
  );
}

type SoonRoute = Exclude<SalesRoute, "canli" | "karsilastir" | "raporlar">;

const SOON_TEXT: Record<SoonRoute, [string, string]> = {
  pano: ["Ekip Panosu", "Ekip hedefine canlı ilerleme, kişisel rekorlar ve ofis TV'si için büyük ekran."],
};

function Soon({ route }: { route: SoonRoute }) {
  const [title, text] = SOON_TEXT[route];
  return (
    <div className="card placeholder-page">
      <h2>{title}</h2>
      <p>{text}</p>
      <p className="muted">Sonraki aşamada geliyor.</p>
    </div>
  );
}
