"""Offline tests for scripts/export_history.py: fake Yahoo and Stooq responses, no network."""

from __future__ import annotations

import importlib.util
import json
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
DAYS = [date(2022, 12, 28), date(2022, 12, 29), date(2022, 12, 30), date(2023, 1, 3), date(2023, 1, 4)]


def stamp(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 8, 0, tzinfo=UTC).timestamp())


def yahoo(
    days: list[date],
    closes: list[float],
    adj: list[float] | None = None,
    currency: str = "GBP",
    opens: list[float] | None = None,
) -> bytes:
    return json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": currency},
                        "timestamp": [stamp(d) for d in days],
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
    return (
        "Date,Open,High,Low,Close,Volume\n"
        + "".join(f"{d},{c},{c},{c},{c},0\n" for d, c in zip(days, closes, strict=True))
    ).encode()


def opener(pages: dict[str, bytes]):  # type: ignore[no-untyped-def]
    def fetch(url: str) -> bytes:
        for key, body in pages.items():
            if key in url:
                return body
        raise AssertionError(f"unexpected URL {url}")

    return fetch


FUND = ex.Fund("AAA", "trade", "AAA.L", [], "aaa.uk")


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


def test_adjusted_closes_are_used_and_opens_adjusted_by_the_same_factor() -> None:
    s = ex.fetch_yahoo(
        "AAA.L", opener({"AAA.L": yahoo(DAYS[:2], [100.0, 110.0], adj=[50.0, 55.0], opens=[98.0, 108.0])})
    )
    assert [(b.open, b.close) for b in s.bars] == [(49.0, 50.0), (54.0, 55.0)]


def test_yahoo_pence_is_gbx_and_null_rows_are_skipped() -> None:
    body = json.loads(yahoo(DAYS[:3], [100.0, 101.0, 102.0], currency="GBp"))
    body["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
    s = ex.fetch_yahoo("AAA.L", opener({"AAA.L": json.dumps(body).encode()}))
    assert s.currency == "GBX" and [b.day for b in s.bars] == [DAYS[0], DAYS[2]]


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"chart": {"result": []}}',
        json.dumps({"chart": {"result": [{"meta": {"currency": "USD"}}]}}).encode(),
    ],
)
def test_an_unexpected_yahoo_response_is_an_error(body: bytes) -> None:
    with pytest.raises(ex.ExportError):
        ex.fetch_yahoo("AAA.L", opener({"AAA.L": body}))


def test_a_proxy_is_spliced_under_the_fund_scaled_at_the_first_common_day() -> None:
    primary = ex.Series("NEW.L", "GBP", [ex.Bar(DAYS[2], 20.0, 20.0), ex.Bar(DAYS[3], 21.0, 21.0)])
    proxy = ex.Series(
        "OLD.L", "GBX", [ex.Bar(d, c, c) for d, c in zip(DAYS[:3], [900.0, 950.0, 1000.0], strict=True)]
    )
    bars, splices = ex.splice(primary, [proxy])
    assert [b.close for b in bars] == pytest.approx([18.0, 19.0, 20.0, 21.0])  # 1000p = £10, scaled x2
    assert splices == [{"proxy": "OLD.L", "until": "2022-12-30", "ratio": "2.000000"}]


def test_a_proxy_without_the_splice_day_is_an_error() -> None:
    primary = ex.Series("NEW.L", "GBP", [ex.Bar(DAYS[3], 20.0, 20.0)])
    proxy = ex.Series("OLD.L", "GBP", [ex.Bar(DAYS[0], 10.0, 10.0)])
    with pytest.raises(ex.ExportError, match="no bar on"):
        ex.splice(primary, [proxy])


def test_the_second_source_comparison_counts_disagreeing_days() -> None:
    bars = [ex.Bar(d, c, c) for d, c in zip(DAYS, [100.0, 101.0, 102.0, 103.0, 104.0], strict=True)]
    same = ex.compare(bars, {d: b.close * 2 for d, b in zip(DAYS, bars, strict=True)})  # scale doesn't matter
    off = ex.compare(bars, dict(zip(DAYS, [100.0, 101.0, 110.0, 103.0, 104.0], strict=True)))
    assert (same["disagreeing_days"], same["common_days"]) == (0, 4)
    assert off["disagreeing_days"] == 2


def test_export_writes_dev_and_hold_out_separately_with_a_manifest(tmp_path: Path) -> None:
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    pages = {"AAA.L": yahoo(DAYS, closes), "aaa.uk": stooq(DAYS, closes)}
    manifest = ex.export([FUND], date(2023, 1, 1), tmp_path, opener(pages))
    dev = (tmp_path / "dev" / "AAA.csv").read_text().splitlines()
    held = (tmp_path / "holdout" / "AAA.csv").read_text().splitlines()
    assert dev[0] == "date,open,close,currency" and len(dev) == 4 and len(held) == 3
    assert all(line.startswith("2022") for line in dev[1:]) and all(
        line.startswith("2023") for line in held[1:]
    )
    info = manifest["funds"]["AAA"]
    assert (info["dev_rows"], info["hold_out_rows"]) == (3, 2)
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["funds"]["AAA"]["dev_sha256"]
        == info["dev_sha256"]
    )


def test_export_refuses_to_write_when_the_sources_disagree(tmp_path: Path) -> None:
    pages = {
        "AAA.L": yahoo(DAYS, [100.0, 101.0, 102.0, 103.0, 104.0]),
        "aaa.uk": stooq(DAYS, [100.0, 120.0, 90.0, 103.0, 104.0]),
    }
    with pytest.raises(ex.ExportError, match="disagree"):
        ex.export([FUND], date(2023, 1, 1), tmp_path, opener(pages))
    assert list(tmp_path.rglob("*.csv")) == [] and not (tmp_path / "manifest.json").exists()
    manifest = ex.export([FUND], date(2023, 1, 1), tmp_path, opener(pages), accept_disagreement=True)
    assert manifest["accept_disagreement"] is True and manifest["problems"]


def test_the_export_refuses_an_output_folder_inside_the_repo(capsys: pytest.CaptureFixture[str]) -> None:
    assert ex.main(["--out", str(ROOT / "history")], opener=opener({})) == 1
    assert "inside the repository" in capsys.readouterr().err


def test_list_mode_reports_each_symbols_range_and_failures() -> None:
    funds = [ex.Fund("AAA", "trade", "AAA.L", ["OLD.L"], "aaa.uk")]
    lines = ex.list_symbols(funds, opener({"AAA.L": yahoo(DAYS[:2], [1.0, 2.0]), "OLD.L": b"{}"}))
    assert lines[0].startswith("AAA   OLD.L      FAILED")
    assert "GBP 2022-12-28 .. 2022-12-29 (2 bars)" in lines[1]
