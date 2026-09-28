"""Yönetici Güncelle düğmesi. GitHub'daki ingest akışını başlatır.

Zoho anahtarları panelde durmaz. İş Actions'ta koşar.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_WORKFLOW = "ingest.yml"
_DEFAULT_REPO = "yussufaras-star/pusula"


class DispatchError(Exception):
    """Ingest başlatılamadı. Mesajda gizli bilgi yok."""


def dispatch_ingest(
    token: str,
    *,
    repo: str = _DEFAULT_REPO,
    ref: str = "main",
) -> None:
    """ingest.yml workflow_dispatch. Başarı 204."""
    url = (
        "https://api.github.com/repos/"
        f"{repo}/actions/workflows/{_WORKFLOW}/dispatches"
    )
    payload = json.dumps({"ref": ref}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "pusula-panel",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(getattr(response, "status", 204))
    except urllib.error.HTTPError as exc:
        raise DispatchError(f"güncelleme başlamadı ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise DispatchError("güncelleme başlamadı") from exc
    if status not in (200, 204):
        raise DispatchError(f"güncelleme başlamadı ({status})")
