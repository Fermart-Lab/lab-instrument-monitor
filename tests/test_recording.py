"""Exercise recording output without connecting lab equipment."""

import csv

import pytest
from openpyxl import load_workbook

from surface_conductivity_monitor.recording import (
    RecordingTarget,
    append_recording_rows,
    finalize_recording_spool,
    parse_recording_target,
    prepare_recording_spool,
)


def test_csv_recording_preserves_headers_and_samples(tmp_path):
    target = RecordingTarget(tmp_path / "samples.csv", "csv")
    spool = prepare_recording_spool(target, ["Time", "Voltage (V)"])
    append_recording_rows(spool, [["12:00:00", 1.25], ["12:00:01", -0.5]])
    finalize_recording_spool(spool)
    with target.path.open(newline="", encoding="utf-8") as stream:
        assert list(csv.reader(stream)) == [
            ["Time", "Voltage (V)"], ["12:00:00", "1.25"], ["12:00:01", "-0.5"]
        ]


def test_excel_recording_finalizes_and_removes_spool(tmp_path):
    target = RecordingTarget(tmp_path / "samples.xlsx", "xlsx")
    spool = prepare_recording_spool(target, ["Voltage (V)"])
    append_recording_rows(spool, [[1.25]])
    finalize_recording_spool(spool)
    assert not spool.temp_path.exists()
    workbook = load_workbook(target.path, read_only=True)
    try:
        assert list(workbook.active.values) == [("Voltage (V)",), ("1.25",)]
    finally:
        workbook.close()


def test_recording_path_without_extension_defaults_to_csv(tmp_path):
    target = parse_recording_target(str(tmp_path / "samples"))
    assert target.path == tmp_path / "samples.csv"
    assert target.format == "csv"


def test_empty_recording_path_is_rejected():
    with pytest.raises(RuntimeError, match="Enter a recording file path"):
        parse_recording_target("  ")
