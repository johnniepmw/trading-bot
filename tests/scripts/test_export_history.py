"""Offline tests for scripts/export_history.py: fake Yahoo and EODHD responses, no network."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_history", ROOT / "scripts" / "export_history.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


ex = load()
TODAY = date(2024, 6, 1)
DAYS = [date(2022, 12, 29), date(2022, 12, 30), date(2023, 1, 1), date(2023, 1, 3), date(2023, 1, 4)]


def stamp(d: date, hour: int = 8, minute: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC).timestamp())


def yahoo(  # noqa: PLR0913 - a test builder with defaults
    days: list[date],
    closes: list[float],
    *,
    adj: list[float] | None = None,
    currency: str = "GBP",
    opens: list[float] | None = None,
    stamps: list[int] | None = None,
) -> bytes:
    return json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": currency},
                        "timestamp": stamps or [stamp(d) for d in days],
                        "indicators": {
                            "quote": [{"open": opens or closes, "close": closes}],
                            "adjclose": [{"adjclose": adj or closes}],
                        },
                    }
                ]
            }
        }
    ).encode()


def eodhd(days: list[date], adjusted: list[float]) -> bytes:
    rows = [
        {"date": d.isoformat(), "close": a * 2, "adjusted_close": a}  # only the adjusted close counts
        for d, a in zip(days, adjusted, strict=True)
    ]
    return json.dumps(rows).encode()


KEY = "test-key-123"


def opener(pages: dict[str, bytes], fetched: list[str] | None = None):  # type: ignore[no-untyped-def]
    """Serves pages by the URL's last path part: Yahoo's AAA.L, EODHD's AAA.LSE."""

    def fetch(url: str) -> bytes:
        if fetched is not None:
            fetched.append(url)
        symbol = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
        if symbol in pages:
            return pages[symbol]
        raise ex.ExportError(f"unexpected symbol {symbol}")

    return fetch


ORIGINAL_MIN_CHECKED_DAYS = ex.MIN_CHECKED_DAYS


@pytest.fixture(autouse=True)
def few_days_suffice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "MIN_CHECKED_DAYS", 1)  # the fakes are a few days long; one test sets it back


SRC = ex.Source("AAA.L", "AAA.LSE", distributing=False)
FUND = ex.Fund("AAA", "trade", SRC, [])
CLOSES = [100.0, 101.0, 102.0, 103.0, 104.0]


def series(bars: list[tuple[date, float]], currency: str = "GBP", symbol: str = "X.L") -> object:
    return ex.Series(symbol, currency, [ex.Bar(d, c, c) for d, c in bars])


# --- universe ------------------------------------------------------------------------
def test_the_repo_universe_has_seven_trading_funds_and_ssac_as_benchmark_only() -> None:
    funds, hold_out = ex.load_universe()
    assert hold_out == date(2023, 1, 1)
    assert sorted(f.ticker for f in funds if f.role == "trade") == [
        "EMIM",
        "IGLT",
        "INXG",
        "SGLN",
        "VAGP",
        "VUKE",
        "VWRP",
    ]
    assert [f.ticker for f in funds if f.role == "benchmark"] == ["SSAC"]


def test_vagp_is_spliced_onto_the_vanguard_fund_on_the_same_index() -> None:
    vagp = next(f for f in ex.load_universe()[0] if f.ticker == "VAGP")
    assert [(p.yahoo, p.eodhd, p.distributing) for p in vagp.proxies] == [("0P0000KM24.L", None, False)]


@pytest.mark.parametrize(
    ("field", "value"),
    [("ticker", "../../x"), ("ticker", "/home/u/x"), ("yahoo", "A.L/../.."), ("eodhd", "A.LSE?x=1")],
)
def test_unsafe_tickers_or_symbols_are_refused(tmp_path: Path, field: str, value: str) -> None:
    entry = {
        "ticker": "AAA",
        "role": "trade",
        "yahoo": "AAA.L",
        "eodhd": "AAA.LSE",
        "distributing": False,
    } | {field: value}
    body = (
        'hold_out_start = "2023-01-01"\n[[fund]]\n'
        + "".join(f"{k} = {json.dumps(v)}\n" for k, v in entry.items())
        + "proxies = []\n"
    )
    path = tmp_path / "u.toml"
    path.write_text(body)
    with pytest.raises(ex.ExportError, match="invalid"):
        ex.load_universe(path)


# --- Yahoo parsing ---------------------------------------------------------------------
def test_adjusted_closes_are_used_and_opens_adjusted_by_the_same_factor() -> None:
    s = ex.fetch_yahoo(
        "AAA.L",
        opener({"AAA.L": yahoo(DAYS[:2], [100.0, 110.0], adj=[50.0, 55.0], opens=[98.0, 108.0])}),
        TODAY,
    )
    assert [(b.open, b.close) for b in s.bars] == [(49.0, 50.0), (54.0, 55.0)]


def test_yahoo_pence_is_gbx_and_bad_rows_are_skipped_and_counted() -> None:
    body = json.loads(yahoo(DAYS[:4], [100.0, 101.0, 102.0, 103.0], currency="GBp"))
    quote = body["chart"]["result"][0]["indicators"]["quote"][0]
    quote["close"][1] = None
    quote["open"][2] = 0
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": json.dumps(body).encode()}), TODAY)
    assert s.currency == "GBX" and [b.day for b in s.bars] == [DAYS[0], DAYS[3]] and s.skipped == 2


def test_non_finite_values_are_skipped_never_kept() -> None:
    body = yahoo(DAYS[:3], [100.0, 101.0, 102.0]).replace(b"101.0", b"NaN")
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": body}), TODAY)
    assert all(math.isfinite(b.close) for b in s.bars) and s.skipped == 1


def test_dates_are_london_dates() -> None:
    late = stamp(date(2024, 5, 1), 23, 30)  # 23:30 UTC in summer is 00:30 next day in London
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": yahoo([date(2024, 5, 1)], [1.0], stamps=[late])}), TODAY)
    assert s.bars[0].day == date(2024, 5, 2)


def test_todays_partial_bar_and_duplicate_rows_are_dropped() -> None:
    days = [date(2024, 5, 30), date(2024, 5, 31), date(2024, 5, 31), TODAY]
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": yahoo(days, [1.0, 2.0, 2.1, 3.0])}), TODAY)
    assert [(b.day, b.close) for b in s.bars] == [(date(2024, 5, 30), 1.0), (date(2024, 5, 31), 2.1)]


@pytest.mark.parametrize(
    ("closes", "opens", "why"),
    [([100.0, 10_000.0], None, "jump"), ([100.0, 100.0], [100.0, 150.0], "open")],
)
def test_bad_series_are_refused_at_export_time(
    closes: list[float], opens: list[float] | None, why: str
) -> None:
    with pytest.raises(ex.ExportError, match=why):
        ex.fetch_yahoo("AAA.L", opener({"AAA.L": yahoo(DAYS[:2], closes, opens=opens)}), TODAY)


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"chart": {"result": []}}',
        b"not json",
        json.dumps({"chart": {"result": [{"meta": {"currency": "USD"}}]}}).encode(),
    ],
)
def test_an_unexpected_yahoo_response_is_an_export_error(body: bytes) -> None:
    with pytest.raises(ex.ExportError):
        ex.fetch_yahoo("AAA.L", opener({"AAA.L": body}), TODAY)


def test_mismatched_yahoo_arrays_are_an_export_error() -> None:
    body = json.loads(yahoo(DAYS[:2], [1.0, 2.0]))
    body["chart"]["result"][0]["indicators"]["quote"][0]["open"] = [1.0]
    with pytest.raises(ex.ExportError):
        ex.fetch_yahoo("AAA.L", opener({"AAA.L": json.dumps(body).encode()}), TODAY)


def test_implied_dividend_yield_is_measured() -> None:
    days = [date(2020, 1, 1), date(2021, 1, 1)]
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": yahoo(days, [100.0, 100.0], adj=[97.0, 100.0])}), TODAY)
    assert s.yield_ == pytest.approx(100 / 97 - 1, abs=1e-3)


@pytest.mark.parametrize(
    ("distributing", "yield_", "bad"),
    [(False, 0.0, False), (False, 0.02, True), (True, 0.03, False), (True, 0.0, True), (True, 0.5, True)],
)
def test_dividend_yield_must_match_whether_the_series_distributes(
    distributing: bool, yield_: float, bad: bool
) -> None:
    s = ex.Series("A.L", "GBP", [ex.Bar(DAYS[0], 1.0, 1.0)], yield_=yield_)
    assert (ex.yield_problem(ex.Source("A.L", "a.uk", distributing), s) is not None) is bad


# --- splicing --------------------------------------------------------------------------
def test_a_proxy_is_spliced_under_the_fund_scaled_at_the_first_common_day() -> None:
    primary = series([(DAYS[1], 20.0), (DAYS[3], 21.0)], symbol="NEW.L")
    proxy = series([(DAYS[0], 900.0), (DAYS[1], 1000.0)], currency="GBX", symbol="OLD.L")
    bars, splices = ex.splice(primary, [proxy])
    assert [b.close for b in bars] == pytest.approx([18.0, 20.0, 21.0])  # 1000p = £10, scaled x2
    assert splices == [{"proxy": "OLD.L", "until": "2022-12-30", "ratio": "2.000000"}]


def test_two_proxies_splice_newest_first_and_pounds_convert_to_pence() -> None:
    primary = series([(DAYS[3], 2000.0), (DAYS[4], 2100.0)], currency="GBX", symbol="NEW.L")
    middle = series([(DAYS[1], 9.0), (DAYS[3], 10.0)], currency="GBP", symbol="MID.L")  # £10 = 1000p, x2
    oldest = series([(DAYS[0], 4.0), (DAYS[1], 4.5)], currency="GBP", symbol="OLD.L")  # 450p vs 1800p, x4
    bars, splices = ex.splice(primary, [oldest, middle])
    assert [b.close for b in bars] == pytest.approx([1600.0, 1800.0, 2000.0, 2100.0])
    assert [s["proxy"] for s in splices] == ["MID.L", "OLD.L"]


def test_a_proxy_without_the_splice_day_is_an_error() -> None:
    with pytest.raises(ex.ExportError, match="no bar on"):
        ex.splice(series([(DAYS[3], 20.0)]), [series([(DAYS[0], 10.0)])])


# --- second source -----------------------------------------------------------------------
BARS = [ex.Bar(d, c, c) for d, c in zip(DAYS, CLOSES, strict=True)]


def test_the_comparison_ignores_scale_and_counts_disagreeing_days() -> None:
    same = ex.compare(BARS, {b.day: b.close * 2 for b in BARS})
    off = ex.compare(BARS, dict(zip(DAYS, [100.0, 101.0, 106.0, 103.0, 104.0], strict=True)))
    assert (same["disagreeing_days"], same["common_days"], same["coverage"]) == (0, 4, 1.0)
    assert off["disagreeing_days"] == 2 and off["worst"] == pytest.approx(0.0396, abs=1e-3)


def test_one_large_disagreement_in_a_long_history_is_a_problem() -> None:
    days = [date(2020, 1, 1).fromordinal(date(2020, 1, 1).toordinal() + i) for i in range(200)]
    bars = [ex.Bar(d, 100.0, 100.0) for d in days]
    second = {d: (50.0 if i == 100 else 100.0) for i, d in enumerate(days)}  # an unadjusted 2:1 split, say
    problems = ex.check_problems("AAA.L", ex.compare(bars, second))
    assert any("single day" in p for p in problems)


def test_a_second_source_covering_too_little_of_its_window_is_a_problem() -> None:
    problems = ex.check_problems("AAA.L", ex.compare(BARS, {b.day: b.close for b in [BARS[0], BARS[-1]]}))
    assert any("covers" in p for p in problems)


def test_only_the_second_sources_window_is_compared() -> None:
    check = ex.compare(BARS, {b.day: b.close for b in BARS[-3:]})  # the free plan's last year, say
    assert (check["common_days"], check["coverage"]) == (2, 1.0)


def test_too_few_compared_days_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "MIN_CHECKED_DAYS", 200)
    problems = ex.check_problems("AAA.L", ex.compare(BARS, {b.day: b.close for b in BARS}))
    assert any("only 4 days compared" in p for p in problems)


def test_eodhds_adjusted_close_is_read_and_todays_bar_dropped() -> None:
    body = eodhd([DAYS[0], DAYS[1], TODAY], [10.0, 11.0, 12.0])
    assert ex.fetch_eodhd("AAA.LSE", KEY, opener({"AAA.LSE": body}), TODAY) == {DAYS[0]: 10.0, DAYS[1]: 11.0}


def test_eodhd_is_asked_for_400_days_up_to_yesterday() -> None:
    fetched: list[str] = []
    ex.fetch_eodhd("AAA.LSE", KEY, opener({"AAA.LSE": eodhd(DAYS, CLOSES)}, fetched), TODAY)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(fetched[0]).query)
    assert (query["from"], query["to"], query["api_token"]) == (["2023-04-28"], ["2024-05-31"], [KEY])


@pytest.mark.parametrize("body", [b"<html>", b'{"error": "x"}', b'[{"date": "2024-01-02"}]', b"[]"])
def test_an_unexpected_eodhd_response_is_an_export_error_without_the_body(body: bytes) -> None:
    with pytest.raises(ex.ExportError, match="EODHD") as raised:
        ex.fetch_eodhd("AAA.LSE", KEY, opener({"AAA.LSE": body}), TODAY)
    assert "html" not in str(raised.value) and "error" not in str(raised.value)


def test_a_failed_fetch_never_shows_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> bytes:  # the error type that holds the full URL
        raise urllib.error.HTTPError(
            f"https://eodhd.com/api/eod/AAA.LSE?api_token={KEY}", 401, "No", {}, None
        )  # type: ignore[arg-type]

    monkeypatch.setattr(ex._URLOPENER, "open", refuse)
    with pytest.raises(ex.ExportError, match=r"eodhd\.com/api/eod/AAA\.LSE") as raised:
        ex.default_opener(f"https://eodhd.com/api/eod/AAA.LSE?api_token={KEY}")
    assert KEY not in str(raised.value) and raised.value.__cause__ is None


def test_a_refused_redirect_never_shows_the_key() -> None:
    with pytest.raises(ex.ExportError, match="refusing") as raised:
        ex.check_url(f"https://evil.example/x?api_token={KEY}")
    assert KEY not in str(raised.value)


# --- export --------------------------------------------------------------------------------
def pages(eodhd_closes: list[float] | None = None) -> dict[str, bytes]:
    return {"AAA.L": yahoo(DAYS, CLOSES), "AAA.LSE": eodhd(DAYS, eodhd_closes or CLOSES)}


def test_export_splits_dev_and_hold_out_at_the_boundary_with_a_manifest(tmp_path: Path) -> None:
    out = tmp_path / "export"
    manifest = ex.export([FUND], date(2023, 1, 1), out, opener(pages()), TODAY, eodhd_key=KEY)
    dev = (out / "dev" / "AAA.csv").read_text().splitlines()
    held = (out / "holdout" / "AAA.csv").read_text().splitlines()
    assert dev[0] == "date,open,close,currency"
    assert [line[:10] for line in dev[1:]] == ["2022-12-29", "2022-12-30"]
    assert [line[:10] for line in held[1:]] == [
        "2023-01-01",
        "2023-01-03",
        "2023-01-04",
    ]  # the start day is hold-out
    assert (
        json.loads((out / "manifest.json").read_text())["funds"]["AAA"]["dev_sha256"]
        == manifest["funds"]["AAA"]["dev_sha256"]
    )


def test_export_refuses_a_folder_that_already_has_files(tmp_path: Path) -> None:
    (tmp_path / "keep.csv").write_text("mine")
    with pytest.raises(ex.ExportError, match="not empty"):
        ex.export([FUND], date(2023, 1, 1), tmp_path, opener(pages()), TODAY, eodhd_key=KEY)
    assert (tmp_path / "keep.csv").read_text() == "mine"


def test_a_disagreement_writes_nothing_and_touches_nothing_else(tmp_path: Path) -> None:
    (tmp_path / "other.csv").write_text("someone else's")
    out = tmp_path / "export"
    with pytest.raises(ex.ExportError, match="disagree"):
        ex.export(
            [FUND],
            date(2023, 1, 1),
            out,
            opener(pages([100.0, 120.0, 90.0, 103.0, 104.0])),
            TODAY,
            eodhd_key=KEY,
        )
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.csv"]  # no temp folder left either


def test_a_failure_part_way_leaves_no_partial_export(tmp_path: Path) -> None:
    second = ex.Fund("BBB", "trade", ex.Source("BBB.L", "BBB.LSE", distributing=False), [])
    out = tmp_path / "export"
    with pytest.raises(ex.ExportError):
        ex.export(
            [FUND, second], date(2023, 1, 1), out, opener(pages()), TODAY, eodhd_key=KEY
        )  # BBB has no pages
    assert not out.exists() and list(tmp_path.iterdir()) == []


def test_an_override_writes_and_records_the_problems(tmp_path: Path) -> None:
    out = tmp_path / "export"
    manifest = ex.export(
        [FUND],
        date(2023, 1, 1),
        out,
        opener(pages([100.0, 120.0, 90.0, 103.0, 104.0])),
        TODAY,
        eodhd_key=KEY,
        accept_problems=True,
    )
    assert manifest["accept_problems"] is True and manifest["problems"]


def test_proxies_are_not_sent_to_eodhd_and_the_manifest_says_what_was_checked(tmp_path: Path) -> None:
    fund = ex.Fund("AAA", "trade", SRC, [ex.Source("OLD.L", None, distributing=False)])
    fetched: list[str] = []
    p = pages() | {"OLD.L": yahoo(DAYS[:3], [50.0, 51.0, 51.5])}
    manifest = ex.export(
        [fund], date(2023, 1, 1), tmp_path / "export", opener(p, fetched), TODAY, eodhd_key=KEY
    )
    sources = manifest["funds"]["AAA"]["sources"]
    assert sources["OLD.L"]["cross_check"] == "none (Yahoo alone)"
    assert (sources["AAA.L"]["checked_from"], sources["AAA.L"]["checked_to"]) == ("2022-12-29", "2023-01-04")
    assert "Yahoo alone" in manifest["cross_check"]
    assert [u for u in fetched if "eodhd.com" in u and "OLD" in u] == []


def test_the_key_is_never_written(tmp_path: Path) -> None:
    out = tmp_path / "export"
    ex.export([FUND], date(2023, 1, 1), out, opener(pages()), TODAY, eodhd_key=KEY)
    assert [f for f in out.rglob("*") if f.is_file() and KEY in f.read_text()] == []


def test_the_export_needs_an_eodhd_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ex.getpass, "getpass", lambda _prompt: "  ")
    assert ex.main(["--out", str(tmp_path / "export")], opener=opener({})) == 1
    assert "EODHD key missing" in capsys.readouterr().err


@pytest.mark.parametrize("inside", ["history", "research/data"])
def test_the_export_refuses_any_folder_inside_the_repo(
    capsys: pytest.CaptureFixture[str], inside: str
) -> None:
    assert ex.main(["--out", str(ROOT / inside)], opener=opener({})) == 1
    assert "inside the repository" in capsys.readouterr().err


def test_the_key_typed_at_the_prompt_reaches_eodhd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ex.getpass, "getpass", lambda _prompt: f" {KEY}\n")
    monkeypatch.setattr(ex, "load_universe", lambda: ([FUND], date(2023, 1, 1)))
    fetched: list[str] = []
    assert ex.main(["--out", str(tmp_path / "export")], opener=opener(pages(), fetched)) == 0
    eodhd_urls = [u for u in fetched if "eodhd.com" in u]
    assert [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["api_token"] for u in eodhd_urls] == [[KEY]]


def test_the_real_minimum_of_compared_days_is_200() -> None:
    assert ORIGINAL_MIN_CHECKED_DAYS == 200  # read before the fixture lowers it


def test_list_mode_reports_yahoo_ranges_and_failures() -> None:
    funds = [ex.Fund("AAA", "trade", SRC, [ex.Source("OLD.L", None, distributing=True)])]
    lines = ex.list_symbols(funds, opener({"AAA.L": yahoo(DAYS[:2], [1.0, 2.0])}), TODAY)
    assert any("OLD.L" in line and "FAILED" in line for line in lines)
    assert any("AAA.L" in line and "2022-12-29 .. 2022-12-30" in line for line in lines)


def test_list_mode_spends_no_eodhd_calls() -> None:
    fetched: list[str] = []
    ex.list_symbols([FUND], opener({"AAA.L": yahoo(DAYS[:2], [1.0, 2.0])}, fetched), TODAY)
    assert [u for u in fetched if "eodhd.com" in u] == []


@pytest.mark.parametrize(
    "url", ["http://eodhd.com/x", "https://stooq.com/x", "https://evil.example/x", "file:///etc/passwd"]
)
def test_only_https_on_the_two_data_hosts_is_fetched_including_redirects(url: str) -> None:
    with pytest.raises(ex.ExportError, match="refusing"):
        ex.check_url(url)


def test_a_dividend_adjustment_on_a_fund_marked_accumulating_refuses_the_export(tmp_path: Path) -> None:
    # e.g. Yahoo recording a dividend in pounds against a pence price: the adjustment is wrong
    days = [date(2021, 1, 4), date(2022, 1, 4), date(2023, 1, 4)]
    p = {
        "AAA.L": yahoo(days, [100.0, 100.0, 100.0], adj=[90.0, 95.0, 100.0]),
        "AAA.LSE": eodhd(days, [100.0, 100.0, 100.0]),
    }
    with pytest.raises(ex.ExportError, match="accumulating"):
        ex.export([FUND], date(2023, 1, 1), tmp_path / "export", opener(p), TODAY, eodhd_key=KEY)


def test_a_distributing_series_with_a_year_of_no_dividends_is_a_problem() -> None:
    # the average looks plausible, but 2022's dividends are missing (e.g. recorded in the wrong unit)
    s = ex.Series("A.L", "GBP", [ex.Bar(DAYS[0], 1.0, 1.0)], yield_=0.02, yearly={2021: 0.04, 2022: 0.0})
    problem = ex.yield_problem(ex.Source("A.L", "A.LSE", distributing=True), s)
    assert problem is not None and "2022" in problem


def test_yearly_yields_are_measured_from_year_end_to_year_end() -> None:
    days = [date(2020, 12, 31), date(2021, 6, 30), date(2021, 12, 31), date(2022, 12, 30)]
    body = yahoo(days, [100.0] * 4, adj=[90.0, 92.0, 94.5, 94.5])
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": body}), TODAY)
    assert s.yearly == pytest.approx({2021: 94.5 / 90 - 1, 2022: 0.0})


def test_too_many_skipped_rows_refuse_the_export(tmp_path: Path) -> None:
    body = json.loads(yahoo(DAYS, CLOSES))
    body["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][1] = None  # 1 of 5 rows: 20%
    p = {"AAA.L": json.dumps(body).encode(), "AAA.LSE": eodhd(DAYS, CLOSES)}
    with pytest.raises(ex.ExportError, match="skipped"):
        ex.export([FUND], date(2023, 1, 1), tmp_path / "export", opener(p), TODAY, eodhd_key=KEY)


def test_the_redirect_guard_is_wired_into_the_opener() -> None:
    assert any(isinstance(h, ex._SafeRedirect) for h in ex._URLOPENER.handlers)


def test_a_symbol_with_a_trailing_newline_is_refused(tmp_path: Path) -> None:
    body = (
        'hold_out_start = "2023-01-01"\n[[fund]]\nticker = "AAA\\n"\nrole = "trade"\n'
        'yahoo = "AAA.L"\neodhd = "AAA.LSE"\ndistributing = false\nproxies = []\n'
    )
    path = tmp_path / "u.toml"
    path.write_text(body)
    with pytest.raises(ex.ExportError, match="invalid"):
        ex.load_universe(path)


def test_a_bad_timestamp_is_an_export_error() -> None:
    body = json.loads(yahoo(DAYS[:1], [1.0]))
    body["chart"]["result"][0]["timestamp"] = ["soon"]
    with pytest.raises(ex.ExportError):
        ex.fetch_yahoo("AAA.L", opener({"AAA.L": json.dumps(body).encode()}), TODAY)
