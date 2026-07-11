import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

CSV_OK = (
    "fund_code,fund_name,units,value,currency,contribution_pct\n"
    "HKEQ,HK Equity,100,1000,HKD,60\n"
    "USBD,US Bond,50,2500,USD,40\n"
)


async def _csv(orso_client, text, filename="alloc.csv"):
    return await orso_client.post(
        "/api/orso/ingest/csv",
        files={"file": (filename, text.encode(), "text/csv")})


async def test_csv_matches_existing_fund_by_code(orso_client):
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "HKEQ", "name": "HK Equity", "asset_class": "equity",
        "risk_rating": 5, "currency": "HKD"})).json()["id"]
    r = await _csv(orso_client, CSV_OK)
    assert r.status_code == 200
    body = r.json()
    hkeq = next(row for row in body["rows"] if row["parsed_code"] == "HKEQ")
    assert hkeq["matched_fund_id"] == fid
    assert hkeq["implied_price"] == "10.0000"        # 1000 / 100
    assert hkeq["contribution_pct"] == "60"
    usbd = next(row for row in body["rows"] if row["parsed_code"] == "USBD")
    assert usbd["matched_fund_id"] is None
    assert usbd["proposed_fund"]["currency"] == "USD"
    assert usbd["implied_price"] == "50.0000"        # 2500 / 50


async def test_csv_flags_pct_sum_off(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,10,100,50\nBBB,10,100,40\n")
    body = (await _csv(orso_client, text)).json()
    assert any("pct_sum" in w for w in body["warnings"])


async def test_csv_malformed_row_becomes_flagged_not_500(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,notanumber,100,50\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert "unparseable_units" in row["flags"]


async def test_csv_missing_required_header_422(orso_client):
    text = "units,value\n10,100\n"
    r = await _csv(orso_client, text)
    assert r.status_code == 422


async def test_csv_matches_existing_fund_by_fuzzy_name(orso_client):
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "GEQ", "name": "Global Equity Fund", "asset_class": "equity",
        "risk_rating": 5, "currency": "HKD"})).json()["id"]
    # CSV code doesn't match ("GLB"), but the name does after normalization
    # (lowercased, collapsed whitespace).
    text = ("fund_code,fund_name,units,value,contribution_pct\n"
            "GLB,global  equity fund,100,1000,100\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["matched_fund_id"] == fid
    assert row["proposed_fund"] is None
    assert "unmatched" not in row["flags"]


async def test_csv_unparseable_value_flagged(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,10,1000notanumber,100\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert "unparseable_value" in row["flags"]
