"""Görüşme analizini API yerine Claude Code oturumunda yapmak için dışa/içe aktarma.

Gece işi (gorusme-analizi.yml) ANTHROPIC_API_KEY yokken analizi atlar; yazıya
dökülmüş ('yazildi') kayıtlar oturumda analiz edilir:

    python scripts/conv_session.py export --out DIR [--batch 12] [--limit N]
        DIR/system.md (analiz talimatı), DIR/schema.json (çıktı şeması),
        DIR/batch_001.json ... ([{id, prompt}]) yazar. Sıra: haftalık akış önce,
        sonra tutarlılık havuzu (en eski önce) — gece işiyle aynı.
    python scripts/conv_session.py check --file DIR/out_001.json
        Çıktıyı ({id: analiz}) şemaya göre denetler; hata varsa listeler.
    python scripts/conv_session.py import --dir DIR
        DIR/out_*.json dosyalarındaki analizleri normalize_analysis'ten geçirip
        kaydeder (ai_model: "claude-opus-5-5 (Claude Code oturumu)"). Yalnız
        hâlâ 'yazildi' durumundaki kayıtlar yazılır; tekrar çalıştırmak güvenli.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

MODEL = "claude-opus-5-5 (Claude Code oturumu)"


def _pending(conn: Any, limit: int | None) -> list[dict[str, Any]]:
    from pusula.config import get_org_id
    from pusula.conversations import weekly_only

    cur = conn.execute(
        f"""
        SELECT c.*, r.full_name AS rep_name, e.body AS event_body FROM conv_items c
        LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
        LEFT JOIN events e ON e.id = c.event_id
        WHERE c.org_id = %s AND c.status = 'yazildi'
        ORDER BY (c.meta ? 'talep') DESC, NOT {weekly_only()}, c.occurred_at
        {"LIMIT %s" if limit else ""}
        """,
        (get_org_id(), *([limit] if limit else [])),
    )
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def export(out: Path, batch: int, limit: int | None) -> dict[str, Any]:
    from pusula.conversations import ANALYSIS_SCHEMA, ANALYSIS_SYSTEM, _prompt, call_notes
    from pusula.panel_data import connect

    out.mkdir(parents=True, exist_ok=True)
    (out / "system.md").write_text(ANALYSIS_SYSTEM, encoding="utf-8")
    (out / "schema.json").write_text(json.dumps(ANALYSIS_SCHEMA, ensure_ascii=False, indent=1), encoding="utf-8")
    with connect() as conn:
        items = _pending(conn, limit)
        prompts = [{"id": i["id"], "prompt": _prompt(i, call_notes(conn, i))} for i in items]
    files = 0
    for start in range(0, len(prompts), batch):
        files += 1
        chunk = prompts[start : start + batch]
        (out / f"batch_{files:03d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"kayit": len(prompts), "dosya": files, "ortalama_karakter": round(sum(len(p["prompt"]) for p in prompts) / max(len(prompts), 1))}


def _errors(value: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    """Şemanın kullandığı alt küme için hafif denetim (type, required, enum, items, properties)."""
    out: list[str] = []
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if kind and not isinstance(value, types[kind]) or (kind in ("integer", "number") and isinstance(value, bool)):
        return [f"{path or '/'}: {kind} bekleniyor"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} izinli değil ({', '.join(map(str, schema['enum']))})")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}/{key}: eksik")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                out.extend(_errors(value[key], sub, f"{path}/{key}"))
    if kind == "array" and "items" in schema:
        for i, v in enumerate(value):
            out.extend(_errors(v, schema["items"], f"{path}[{i}]"))
    return out


def check(file: Path) -> list[str]:
    from pusula.conversations import ANALYSIS_SCHEMA

    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return ["dosya {id: analiz} nesnesi olmalı"]
    problems = []
    for item_id, analysis in data.items():
        problems.extend(f"{item_id}{e}" for e in _errors(analysis, ANALYSIS_SCHEMA))
    return problems


def import_dir(folder: Path) -> dict[str, int]:
    from pusula.conversations import _save_analysis
    from pusula.panel_data import connect

    report = {"yazildi": 0, "atlandi": 0, "hatali": 0}
    with connect() as conn:
        for file in sorted(folder.glob("out_*.json")):
            if check(file):
                report["hatali"] += 1
                print(f"{file.name}: şema hatası, atlandı", file=sys.stderr)
                continue
            for item_id, analysis in json.loads(file.read_text(encoding="utf-8")).items():
                row = conn.execute("SELECT status FROM conv_items WHERE id = %s", (int(item_id),)).fetchone()
                if not row or row[0] != "yazildi":
                    report["atlandi"] += 1
                    continue
                _save_analysis(conn, int(item_id), analysis, {"model": MODEL}, None)
                report["yazildi"] += 1
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Görüşme analizini oturumda yap.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export")
    p.add_argument("--out", required=True)
    p.add_argument("--batch", type=int, default=12)
    p.add_argument("--limit", type=int, default=None)
    p = sub.add_parser("check")
    p.add_argument("--file", required=True)
    p = sub.add_parser("import")
    p.add_argument("--dir", required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if args.cmd == "export":
        print(json.dumps(export(Path(args.out), args.batch, args.limit), ensure_ascii=False))
    elif args.cmd == "check":
        problems = check(Path(args.file))
        print("\n".join(problems[:40]) if problems else "OK")
        return 1 if problems else 0
    else:
        print(json.dumps(import_dir(Path(args.dir)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
