# -*- coding: utf-8 -*-
"""
List-page filter helpers: the inclusive date range, bounded paging params,
and the "today in history" window (src/functions/date_functions.py,
src/endpoints/notes.py, src/endpoints/web_links.py).
"""

from datetime import datetime

import pytest

from src.endpoints.notes import history_month_days
from src.functions.date_functions import filter_date_range


def test_end_date_is_inclusive():
    start, end = filter_date_range("2024-03-01", "2024-03-31")
    assert start == datetime(2024, 3, 1)
    # Half-open: everything on Mar 31 is < the bound, Apr 1 00:00 is not.
    assert datetime(2024, 3, 31, 23, 59, 59) < end
    assert end == datetime(2024, 4, 1)


def test_open_ended_ranges_use_sensible_defaults():
    start, end = filter_date_range("", "")
    assert start == datetime(2011, 1, 1)
    assert end > datetime.now()
    start, _ = filter_date_range("2024-05-05", None)
    assert start == datetime(2024, 5, 5)


@pytest.mark.parametrize("bad", ["2024-1-1x", "tomorrow", "31/03/2024", "2024-13-01"])
def test_invalid_dates_raise(bad):
    with pytest.raises(ValueError):
        filter_date_range(bad, "")


def test_history_window_crosses_a_month_end():
    # The old month/day BETWEEN logic was empty here: Dec 30 +/- 7 days is
    # "day BETWEEN 23 AND 6".
    days = history_month_days(datetime(2024, 12, 30), 7)
    assert len(days) == 15
    assert days[0] == "12-23"
    assert "12-31" in days and "01-01" in days
    assert days[-1] == "01-06"


def test_history_window_handles_leap_day():
    assert "02-29" in history_month_days(datetime(2024, 3, 1), 2)


def test_history_window_of_zero_is_just_today():
    assert history_month_days(datetime(2024, 6, 15), 0) == ["06-15"]


@pytest.mark.parametrize("path", ["/notes/pagination", "/weblinks/pagination"])
@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"page": 0}, {"page": -3}, {"limit": 100000}]
)
def test_paging_params_are_bounded(logged_in_client, path, params):
    response = logged_in_client.get(
        path, params=params, headers={"Accept": "application/json"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/notes/pagination", "/weblinks/pagination"])
def test_bad_dates_are_a_client_error_not_a_500(logged_in_client, path):
    response = logged_in_client.get(
        path,
        params={"start_date": "2024-1-1x", "end_date": "nope"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400


def _create_note_with_summary(client, summary):
    created = client.post(
        "/notes/new",
        data={"mood": "positive", "note": f"body for {summary}"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    note_id = created.headers["location"].rstrip("/").rsplit("/", 1)[-1]
    # The list pages render only the (short) summary, which the test AI
    # never fills in - set it by hand, as test_notes.py does.
    edited = client.post(
        f"/notes/edit/{note_id}", data={"summary": summary}, follow_redirects=False
    )
    assert edited.status_code == 303


def test_end_date_includes_notes_created_that_day(logged_in_client):
    _create_note_with_summary(logged_in_client, "range-inclusive-token")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    response = logged_in_client.get(
        "/notes/pagination", params={"start_date": today, "end_date": today}
    )

    assert response.status_code == 200
    assert "range-inclusive-token" in response.text


def test_notes_today_returns_a_note_created_today(logged_in_client):
    _create_note_with_summary(logged_in_client, "history-window-token")

    response = logged_in_client.get("/notes/today")

    assert response.status_code == 200
    assert "history-window-token" in response.text
