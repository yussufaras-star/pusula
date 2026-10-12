import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Logo } from "./components/Logo";
import { LivePage } from "./pages/Live";
import { LoginPage } from "./pages/Login";
import type { Me } from "./types";

type Route = "canli" | "karsilastir" | "arena" | "raporlar";

const NAV: { route: Route; label: string; soon?: boolean; adminOnly?: boolean }[] = [
  { route: "canli", label: "Canlı" },
  { route: "karsilastir", label: "Karşılaştır", soon: true },
  { route: "arena", label: "Arena", soon: true },
  { route: "raporlar", label: "Raporlar", soon: true },
];

function readRoute(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  return (NAV.find((n) => n.route === hash)?.route ?? "canli") as Route;
}

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [route, setRoute] = useState<Route>(readRoute);

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

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <Logo /> Pusula
        </div>
        <nav className="nav">
          {NAV.filter((n) => !n.adminOnly || me.role === "admin").map((n) => (
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
        {route === "canli" ? <LivePage me={me} /> : <Soon route={route} />}
      </main>
    </div>
  );
}

const SOON_TEXT: Record<Exclude<Route, "canli">, [string, string]> = {
  karsilastir: [
    "Karşılaştırma",
    "İki dönemi veya iki temsilciyi yan yana, araba karşılaştırır gibi: bugün vs geçen salı, bu hafta vs geçen hafta, Ayşe vs Mehmet.",
  ],
  arena: ["Arena", "Canlı liderlik tablosu ve TV modu: günün yarışı, hedef ilerlemesi, rozetler."],
  raporlar: ["Raporlar", "Huni, dönüşüm, satış döngüsü, ciro ve haftalık seriler."],
};

function Soon({ route }: { route: Exclude<Route, "canli"> }) {
  const [title, text] = SOON_TEXT[route];
  return (
    <div className="card placeholder-page">
      <h2>{title}</h2>
      <p>{text}</p>
      <p className="muted">Sonraki aşamada geliyor.</p>
    </div>
  );
}
