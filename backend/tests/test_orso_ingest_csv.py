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


async def test_csv_value_with_thousands_commas_parses(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            'AAA,10,"683,575.23",100\n')
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert "unparseable_value" not in row["flags"]
    assert row["value"] == "683575.23"


async def test_csv_malformed_comma_numbers_are_unparseable(orso_client):
    # Blindly stripping commas would turn these into WRONG numbers with no
    # flag: "1,2,3"->123, European decimal-comma "9,97"->997 (10x), "1,23".
    # They must all stay unparseable instead.
    for bad in ("1,2,3", "9,97", "1,23"):
        text = ("fund_code,units,value,contribution_pct\n"
                f'AAA,10,"{bad}",100\n')
        r = await _csv(orso_client, text)
        assert r.status_code == 200
        row = r.json()["rows"][0]
        assert "unparseable_value" in row["flags"], bad
        assert row["value"] is None, bad


async def test_csv_legitimate_thousands_grouping_parses(orso_client):
    cases = {"683,575.23": "683575.23", "1,234,567.89": "1234567.89",
             "HK$683,575.23": "683575.23"}
    for raw, expected in cases.items():
        text = ("fund_code,units,value,contribution_pct\n"
                f'AAA,10,"{raw}",100\n')
        r = await _csv(orso_client, text)
        assert r.status_code == 200
        row = r.json()["rows"][0]
        assert "unparseable_value" not in row["flags"], raw
        assert row["value"] == expected, raw


async def test_csv_pct_with_percent_sign_parses(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,10,100,9.97%\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert "unparseable_pct" not in row["flags"]
    assert row["contribution_pct"] == "9.97"


async def test_csv_value_with_currency_prefix_and_suffix_parses(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,10,HK$683575.23,100\n"
            "BBB,10,683575.23 HKD,100\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    rows = r.json()["rows"]
    for row in rows:
        assert "unparseable_value" not in row["flags"]
        assert row["value"] == "683575.23"


async def test_csv_junk_value_still_unparseable(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,10,n/a,100\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert "unparseable_value" in row["flags"]


async def test_unmatched_long_name_gets_derived_short_code(orso_client):
    text = ("fund_code,fund_name,units,value,contribution_pct\n"
            "Hang Seng Index Tracking Fund Class A Accumulation,"
            "Hang Seng Index Tracking Fund Class A Accumulation,10,100,100\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["proposed_fund"] is not None
    assert len(row["proposed_fund"]["code"]) <= 16
    assert row["proposed_fund"]["name"] == (
        "Hang Seng Index Tracking Fund Class A Accumulation")


async def test_unmatched_funds_with_colliding_derived_codes_get_distinct_codes(orso_client):
    text = ("fund_code,fund_name,units,value,contribution_pct\n"
            "Hang Seng Index Tracking Fund,Hang Seng Index Tracking Fund,10,100,50\n"
            "Hang Seng Income Trust Fund,Hang Seng Income Trust Fund,10,100,50\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    rows = r.json()["rows"]
    codes = [row["proposed_fund"]["code"] for row in rows]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert len(code) <= 16
