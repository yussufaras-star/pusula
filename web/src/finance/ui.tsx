import { useEffect, type ReactNode } from "react";

import { usePolling } from "../api";
import { COMPANY_SHORT, companyColor } from "./format";
import type { CompanyId, Meta, Scope } from "./types";

export interface TabProps {
  scope: Scope;
  start: string;
  end: string;
  meta: Meta;
  tick: number; // senkron / yükleme sonrası artar, sekme yeniden çeker
  reload: () => void;
}

/** Sekme verisi: tick değişince yeniden çekilir. */
export function useFin<T>(path: string | null, tick: number) {
  const sep = path?.includes("?") ? "&" : "?";
  return usePolling<T>(path ? `${path}${sep}_=${tick}` : null, 0);
}

export function CompanyTag({ id }: { id: CompanyId | null }) {
  if (!id) return <span className="co-tag group">Grup</span>;
  return (
    <span className="co-tag">
      <span className="sw" style={{ background: companyColor(id) }} />
      {COMPANY_SHORT[id]}
    </span>
  );
}

export function FxNote({ missing }: { missing: string[] }) {
  if (!missing.length) return null;
  return (
    <div className="banner">
      Kur verisi yetersiz: {missing.join(", ")}. Bu para birimindeki tutarlar toplamlara girmedi.
    </div>
  );
}

export function Loading({ error }: { error?: { message: string } | null }) {
  if (error) return <div className="banner error">Veri alınamadı: {error.message}</div>;
  return (
    <div className="tiles">
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="skeleton" />
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`card modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="card-head">
          <h2>{title}</h2>
          <button className="btn link-x" onClick={onClose} aria-label="Kapat">
            ×
          </button>
        </div>
        <div className="card-body">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="f-field">
      <span>{label}</span>
      {children}
      {hint ? <small className="muted">{hint}</small> : null}
    </label>
  );
}

export const scopeIds = (scope: Scope, meta: Meta): CompanyId[] =>
  scope === "all" ? [...meta.companies].sort((a, b) => a.sort - b.sort).map((c) => c.id) : [scope];

export const categoryName = (meta: Meta, id: string | null): string =>
  id ? (meta.categories.find((c) => c.id === id)?.name ?? id) : "Kategorisiz";
