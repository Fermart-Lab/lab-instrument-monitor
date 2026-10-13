"""Tests for main module."""

from collections import deque
from datetime import datetime, timedelta

from src.main import extract_numeric_value, trim_points


def test_extract_numeric_value_returns_first_number():
    assert extract_numeric_value("DCV:+1.2345 V") == 1.2345


def test_extract_numeric_value_returns_none_if_no_number():
    assert extract_numeric_value("NO DATA") is None


def test_trim_points_keeps_only_recent_window():
    now = datetime.now()
    points = deque(
        [
            (now - timedelta(seconds=120), 1.0),
            (now - timedelta(seconds=10), 2.0),
        ]
    )

    trim_points(points, window_seconds=60)

    assert len(points) == 1
    assert points[0][1] == 2.0
