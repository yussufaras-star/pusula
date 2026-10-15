import { useState } from "react";
import { toPng } from "html-to-image";

import { api } from "../api";

/** Görsele girmeyenler. scripts/card_snapshots.py aynı seçiciyi kullanır. */
const SKIP = "[data-share-skip], .link-btn, .cm-orphans";

/** Kartın görselini ekip Cliq kanalına gönderir. Butonun içinde bulunduğu
 * [data-share] kartını çeker; panel eylemleri (bağlantı butonları vb.) görselde yer almaz. */
export function ShareButton({ kart, baslik }: { kart: string; baslik: string }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState("");

  async function share(button: HTMLButtonElement) {
    const card = button.closest<HTMLElement>("[data-share]");
    if (!card) return;
    if (!window.confirm(`"${baslik}" kartının görseli ekip Cliq kanalına gönderilsin mi?`)) return;
    setState("busy");
    setError("");
    try {
      const background = getComputedStyle(document.body).backgroundColor;
      const options = {
        backgroundColor: background,
        filter: (node: HTMLElement) => !(node instanceof HTMLElement && node.matches(SKIP)),
      };
      let png = await toPng(card, { ...options, pixelRatio: 2 });
      // Base64 ~4/3 büyür; sunucu sınırı 3 MB.
      if (png.length > 3.9e6) png = await toPng(card, { ...options, pixelRatio: 1 });
      await api("/api/share-card", { method: "POST", body: JSON.stringify({ kart, baslik, png }) });
      setState("done");
      window.setTimeout(() => setState("idle"), 4000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState("error");
    }
  }

  return (
    <button
      className="btn share-btn"
      data-share-skip
      disabled={state === "busy"}
      title={state === "error" ? error : "Kartın görselini ekip Cliq kanalına gönder"}
      onClick={(e) => share(e.currentTarget)}
    >
      {state === "busy" ? "Gönderiliyor…" : state === "done" ? "Cliq'e gönderildi ✓" : state === "error" ? "Gönderilemedi — tekrar dene" : "Cliq'e gönder"}
    </button>
  );
}
