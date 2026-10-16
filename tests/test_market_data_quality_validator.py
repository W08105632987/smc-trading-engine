from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import DataSource
from market_data.quality.data_validator import (
    DataQualityStatus,
    RawCandleRecord,
    validate_raw_dataset,
)


def _dt(minutes: int) -> datetime:
    return datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _rec(o, h, l, c, to, tc, symbol="XAUUSD", timeframe=Timeframe.M5) -> RawCandleRecord:
    return RawCandleRecord(
        symbol=symbol, timeframe=timeframe, open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), timestamp_open=to, timestamp_close=tc,
        source=DataSource.BROKER_NATIVE,
    )


def test_empty_input_returns_empty_list():
    assert validate_raw_dataset([]) == []


def test_valid_record_reports_valid():
    records = [_rec(100, 101, 99, 100.5, _dt(0), _dt(5))]
    findings = validate_raw_dataset(records)
    assert len(findings) == 1
    assert findings[0].status == DataQualityStatus.VALID
    assert findings[0].candle is not None


def test_bad_ohlc_reports_invalid_without_raising():
    # high < close is impossible; Candle's own validator rejects it —
    # this module must catch that, not propagate the exception.
    records = [_rec(100, 90, 99, 100.5, _dt(0), _dt(5))]
    findings = validate_raw_dataset(records)
    assert findings[0].status == DataQualityStatus.INVALID
    assert findings[0].candle is None
    assert "high" in findings[0].message.lower()


def test_identical_duplicate_flagged_duplicate_keeping_the_first():
    base = _rec(100, 101, 99, 100.5, _dt(0), _dt(5))
    identical_copy = _rec(100, 101, 99, 100.5, _dt(0), _dt(5))
    findings = validate_raw_dataset([base, identical_copy])
    assert findings[0].status == DataQualityStatus.VALID
    assert findings[1].status == DataQualityStatus.DUPLICATE


def test_conflicting_same_key_records_are_never_silently_resolved():
    a = _rec(100, 101, 99, 100.5, _dt(0), _dt(5))
    b = _rec(100, 105, 99, 104, _dt(0), _dt(5))  # same key, different values
    findings = validate_raw_dataset([a, b])
    assert findings[0].status == DataQualityStatus.CONFLICT
    assert findings[1].status == DataQualityStatus.CONFLICT


def test_gap_detected_between_non_adjacent_candles():
    first = _rec(100, 101, 99, 100.5, _dt(0), _dt(5))
    later = _rec(100, 102, 99, 101, _dt(20), _dt(25))  # opens well after `first` closed
    findings = validate_raw_dataset([first, later])
    assert findings[0].status == DataQualityStatus.VALID
    assert findings[1].status == DataQualityStatus.GAP


def test_adjacent_candles_with_no_gap_are_both_valid():
    first = _rec(100, 101, 99, 100.5, _dt(0), _dt(5))
    second = _rec(100.5, 101.5, 100, 101, _dt(5), _dt(10))  # opens exactly when `first` closed
    findings = validate_raw_dataset([first, second])
    assert findings[0].status == DataQualityStatus.VALID
    assert findings[1].status == DataQualityStatus.VALID


def test_findings_are_returned_in_original_record_order():
    records = [
        _rec(100, 90, 99, 100.5, _dt(0), _dt(5)),      # INVALID
        _rec(100, 101, 99, 100.5, _dt(10), _dt(15)),   # VALID
    ]
    findings = validate_raw_dataset(records)
    assert [f.record_index for f in findings] == [0, 1]
    assert findings[0].status == DataQualityStatus.INVALID
    assert findings[1].status == DataQualityStatus.VALID
