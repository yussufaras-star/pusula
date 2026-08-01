"""Zoho CRM v7 senkron istemcisi (httpx).

Sadece okuma yapar: COQL, kayıt listeleme, ilişkili kayıtlar ve bulk
read. Tüm HTTP trafiği _request üzerinden geçer; retry ve token
yenileme mantığı yalnızca oradadır. Tüm fonksiyonlar kayıtları tek tek
yield eden iterator döner.
"""

import csv
import io
import os
import re
import tempfile
import time
import zipfile
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from pusula.zoho.auth import get_access_token, get_api_domain

ISTANBUL = ZoneInfo("Europe/Istanbul")

# Sayfa boyutu: Zoho'nun tek istekte döndürdüğü üst sınır.
_PAGE_SIZE = 200
# 429 ve 5xx için bekleme süreleri; toplamda 5 tekrar denemesi yapılır.
_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
# Bulk read job'ı için poll aralığı ve toplam bekleme sınırı.
_BULK_POLL_INTERVAL_SECONDS = 5
_BULK_POLL_TIMEOUT_SECONDS = 30 * 60

_client: httpx.Client | None = None


class ZohoCrmError(RuntimeError):
    """CRM isteği kalıcı olarak başarısız olduğunda fırlatılır."""


def _get_client() -> httpx.Client:
    # Tembel başlatma: import anında bağlantı açılmaz.
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
    return _client


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Tüm CRM istekleri için tek giriş noktası.

    - Authorization: Zoho-oauthtoken <token> header'ını ekler.
    - 401'de token'ı bir kez zorla yeniler ve isteği tekrarlar.
    - 429 ve 5xx'te exponential backoff ile en fazla 5 kez tekrarlar.
    - Diğer 4xx'lerde gövdeyi hata mesajına ekleyip hemen fırlatır.
    """
    url = get_api_domain() + path
    extra_headers: dict[str, str] = kwargs.pop("headers", None) or {}
    force_refresh = False
    auth_retried = False
    backoff_index = 0

    while True:
        headers = {
            "Authorization": f"Zoho-oauthtoken {get_access_token(force_refresh=force_refresh)}",
            **extra_headers,
        }
        force_refresh = False
        try:
            response = _get_client().request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ZohoCrmError(f"{method} {path} isteği gönderilemedi: {exc}") from exc

        if response.status_code == 401 and not auth_retried:
            auth_retried = True
            force_refresh = True
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if backoff_index >= len(_BACKOFF_SECONDS):
                raise ZohoCrmError(
                    f"{method} {path} {len(_BACKOFF_SECONDS)} tekrara rağmen başarısız, "
                    f"HTTP {response.status_code}: {response.text}"
                )
            time.sleep(_BACKOFF_SECONDS[backoff_index])
            backoff_index += 1
            continue

        if response.status_code >= 400:
            raise ZohoCrmError(
                f"{method} {path} HTTP {response.status_code}: {response.text}"
            )
        return response


def _format_if_modified_since(value: datetime) -> str:
    """datetime'ı Zoho'nun beklediği ISO-8601 biçimine çevirir.

    Naive değerler Europe/Istanbul kabul edilir; aware değerler
    Istanbul'a dönüştürülür.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=ISTANBUL)
    else:
        value = value.astimezone(ISTANBUL)
    return value.isoformat(timespec="seconds")


def coql(query: str) -> Iterator[dict[str, Any]]:
    """COQL sorgusunu çalıştırır, kayıtları tek tek yield eder.

    Sayfalama otomatiktir: sorguya LIMIT/OFFSET eklenerek 200'lük
    sayfalar halinde ilerlenir, more_records false olana kadar devam
    edilir. Bu yüzden sorgu LIMIT veya OFFSET içeremez.
    """
    if re.search(r"\b(limit|offset)\b", query, re.IGNORECASE):
        raise ValueError("coql sorgusu LIMIT/OFFSET içeremez; sayfalama otomatiktir")

    base_query = query.strip().rstrip(";")
    offset = 0
    while True:
        paged_query = f"{base_query} limit {_PAGE_SIZE} offset {offset}"
        response = _request("POST", "/crm/v7/coql", json={"select_query": paged_query})
        if response.status_code == 204:
            return
        payload = response.json()
        yield from payload.get("data", [])
        info = payload.get("info") or {}
        if not info.get("more_records"):
            return
        offset += _PAGE_SIZE


def get_records(
    module: str,
    fields: Sequence[str],
    modified_since: datetime | None = None,
    per_page: int = _PAGE_SIZE,
) -> Iterator[dict[str, Any]]:
    """Modül kayıtlarını listeler, tek tek yield eder.

    modified_since verilirse If-Modified-Since header'ı ile delta
    çekilir; değişiklik yoksa (304) hiç kayıt üretmez. Sayfalama
    otomatiktir; 2000 kayıt sınırından sonra Zoho'nun verdiği
    next_page_token kullanılır.
    """
    headers: dict[str, str] = {}
    if modified_since is not None:
        headers["If-Modified-Since"] = _format_if_modified_since(modified_since)

    page = 1
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"fields": ",".join(fields), "per_page": per_page}
        if page_token is not None:
            params["page_token"] = page_token
        else:
            params["page"] = page
        response = _request("GET", f"/crm/v7/{module}", params=params, headers=headers)
        if response.status_code in (204, 304):
            return
        payload = response.json()
        yield from payload.get("data", [])
        info = payload.get("info") or {}
        if not info.get("more_records"):
            return
        page_token = info.get("next_page_token")
        page += 1


def get_field_metadata(module: str) -> list[dict[str, Any]]:
    """Modül alan metadata'sını döner.

    Her öğe: api_name, field_label, data_type, custom_field.
    Kayıt çekmeden tam alan listesi için kullanılır.
    """
    response = _request(
        "GET", "/crm/v7/settings/fields", params={"module": module}
    )
    if response.status_code == 204:
        return []
    fields = response.json().get("fields") or []
    result: list[dict[str, Any]] = []
    for field in fields:
        api_name = field.get("api_name")
        if not api_name:
            continue
        result.append(
            {
                "api_name": api_name,
                "field_label": field.get("field_label"),
                "data_type": field.get("data_type"),
                "custom_field": bool(field.get("custom_field", False)),
            }
        )
    return result


def get_related(
    module: str, record_id: str, related_list: str
) -> Iterator[dict[str, Any]]:
    """Bir kaydın ilişkili listesini çeker, kayıtları tek tek yield eder."""
    page = 1
    while True:
        response = _request(
            "GET",
            f"/crm/v7/{module}/{record_id}/{related_list}",
            params={"page": page, "per_page": _PAGE_SIZE},
        )
        if response.status_code == 204:
            return
        payload = response.json()
        yield from payload.get("data", [])
        info = payload.get("info") or {}
        if not info.get("more_records"):
            return
        page += 1


def bulk_read(
    module: str,
    fields: Sequence[str],
    criteria: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Bulk read API ile modülü toplu çeker, satırları dict olarak yield eder.

    Her sayfa (en çok 200.000 kayıt) için bir job oluşturur, durumu 5
    saniye aralıkla en fazla 30 dakika poll eder, COMPLETED olunca zip
    sonucu indirir ve içindeki CSV'yi satır satır yield eder. Geçici
    dosyalar her sayfadan sonra silinir.
    """
    page = 1
    while True:
        job_id = _create_bulk_read_job(module, fields, criteria, page)
        result_info = _wait_for_bulk_read_job(job_id)
        yield from _download_bulk_read_result(job_id)
        if not result_info.get("more_records"):
            return
        page += 1


def _create_bulk_read_job(
    module: str,
    fields: Sequence[str],
    criteria: dict[str, Any] | None,
    page: int,
) -> str:
    """Bulk read job'ı oluşturur, job id döner."""
    query: dict[str, Any] = {
        "module": {"api_name": module},
        "fields": list(fields),
        "page": page,
    }
    if criteria is not None:
        query["criteria"] = criteria
    response = _request("POST", "/crm/bulk/v7/read", json={"query": query})
    job = response.json()["data"][0]
    if job.get("status") != "success":
        raise ZohoCrmError(f"Bulk read job oluşturulamadı: {job}")
    return str(job["details"]["id"])


def _wait_for_bulk_read_job(job_id: str) -> dict[str, Any]:
    """Job COMPLETED olana kadar poll eder; sonuç bilgisini döner."""
    deadline = time.monotonic() + _BULK_POLL_TIMEOUT_SECONDS
    while True:
        response = _request("GET", f"/crm/bulk/v7/read/{job_id}")
        job = response.json()["data"][0]
        state = job.get("state")
        if state == "COMPLETED":
            return job.get("result") or {}
        if state == "FAILURE":
            raise ZohoCrmError(f"Bulk read job başarısız oldu: {job}")
        if time.monotonic() >= deadline:
            raise ZohoCrmError(
                f"Bulk read job {_BULK_POLL_TIMEOUT_SECONDS // 60} dakikada "
                f"tamamlanmadı (son durum: {state})"
            )
        time.sleep(_BULK_POLL_INTERVAL_SECONDS)


def _download_bulk_read_result(job_id: str) -> Iterator[dict[str, Any]]:
    """Job sonucunu (zip) geçici dosyaya indirir, CSV satırlarını yield eder.

    Geçici dizin, iterator tüketildiğinde veya kapatıldığında silinir.
    """
    response = _request("GET", f"/crm/bulk/v7/read/{job_id}/result")
    with tempfile.TemporaryDirectory(prefix="pusula_bulk_read_") as tmp_dir:
        zip_path = os.path.join(tmp_dir, "result.zip")
        with open(zip_path, "wb") as zip_file:
            zip_file.write(response.content)
        with zipfile.ZipFile(zip_path) as archive:
            csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise ZohoCrmError("Bulk read sonucu zip içinde CSV bulunamadı")
            with archive.open(csv_names[0]) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
                yield from reader
