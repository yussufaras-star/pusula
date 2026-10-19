"""Ekip Panosu OKR: dönem, puanlama, durum, pano kurulumu ve yetki (DB sahte)."""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from pusula import okr
from pusula.okr_template import TEMPLATES, template_for
from pusula.panel_auth import AuthUser
from pusula.panel_data import Rep
from pusula.web import okr as web_okr
from pusula.web.app import app
from pusula.web.auth import encode_session

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
REP = AuthUser(email="ayse@rexven.com", role="rep", rep_id="r1", full_name="Ayşe Kar")


def test_period_bounds_and_share() -> None:
    assert okr.period_bounds(2026, 4) == (date(2026, 10, 1), date(2026, 12, 31))
    assert okr.period_bounds(2027, 1) == (date(2027, 1, 1), date(2027, 3, 31))
    assert okr.period_bounds(2027, 0) == (date(2027, 1, 1), date(2027, 12, 31))
    first, last = okr.period_bounds(2026, 4)
    assert okr.elapsed_share(first, last, date(2026, 9, 30)) == 0.0
    assert okr.elapsed_share(first, last, date(2027, 1, 1)) == 1.0
    assert 0.2 < okr.elapsed_share(first, last, date(2026, 10, 19)) < 0.21
    assert okr.period_state(first, last, date(2026, 10, 19)) == "suruyor"
    assert okr.current_quarter(date(2026, 10, 19)) == 4


def test_progress_clamps_and_handles_down_direction() -> None:
    assert okr.kr_progress(0, 10_000_000, 2_500_000) == 0.25
    assert okr.kr_progress(66, 70, 63) == 0.0  # başlangıcın altı → 0
    assert okr.kr_progress(66, 70, 75) == 1.0
    assert okr.kr_progress(8.6, 5, 6.8) == 0.5  # döngü kısalınca ilerler
    assert okr.kr_progress(0, 10, None) is None


def test_grade_follows_doerr_scale() -> None:
    assert okr.grade_tone(0.7) == "good"
    assert okr.grade_tone(0.5) == "warning"
    assert okr.grade_tone(0.3) == "critical"
    assert okr.grade_tone(None) == "neutral"


def test_status_paces_by_time_and_bar() -> None:
    # Çeyreğin %50'si geçti; taahhütte beklenen 0,5
    assert okr.kr_status(0.48, kind="taahhut", share=0.5, state="suruyor") == ("yolunda", 0.5)
    assert okr.kr_status(0.35, kind="taahhut", share=0.5, state="suruyor")[0] == "risk"
    assert okr.kr_status(0.1, kind="taahhut", share=0.5, state="suruyor")[0] == "geride"
    # İddialıda çıta 0,7: yarı yolda 0,35 beklenir
    assert okr.kr_status(0.35, kind="iddiali", share=0.5, state="suruyor") == ("yolunda", 0.35)
    # Dönem bitti: taahhüt 1,0'ın altında kaçtı, iddialı 0,7'de tamam
    assert okr.kr_status(0.8, kind="taahhut", share=1, state="bitti")[0] == "kacti"
    assert okr.kr_status(0.8, kind="iddiali", share=1, state="bitti")[0] == "tamam"
    assert okr.kr_status(None, kind="taahhut", share=0, state="gelecek") == ("baslamadi", None)


def _rows():
    objectives = [
        {"id": 1, "year": 2026, "quarter": 0, "owner": "team", "title": "Yıllık", "kind": "iddiali", "parent_id": None},
        {"id": 2, "year": 2026, "quarter": 4, "owner": "team", "title": "Q4 ciro", "kind": "taahhut", "parent_id": 1},
        {"id": 3, "year": 2026, "quarter": 4, "owner": "r1", "title": "Kişisel", "kind": "iddiali", "parent_id": 2},
    ]
    krs = [
        {"id": 10, "objective_id": 2, "title": "Ciro", "metric": "ciro", "start_value": 0, "target_value": 10_000_000},
        {"id": 11, "objective_id": 2, "title": "Elle", "metric": None, "unit": "yuzde", "start_value": 0,
         "target_value": 90, "owner_rep_id": "r1"},
        {"id": 12, "objective_id": 3, "title": "Ulaşma", "metric": "ulasma_orani", "start_value": 60, "target_value": 70},
    ]
    checkins = [
        {"id": 1, "kr_id": 11, "value": 30.0, "confidence": 6, "note": "eski", "created_at": datetime(2026, 10, 5, 10)},
        {"id": 2, "kr_id": 11, "value": 45.0, "confidence": 7, "note": "yeni", "created_at": datetime(2026, 10, 17, 10)},
        {"id": 3, "kr_id": 10, "value": None, "confidence": 8, "note": "iyi", "created_at": datetime(2026, 10, 18, 9)},
    ]
    return objectives, krs, checkins


def test_build_board_scores_aligns_and_flags() -> None:
    objectives, krs, checkins = _rows()
    board = okr.build_board(
        year=2026, quarter=4, objectives=objectives, krs=krs, checkins=checkins,
        values={"team": {"ciro": 2_500_000}, "r1": {"ulasma_orani": 65}},
        names={"r1": "Ayşe Kar"}, today=date(2026, 10, 19),
    )
    assert [o["id"] for o in board["objectives"]] == [2, 3]  # yıllık hedef çeyrekte görünmez
    team = board["objectives"][0]
    assert team["parent_title"] == "Yıllık" and team["children"] == 1
    ciro, manual = team["krs"]
    assert ciro["source"] == "otomatik" and ciro["current"] == 2_500_000 and ciro["score"] == 0.25
    assert ciro["status"] == "yolunda"  # %21 geçti, %25 alındı
    assert ciro["confidence"] == 8 and not ciro["checkin_due"]
    assert manual["source"] == "elle" and manual["current"] == 45.0 and manual["score"] == 0.5
    assert manual["owner_name"] == "Ayşe Kar"
    assert team["score"] == round((0.25 + 0.5) / 2, 2)
    assert team["tone"] == "good"  # puan 0,38 ama iki sonuç da tempoda
    personal = board["objectives"][1]
    assert personal["owner_name"] == "Ayşe Kar" and personal["krs"][0]["score"] == 0.5
    assert personal["krs"][0]["checkin_due"]  # hiç check-in yok
    assert board["counts"] == {0: 1, 1: 0, 2: 0, 3: 0, 4: 2}
    assert [p["id"] for p in board["aligned_parents"]] == [1, 2]
    # Kişisel hedef tek anahtar sonuçlu → odak uyarısı
    assert any("Kişisel" in w for w in board["warnings"])


def test_future_quarter_has_no_values() -> None:
    objectives, krs, checkins = _rows()
    for o in objectives:
        o["year"] = 2027
    board = okr.build_board(
        year=2027, quarter=4, objectives=objectives, krs=krs, checkins=[],
        values={"team": {"ciro": 1}}, names={}, today=date(2026, 10, 19),
    )
    assert board["state"] == "gelecek"
    assert all(k["status"] == "baslamadi" and k["current"] is None for k in board["objectives"][0]["krs"][:1])


def test_rule_warnings_limit_objectives() -> None:
    objs = [{"owner": "team", "title": f"H{i}", "krs": [1, 2, 3]} for i in range(6)]
    warnings = okr.rule_warnings(objs, {})
    assert any("6 hedef" in w for w in warnings)


def test_visibility_rules() -> None:
    assert okr.visible("team", "r1") and okr.visible("r1", "r1") and not okr.visible("r2", "r1")
    assert okr.visible("r2", None)
    assert okr.cfr_visible({"kind": "takdir", "rep_id": "r2"}, "r1")
    assert not okr.cfr_visible({"kind": "konusma", "rep_id": "r2"}, "r1")


def test_templates_are_valid() -> None:
    for year in TEMPLATES:
        template = template_for(year)
        keys = {o.get("key") for o in template}
        for obj in template:
            assert obj["kind"] in okr.KINDS and obj["status"] in okr.STATUSES
            assert okr.MIN_KRS <= len(obj["krs"]) <= okr.MAX_KRS
            assert obj.get("parent") is None or obj["parent"] in keys
            for kr in obj["krs"]:
                assert kr["metric"] is None or kr["metric"] in okr.METRICS
                assert kr["start"] != kr["target"]
    year = template_for(2027)
    quarterly = sum(kr["target"] for o in year if o["quarter"] for kr in o["krs"] if kr["metric"] == "ciro")
    annual = next(kr["target"] for o in year if o["quarter"] == 0 for kr in o["krs"] if kr["metric"] == "ciro")
    assert quarterly == annual


class FakeStore:
    def __init__(self) -> None:
        self.inserts: list = []
        self.updates: list = []
        self.deleted: list = []
        self.objectives = {2: {"id": 2, "year": 2026, "quarter": 4, "owner": "team"},
                           3: {"id": 3, "year": 2026, "quarter": 4, "owner": "r1"}}
        self.krs = {10: {"id": 10, "objective_id": 2, "owner_rep_id": None, "owner": "team", "kind": "taahhut"},
                    11: {"id": 11, "objective_id": 2, "owner_rep_id": "r1", "owner": "team", "kind": "taahhut"},
                    12: {"id": 12, "objective_id": 3, "owner_rep_id": None, "owner": "r1", "kind": "iddiali"}}

    def insert(self, table, values):
        self.inserts.append((table, values))
        return 99

    def update(self, table, row_id, values):
        self.updates.append((table, row_id, values))

    def delete(self, table, row_id):
        self.deleted.append((table, row_id))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    fake = FakeStore()
    monkeypatch.setattr(okr, "insert", fake.insert)
    monkeypatch.setattr(okr, "update", fake.update)
    monkeypatch.setattr(okr, "delete", fake.delete)
    monkeypatch.setattr(okr, "objective_row", fake.objectives.get)
    monkeypatch.setattr(okr, "kr_row", fake.krs.get)

    def client(user: AuthUser) -> TestClient:
        c = TestClient(app)
        c.cookies.set("pusula_session", encode_session(user))
        return c

    return client, fake


def test_rep_writes_only_personal_objectives(env) -> None:
    client, fake = env
    rep = client(REP)
    body = {"year": 2026, "quarter": 4, "title": "Ekip hedefi", "owner": "team"}
    assert rep.post("/api/okr/objectives", json=body).status_code == 403
    assert rep.post("/api/okr/objectives", json={**body, "owner": "r2"}).status_code == 403
    assert rep.post("/api/okr/objectives", json={**body, "owner": "r1", "parent_id": 2}).status_code == 200
    assert fake.inserts[-1][1]["created_by"] == REP.email
    assert rep.delete("/api/okr/objectives/2").status_code == 403
    assert rep.delete("/api/okr/objectives/3").status_code == 200
    assert client(ADMIN).post("/api/okr/objectives", json=body).status_code == 200


def test_parent_must_be_same_year(env) -> None:
    client, _ = env
    body = {"year": 2027, "quarter": 1, "title": "x", "parent_id": 2}
    assert client(ADMIN).post("/api/okr/objectives", json=body).status_code == 400


def test_key_result_metric_sets_unit_and_rejects_unknown(env) -> None:
    client, fake = env
    admin = client(ADMIN)
    body = {"objective_id": 2, "title": "Döngü", "metric": "dongu_gun", "unit": "sayi", "start_value": 8.6, "target_value": 5}
    assert admin.post("/api/okr/key-results", json=body).status_code == 200
    assert fake.inserts[-1][1]["unit"] == "gun" and fake.inserts[-1][1]["direction"] == "down"
    assert admin.post("/api/okr/key-results", json={**body, "metric": "yok"}).status_code == 400
    assert admin.post("/api/okr/key-results", json={**body, "target_value": 8.6}).status_code == 400
    assert client(REP).post("/api/okr/key-results", json=body).status_code == 403
    assert client(REP).post("/api/okr/key-results", json={**body, "objective_id": 3}).status_code == 200


def test_checkin_requires_kr_owner(env) -> None:
    client, fake = env
    rep = client(REP)
    assert rep.post("/api/okr/checkins", json={"kr_id": 10, "confidence": 7}).status_code == 403
    assert rep.post("/api/okr/checkins", json={"kr_id": 11, "value": 50, "confidence": 7}).status_code == 200
    assert rep.post("/api/okr/checkins", json={"kr_id": 12, "note": "  bu hafta 3 toplantı  "}).status_code == 200
    assert fake.inserts[-1][1]["note"] == "bu hafta 3 toplantı" and fake.inserts[-1][1]["author"] == "Ayşe Kar"
    assert rep.post("/api/okr/checkins", json={"kr_id": 12}).status_code == 400
    assert rep.post("/api/okr/checkins", json={"kr_id": 12, "confidence": 11}).status_code == 422
    assert rep.post("/api/okr/checkins", json={"kr_id": 77, "confidence": 5}).status_code == 404


def test_cfr_permissions(env) -> None:
    client, _ = env
    rep = client(REP)
    assert rep.post("/api/okr/cfr", json={"kind": "takdir", "rep_id": "r2", "text": "Harika kapanış"}).status_code == 200
    assert rep.post("/api/okr/cfr", json={"kind": "geri_bildirim", "rep_id": "r1", "text": "x"}).status_code == 403
    assert rep.post("/api/okr/cfr", json={"kind": "konusma", "rep_id": "r2", "text": "x"}).status_code == 403
    assert rep.post("/api/okr/cfr", json={"kind": "konusma", "rep_id": "r1", "text": "x"}).status_code == 200
    assert rep.post("/api/okr/seed", json={"year": 2027}).status_code == 403


def test_board_scopes_rep_view(env, monkeypatch) -> None:
    client, _ = env
    objectives, krs, checkins = _rows()
    cfr = [
        {"id": 1, "kind": "takdir", "rep_id": "r2", "objective_id": None, "text": "t", "author": "a", "created_at": datetime(2026, 10, 1)},
        {"id": 2, "kind": "konusma", "rep_id": "r2", "objective_id": None, "text": "k", "author": "a", "created_at": datetime(2026, 10, 1)},
    ]
    objectives.append({"id": 4, "year": 2026, "quarter": 4, "owner": "r2", "title": "Başkası", "kind": "taahhut", "parent_id": None})
    monkeypatch.setattr(okr, "load_year", lambda year: {"objectives": objectives, "krs": krs, "checkins": checkins, "cfr": cfr})
    monkeypatch.setattr(web_okr, "load_reps", lambda: [Rep("r1", "Ayşe Kar"), Rep("r2", "Serkan Şahin")])
    monkeypatch.setattr(web_okr, "period_values", lambda year, quarter, now: ({"team": {"ciro": 1_000_000}}, {}))
    body = client(REP).get("/api/okr?year=2026&quarter=4").json()
    assert [o["id"] for o in body["objectives"]] == [2, 3]
    assert [c["id"] for c in body["cfr"]] == [1]
    assert [r["rep_id"] for r in body["reps"]] == ["r1"]
    assert not body["can_edit_team"] and not body["template_available"]
    admin = client(ADMIN).get("/api/okr?year=2026&quarter=4").json()
    assert [o["id"] for o in admin["objectives"]] == [2, 3, 4]
    assert admin["can_edit_team"] and not admin["template_available"]  # yılda hedef var
    assert client(ADMIN).get("/api/okr?year=2026&quarter=7").status_code == 400


def test_phased_share_follows_monthly_plan() -> None:
    first, last = okr.period_bounds(2026, 4)
    weights = [3_148_330, 5_920_740, 4_370_070]
    # 31 Ekim sonu: yalnız Ekim'in payı (%23), takvim oranı %34 olurdu
    assert okr.phased_share(first, last, date(2026, 11, 1), weights) < 0.25
    assert okr.phased_share(first, last, date(2026, 10, 16), weights) == round(3_148_330 * 15.5 / 31 / sum(weights), 4)
    assert okr.phased_share(first, last, date(2026, 10, 16), [1, 2]) == okr.elapsed_share(first, last, date(2026, 10, 16))
    assert okr.phased_share(first, last, date(2027, 1, 2), weights) == 1.0


def test_auto_kr_falls_back_to_checkin_and_annual_rolls_up() -> None:
    objectives = [
        {"id": 1, "year": 2026, "quarter": 0, "owner": "team", "title": "Yıl", "kind": "taahhut", "parent_id": None},
        {"id": 2, "year": 2026, "quarter": 1, "owner": "team", "title": "Q1", "kind": "taahhut", "parent_id": 1},
    ]
    krs = [
        {"id": 10, "objective_id": 1, "title": "Ciro", "metric": "ciro", "start_value": 0, "target_value": 50_000_000},
        {"id": 20, "objective_id": 2, "title": "Q1 ciro", "metric": "ciro", "start_value": 0, "target_value": 13_000_000},
    ]
    checkins = [{"id": 1, "kr_id": 20, "value": 9_000_000.0, "confidence": None, "note": None,
                 "created_at": datetime(2026, 10, 19, 9)}]
    q1 = okr.build_board(year=2026, quarter=1, objectives=objectives, krs=krs, checkins=checkins,
                         values={}, names={}, today=date(2026, 10, 19))
    kr = q1["objectives"][0]["krs"][0]
    assert kr["source"] == "yedek" and kr["current"] == 9_000_000.0
    rollups = okr.annual_rollup(
        objectives=objectives, krs=krs, checkins=checkins,
        quarter_values={1: {"ciro": None}, 2: {"ciro": 10_000_000}, 3: {"ciro": None}, 4: {"ciro": 2_000_000}},
    )
    assert rollups["ciro"] == (21_000_000, [3])
    assert "satis" not in rollups  # hiçbir çeyrekte yok → veri yok
    year = okr.build_board(year=2026, quarter=0, objectives=objectives, krs=krs, checkins=checkins,
                           values={"team": {"ciro": 1}}, names={}, today=date(2026, 10, 19), rollups=rollups)
    annual = year["objectives"][0]["krs"][0]
    assert annual["current"] == 21_000_000 and "Q3 eksik" in annual["note"]


def test_plan_2026_template_matches_sheet() -> None:
    from pusula.okr_template import PLAN_2026, PLAN_2026_YEAR

    assert sum(PLAN_2026["ciro"]) == PLAN_2026_YEAR["ciro"]
    assert sum(PLAN_2026["satis"]) == PLAN_2026_YEAR["satis"] == 1075
    year = template_for(2026)
    q4 = [o for o in year if o["quarter"] == 4]
    ciro = next(kr for o in q4 for kr in o["krs"] if kr["metric"] == "ciro")
    assert ciro["target"] == 13_439_140 and ciro["phasing"] == [3_148_330, 5_920_740, 4_370_070]
    assert len(q4) <= okr.MAX_OBJECTIVES
