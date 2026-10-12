import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* gövde JSON değil */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export interface Polled<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  fetchedAt: Date | null;
  refresh: () => void;
}

/** Periyodik yenileme. Sekme gizliyken durur, görünür olunca hemen yeniler. */
export function usePolling<T>(path: string | null, intervalMs: number): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const seq = useRef(0);

  const load = useCallback(() => {
    if (!path) return;
    const id = ++seq.current;
    setLoading(true);
    api<T>(path)
      .then((body) => {
        if (id !== seq.current) return;
        setData(body);
        setError(null);
        setFetchedAt(new Date());
      })
      .catch((err: unknown) => {
        if (id !== seq.current) return;
        setError(err instanceof ApiError ? err : new ApiError(0, String(err)));
      })
      .finally(() => {
        if (id === seq.current) setLoading(false);
      });
  }, [path]);

  useEffect(() => {
    setData(null);
    load();
    if (intervalMs <= 0) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, intervalMs);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load, intervalMs]);

  return { data, error, loading, fetchedAt, refresh: load };
}
