"""Offline tests for scripts/export_history.py: fake Yahoo and Stooq responses, no network."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
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


def stooq(days: list[date], closes: list[float]) -> bytes:
    rows = "".join(f"{d},{c},{c},{c},{c},0\n" for d, c in zip(days, closes, strict=True))
    return ("Date,Open,High,Low,Close,Volume\n" + rows).encode()


def opener(pages: dict[str, bytes]):  # type: ignore[no-untyped-def]
    def fetch(url: str) -> bytes:
        for key, body in pages.items():
            if key in url:
                return body
        raise ex.ExportError(f"unexpected URL {url}")

    return fetch


SRC = ex.Source("AAA.L", "aaa.uk", distributing=False)
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


@pytest.mark.parametrize(
    ("field", "value"), [("ticker", "../../x"), ("ticker", "/home/u/x"), ("yahoo", "A.L/../..")]
)
def test_unsafe_tickers_or_symbols_are_refused(tmp_path: Path, field: str, value: str) -> None:
    entry = {"ticker": "AAA", "role": "trade", "yahoo": "AAA.L", "stooq": "aaa.uk", "distributing": False} | {
        field: value
    }
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


def test_a_second_source_covering_too_little_is_a_problem() -> None:
    problems = ex.check_problems("AAA.L", ex.compare(BARS, {b.day: b.close for b in BARS[-2:]}))
    assert any("covers" in p for p in problems)


# --- export --------------------------------------------------------------------------------
def pages(stooq_closes: list[float] | None = None) -> dict[str, bytes]:
    return {"AAA.L": yahoo(DAYS, CLOSES), "aaa.uk": stooq(DAYS, stooq_closes or CLOSES)}


def test_export_splits_dev_and_hold_out_at_the_boundary_with_a_manifest(tmp_path: Path) -> None:
    out = tmp_path / "export"
    manifest = ex.export([FUND], date(2023, 1, 1), out, opener(pages()), TODAY)
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
        ex.export([FUND], date(2023, 1, 1), tmp_path, opener(pages()), TODAY)
    assert (tmp_path / "keep.csv").read_text() == "mine"


def test_a_disagreement_writes_nothing_and_touches_nothing_else(tmp_path: Path) -> None:
    (tmp_path / "other.csv").write_text("someone else's")
    out = tmp_path / "export"
    with pytest.raises(ex.ExportError, match="disagree"):
        ex.export([FUND], date(2023, 1, 1), out, opener(pages([100.0, 120.0, 90.0, 103.0, 104.0])), TODAY)
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.csv"]  # no temp folder left either


def test_a_failure_part_way_leaves_no_partial_export(tmp_path: Path) -> None:
    second = ex.Fund("BBB", "trade", ex.Source("BBB.L", "bbb.uk", distributing=False), [])
    out = tmp_path / "export"
    with pytest.raises(ex.ExportError):
        ex.export([FUND, second], date(2023, 1, 1), out, opener(pages()), TODAY)  # BBB has no pages
    assert not out.exists() and list(tmp_path.iterdir()) == []


def test_an_override_writes_and_records_the_problems(tmp_path: Path) -> None:
    out = tmp_path / "export"
    manifest = ex.export(
        [FUND],
        date(2023, 1, 1),
        out,
        opener(pages([100.0, 120.0, 90.0, 103.0, 104.0])),
        TODAY,
        accept_problems=True,
    )
    assert manifest["accept_problems"] is True and manifest["problems"]


def test_proxies_are_cross_checked_too(tmp_path: Path) -> None:
    proxy = ex.Source("OLD.L", "old.uk", distributing=False)
    fund = ex.Fund("AAA", "trade", ex.Source("AAA.L", "aaa.uk", distributing=False), [proxy])
    old_days, old = DAYS[:3], [50.0, 51.0, 51.5]
    p = pages() | {"OLD.L": yahoo(old_days, old), "old.uk": stooq(old_days, [50.0, 70.0, 51.5])}
    with pytest.raises(ex.ExportError, match=r"OLD\.L"):
        ex.export([fund], date(2023, 1, 1), tmp_path / "export", opener(p), TODAY)


@pytest.mark.parametrize("inside", ["history", "research/data"])
def test_the_export_refuses_any_folder_inside_the_repo(
    capsys: pytest.CaptureFixture[str], inside: str
) -> None:
    assert ex.main(["--out", str(ROOT / inside)], opener=opener({})) == 1
    assert "inside the repository" in capsys.readouterr().err


def test_list_mode_reports_both_sources_and_failures() -> None:
    funds = [ex.Fund("AAA", "trade", SRC, [ex.Source("OLD.L", "old.uk", distributing=True)])]
    lines = ex.list_symbols(
        funds, opener({"AAA.L": yahoo(DAYS[:2], [1.0, 2.0]), "aaa.uk": stooq(DAYS[:2], [1.0, 2.0])}), TODAY
    )
    assert any("OLD.L" in line and "FAILED" in line for line in lines)
    assert any("AAA.L" in line and "2022-12-29 .. 2022-12-30" in line for line in lines)
    assert any("aaa.uk" in line and "2022-12-29 .. 2022-12-30" in line for line in lines)


@pytest.mark.parametrize("url", ["http://stooq.com/x", "https://evil.example/x", "file:///etc/passwd"])
def test_only_https_on_the_two_data_hosts_is_fetched_including_redirects(url: str) -> None:
    with pytest.raises(ex.ExportError, match="refusing"):
        ex.check_url(url)


def test_a_dividend_adjustment_on_a_fund_marked_accumulating_refuses_the_export(tmp_path: Path) -> None:
    # e.g. Yahoo recording a dividend in pounds against a pence price: the adjustment is wrong
    days = [date(2021, 1, 4), date(2022, 1, 4), date(2023, 1, 4)]
    p = {
        "AAA.L": yahoo(days, [100.0, 100.0, 100.0], adj=[90.0, 95.0, 100.0]),
        "aaa.uk": stooq(days, [100.0, 100.0, 100.0]),
    }
    with pytest.raises(ex.ExportError, match="accumulating"):
        ex.export([FUND], date(2023, 1, 1), tmp_path / "export", opener(p), TODAY)
