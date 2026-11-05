"""SONAR-41 tutarlılık havuzu: geçmiş görüşmeleri tutarlılık analizi için kuyruğa alır.

Satışa dönmüş görüşmelerin hepsi ("satis") ve satışa dönmemiş görüşmelerden
aylara orantılı rastgele örnek ("genel") analiz edilir. Satışlı görüşme:
görüşmeden sonraki WINDOW_DAYS gün içinde aynı kişiye kazanılmış satış;
görüşmeden önce satın almış müşterinin görüşmeleri alınmaz. Örnek kararlı
(event id karması); aynı komut tekrar çalışınca aynı görüşmeleri seçer.

Her kayıt meta.havuz (satis/genel) ve meta.agirlik (genel: o ayın satışsız
görüşmesi / örneklenen) taşır; tutarlılık analizi satış oranını bu ağırlıkla
hesaplar. Haftalık akışta zaten seçilmiş (kota secildi / talep) görüşme
yeniden ücretlendirilmez, yalnız etiketlenir. Diğerleri meta.havuz_disi ile
haftalık panodan, kotadan, puan tablosundan ve trendden ayrı tutulur; gece
işi onları haftanın kayıtlarından sonra, en eskiden başlayarak işler
(Verimor kayıtları ~3 ay saklar).

Yalnız satış temsilcilerinin, en az MIN_SEC süren, kaydı olan çağrıları;
süren hafta alınmaz (haftalık kotası henüz açık).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.conversations import QUOTA_OUT, QUOTA_WAIT, _insert_items, _ready, _stable, call_row, week_start_of
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

WINDOW_DAYS = 14
MIN_SEC = 60


def sample(rows: list[dict[str, Any]], size: int) -> list[tuple[dict[str, Any], str, float]]:
    """Saf: (satır, havuz, ağırlık). Satışlıların hepsi; satışsızlardan aylara orantılı,
    kararlı örnek. rows: id, ay, satis."""
    out: list[tuple[dict[str, Any], str, float]] = [(r, "satis", 1.0) for r in rows if r["satis"]]
    rest = [r for r in rows if not r["satis"]]
    by_month: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for r in rest:
        by_month[r["ay"]].append(r)
    total = len(rest)
    for month, items in sorted(by_month.items()):
        n = min(len(items), round(size * len(items) / total)) if total else 0
        if not n:
            continue
        chosen = sorted(items, key=lambda r: _stable(str(r["id"])))[:n]
        out.extend((r, "genel", round(len(items) / n, 3)) for r in chosen)
    return out


def candidates(conn: Any, since: date, until: date) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        WITH calls AS (
            SELECT e.id, e.rep_id, e.thread_id, e.direction, e.occurred_at, e.meta,
                   (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::date AS gun,
                   e.meta->'what_id'->>'id' AS lead_id
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id AND r.category = 'sales'
            WHERE e.org_id = %s AND e.channel = 'call' AND e.meta->>'voice_recording_url' IS NOT NULL
              AND (e.meta->>'duration_sec')::float >= %s
              AND e.occurred_at >= %s AND e.occurred_at < %s
        ),
        won AS (
            SELECT DISTINCT c.id,
                   coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date) - c.gun AS fark
            FROM calls c
            JOIN contacts ct ON ct.org_id = %s
             AND ((c.lead_id IS NOT NULL AND ct.lead_id = c.lead_id) OR (c.thread_id IS NOT NULL AND ct.thread_id = c.thread_id))
            JOIN deal_packages d ON d.org_id = ct.org_id AND d.contact_id = ct.contact_id AND d.stage = %s
        )
        SELECT c.id, c.rep_id, c.thread_id, c.direction, c.occurred_at, c.meta,
               date_trunc('month', c.gun)::date AS ay,
               EXISTS (SELECT 1 FROM won w WHERE w.id = c.id AND w.fark BETWEEN 0 AND %s) AS satis
        FROM calls c
        WHERE NOT EXISTS (SELECT 1 FROM won w WHERE w.id = c.id AND w.fark < 0)
        ORDER BY c.occurred_at
        """,
        (get_org_id(), MIN_SEC, since, until, get_org_id(), WON_STAGE, WINDOW_DAYS),
    )
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def enqueue(since: date, today: date, size: int, *, dry_run: bool = False) -> dict[str, Any]:
    until = week_start_of(today)
    with connect() as conn:
        _ready(conn)
        picked = sample(candidates(conn, since, until), size)
        report: dict[str, Any] = {
            "aralik": f"{since} – {until - timedelta(days=1)}",
            "satis": sum(1 for _, h, _ in picked if h == "satis"),
            "genel": sum(1 for _, h, _ in picked if h == "genel"),
            "ay": dict(Counter(str(r["ay"]) for r, _, _ in picked)),
        }
        if dry_run:
            return report
        reused = added = skipped = 0
        for event, pool, weight in picked:
            row = call_row(event)
            if row is None:
                skipped += 1
                continue
            existing = conn.execute(
                "SELECT status, meta FROM conv_items WHERE org_id = %s AND kind = 'call' AND source_ref = %s",
                (get_org_id(), row["source_ref"]),
            ).fetchone()
            meta = (existing[1] or {}) if existing else {}
            weekly = bool(existing) and (meta.get("kota") == "secildi" or bool(meta.get("talep")))
            if not existing:
                _insert_items(conn, [row])
                added += 1
            else:
                reused += 1
            tags = {"havuz": pool, "agirlik": weight}
            if not weekly:
                tags["havuz_disi"] = True
            conn.execute(
                """
                UPDATE conv_items SET meta = coalesce(meta, '{}'::jsonb) || %s::jsonb,
                       status = CASE WHEN status = 'atlandi' AND error IN (%s, %s)
                                     THEN CASE WHEN transcript IS NOT NULL THEN 'yazildi' ELSE 'bekliyor' END
                                     ELSE status END,
                       error = CASE WHEN error IN (%s, %s) THEN NULL ELSE error END
                WHERE org_id = %s AND kind = 'call' AND source_ref = %s
                """,
                (json.dumps(tags), QUOTA_WAIT, QUOTA_OUT, QUOTA_WAIT, QUOTA_OUT, get_org_id(), row["source_ref"]),
            )
        conn.commit()
        waiting = conn.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'analiz_edildi'), count(*) FILTER (WHERE status IN ('bekliyor', 'yazildi')),
                   count(*) FILTER (WHERE status IN ('hata', 'atlandi'))
            FROM conv_items WHERE org_id = %s AND meta ? 'havuz'
            """,
            (get_org_id(),),
        ).fetchone()
    return {
        **report, "yeni": added, "mevcut": reused, "kayitsiz": skipped,
        "havuz_analiz": int(waiting[0]), "havuz_sirada": int(waiting[1]), "havuz_hata": int(waiting[2]),
    }
