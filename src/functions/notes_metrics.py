# -*- coding: utf-8 -*-
"""

Author:
    Mike Ryan
    MIT Licensed
"""

import asyncio
import datetime
import json
import statistics
import uuid
from collections import Counter, defaultdict, deque
from typing import Any

from loguru import logger
from sqlalchemy import Select, insert, text, update

from ..db_tables import (
    NOTE_METRICS_SCALAR_FIELDS,
    NoteMetrics,
    NoteMetricsScalars,
    Notes,
)
from ..functions.db_guards import is_db_error as _is_db_error
from ..functions.db_guards import safe_list as _safe_list
from ..functions.db_guards import safe_record as _safe_record
from ..resources import db_ops
from ..settings import settings


def _metrics_note_from_model(note: Notes) -> dict[str, Any]:
    return {
        "date_created": note.date_created,
        "word_count": note.word_count or 0,
        "character_count": note.character_count or 0,
        "mood": note.mood or "neutral",
        "mood_analysis": note.mood_analysis or "neutral",
        "tags": note.tags or [],
        "ai_fix": bool(note.ai_fix),
    }


def _sort_by_period(data: dict[str, Any], period_format: str) -> dict[str, Any]:
    return dict(
        sorted(
            data.items(),
            key=lambda item: datetime.datetime.strptime(item[0], period_format),
        )
    )


def _compute_writing_streaks(unique_dates: set[datetime.date]) -> dict[str, int]:
    if not unique_dates:
        return {"current": 0, "longest": 0}

    today = datetime.datetime.now(datetime.timezone.utc).date()
    current = 0
    cursor = today
    while cursor in unique_dates:
        current += 1
        cursor -= datetime.timedelta(days=1)

    longest = 0
    running = 0
    previous = None
    for day in sorted(unique_dates):
        if previous is None or (day - previous).days == 1:
            running += 1
        else:
            running = 1
        if running > longest:
            longest = running
        previous = day

    return {"current": current, "longest": longest}


def _compute_milestones(
    note_count: int, longest_streak: int, unique_days: int, notes: list[dict[str, Any]]
) -> list[str]:
    milestones = []

    note_milestones = [10, 50, 100, 250, 500, 1000, 2500, 5000]
    for threshold in note_milestones:
        if note_count >= threshold:
            milestones.append(f"{threshold} notes written")

    streak_milestones = [7, 14, 30, 60, 100, 365]
    for threshold in streak_milestones:
        if longest_streak >= threshold:
            milestones.append(f"{threshold}-day writing streak")

    day_milestones = [30, 100, 365, 730, 1000]
    for threshold in day_milestones:
        if unique_days >= threshold:
            milestones.append(f"Wrote on {threshold} unique days")

    if notes:
        first_date = min(note["date_created"] for note in notes)
        years_active = max(
            0,
            (
                datetime.datetime.now(datetime.timezone.utc).date() - first_date.date()
            ).days
            // 365,
        )
        if years_active >= 1:
            milestones.append(f"Journaling for {years_active} years")

    return milestones


class _NotesMetricsAccumulator:
    """Mutable per-note accumulators for _compute_metrics_bundle().

    Extracted so the note-aggregation loop (and its nested per-tag loop) live
    in their own low-complexity scope rather than nesting three levels deep
    inside _compute_metrics_bundle() itself.
    """

    def __init__(
        self,
        mood_values: dict[str, int],
        mood_weights_dict: dict[str, float],
        recent_start,
        prior_start,
    ):
        self.mood_values = mood_values
        self.mood_weights_dict = mood_weights_dict
        self.recent_start = recent_start
        self.prior_start = prior_start

        self.word_count = 0
        self.char_count = 0
        self.ai_fix_count = 0
        self.mood_count = Counter()
        self.raw_tag_counter = Counter()
        self.display_tag_counter = Counter()

        self.by_year = defaultdict(lambda: {"note_count": 0, "word_count": 0})
        self.by_month = defaultdict(lambda: {"note_count": 0, "word_count": 0})
        self.by_week = defaultdict(lambda: {"note_count": 0, "word_count": 0})
        self.mood_by_month_counts = defaultdict(lambda: defaultdict(int))

        self.mood_sum_by_month = defaultdict(float)
        self.mood_count_by_month = defaultdict(int)
        self.mood_values_by_month = defaultdict(list)
        self.mood_analysis_sum_by_month = defaultdict(float)
        self.mood_analysis_count_by_month = defaultdict(int)
        self.weekday_activity = defaultdict(int)
        self.hour_activity = defaultdict(int)
        self.mood_tag_counts = {
            "positive": Counter(),
            "neutral": Counter(),
            "negative": Counter(),
        }
        self.recent_tag_counts = Counter()
        self.prior_tag_counts = Counter()
        self.unique_written_dates: set[datetime.date] = set()

    def _add_tag(self, tag, mood_normalized: str, created) -> None:
        tag_text = str(tag)
        tag_lower = tag_text.lower()
        self.raw_tag_counter[tag_lower] += 1
        display_tag = tag_text.capitalize()
        self.display_tag_counter[display_tag] += 1
        self.mood_tag_counts[mood_normalized][display_tag] += 1

        if created >= self.recent_start:
            self.recent_tag_counts[display_tag] += 1
        elif created >= self.prior_start:
            self.prior_tag_counts[display_tag] += 1

    def add_note(self, note: dict[str, Any]) -> None:
        created = note["date_created"]
        note_word_count = note["word_count"] or 0
        note_char_count = note["character_count"] or 0
        mood_raw = note["mood"] or "neutral"
        mood_normalized = mood_raw.lower()
        mood_analysis = (note["mood_analysis"] or "").lower()

        if mood_normalized not in self.mood_values:
            mood_normalized = "neutral"

        tags = note.get("tags") or []
        created_date = created.date()

        self.word_count += note_word_count
        self.char_count += note_char_count
        if note.get("ai_fix"):
            self.ai_fix_count += 1

        self.mood_count[mood_raw] += 1

        year = created.strftime("%Y")
        month = created.strftime("%Y-%m")
        week_year = created.isocalendar()[0:2]
        week = f"{week_year[0]}-{week_year[1]:02d}"

        self.by_year[year]["note_count"] += 1
        self.by_year[year]["word_count"] += note_word_count

        self.by_month[month]["note_count"] += 1
        self.by_month[month]["word_count"] += note_word_count

        self.by_week[week]["note_count"] += 1
        self.by_week[week]["word_count"] += note_word_count

        self.mood_by_month_counts[month][mood_raw] += 1

        self.mood_sum_by_month[month] += self.mood_values[mood_normalized]
        self.mood_count_by_month[month] += 1
        self.mood_values_by_month[month].append(self.mood_values[mood_normalized])

        if mood_analysis in self.mood_weights_dict:
            self.mood_analysis_sum_by_month[month] += self.mood_weights_dict[
                mood_analysis
            ]
            self.mood_analysis_count_by_month[month] += 1

        self.weekday_activity[created.weekday()] += 1
        self.hour_activity[created.hour] += 1
        self.unique_written_dates.add(created_date)

        for tag in tags:
            self._add_tag(tag, mood_normalized, created)


def _compute_mean_by_month(sum_by_month: dict, count_by_month: dict) -> dict:
    mean_by_month = {
        month: round(total / count_by_month[month], 3)
        for month, total in sum_by_month.items()
    }
    return _sort_by_period(mean_by_month, "%Y-%m")


def _compute_rolling_mean_by_month(mean_by_month: dict) -> dict:
    rolling_avg = {}
    months = deque(maxlen=3)
    for month, avg in mean_by_month.items():
        months.append(avg)
        rolling_avg[month] = round(sum(months) / len(months), 3)
    return rolling_avg


def _compute_tag_trend(recent_tag_counts: Counter, prior_tag_counts: Counter) -> dict:
    tag_deltas = []
    for tag in set(recent_tag_counts) | set(prior_tag_counts):
        delta = recent_tag_counts.get(tag, 0) - prior_tag_counts.get(tag, 0)
        if delta != 0:
            tag_deltas.append((tag, delta))

    rising_tags = dict(
        sorted(
            [item for item in tag_deltas if item[1] > 0],
            key=lambda item: item[1],
            reverse=True,
        )[:5]
    )
    falling_tags = dict(
        sorted([item for item in tag_deltas if item[1] < 0], key=lambda item: item[1])[
            :5
        ]
    )
    return {"rising": rising_tags, "falling": falling_tags}


def _compute_word_trend_by_month(by_month: dict) -> dict:
    return {
        month: {
            "total_words": data["word_count"],
            "avg_words_per_note": (
                round(data["word_count"] / data["note_count"], 2)
                if data["note_count"]
                else 0
            ),
            "note_count": data["note_count"],
        }
        for month, data in by_month.items()
    }


def _compute_metrics_bundle(notes: list[dict[str, Any]]) -> dict[str, Any]:
    mood_values = {"negative": -1, "neutral": 0, "positive": 1}
    mood_weights_dict = dict(settings.mood_analysis_weights)

    latest_created = (
        max(note["date_created"] for note in notes)
        if notes
        else datetime.datetime.now(datetime.timezone.utc)
    )
    recent_start = latest_created - datetime.timedelta(days=90)
    prior_start = recent_start - datetime.timedelta(days=90)

    acc = _NotesMetricsAccumulator(
        mood_values, mood_weights_dict, recent_start, prior_start
    )
    for note in notes:
        acc.add_note(note)

    by_year = _sort_by_period(dict(acc.by_year), "%Y")
    by_month = _sort_by_period(dict(acc.by_month), "%Y-%m")
    # by_week's keys are ISO year-week (from created.isocalendar()), which
    # doesn't line up with _sort_by_period's %W (calendar-week) parsing at
    # year boundaries - e.g. "2025-52" and "2026-01" can each contain days
    # from the other's calendar year. %G-%V is the ISO equivalent format,
    # but strptime requires a %u (ISO weekday) alongside it to parse
    # unambiguously, hence the fixed "-1" (Monday).
    by_week = dict(
        sorted(
            acc.by_week.items(),
            key=lambda item: datetime.datetime.strptime(f"{item[0]}-1", "%G-%V-%u"),
        )
    )
    mood_by_month_counts = _sort_by_period(
        {k: dict(v) for k, v in acc.mood_by_month_counts.items()}, "%Y-%m"
    )

    mood_mean_by_month = _compute_mean_by_month(
        acc.mood_sum_by_month, acc.mood_count_by_month
    )
    mood_median_by_month = _sort_by_period(
        {
            month: round(statistics.median(values), 3)
            for month, values in acc.mood_values_by_month.items()
        },
        "%Y-%m",
    )
    rolling_avg = _compute_rolling_mean_by_month(mood_mean_by_month)
    mood_analysis_mean_by_month = _compute_mean_by_month(
        acc.mood_analysis_sum_by_month, acc.mood_analysis_count_by_month
    )

    tags_common = dict(
        sorted(acc.display_tag_counter.items(), key=lambda item: item[1], reverse=True)[
            :30
        ]
    )

    weekday_labels = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    activity_by_day = {
        weekday_labels[index]: acc.weekday_activity.get(index, 0) for index in range(7)
    }

    activity_by_hour = {
        f"{hour:02d}": acc.hour_activity.get(hour, 0) for hour in range(24)
    }

    mood_tag_correlation = {
        mood: dict(counter.most_common(10))
        for mood, counter in acc.mood_tag_counts.items()
    }

    tag_trend = _compute_tag_trend(acc.recent_tag_counts, acc.prior_tag_counts)

    mood_stability_score = (
        round(statistics.pstdev(mood_mean_by_month.values()), 3)
        if len(mood_mean_by_month) > 1
        else 0.0
    )

    writing_streak = _compute_writing_streaks(acc.unique_written_dates)
    milestones = _compute_milestones(
        note_count=len(notes),
        longest_streak=writing_streak["longest"],
        unique_days=len(acc.unique_written_dates),
        notes=notes,
    )

    word_trend_by_month = _compute_word_trend_by_month(by_month)

    metrics = {
        "note_count_by_year": json.dumps(by_year),
        "note_count_by_month": json.dumps(by_month),
        "note_count_by_week": json.dumps(by_week),
        "mood_by_month": mood_by_month_counts,
        "mood_trend_by_month": mood_mean_by_month,
        "mood_trend_by_median_month": mood_median_by_month,
        "mood_trend_by_rolling_mean_month": rolling_avg,
        "mood_analysis_trend_by_mean_month": mood_analysis_mean_by_month,
        "tags_common": json.dumps(tags_common),
        "writing_streak": writing_streak,
        "activity_by_day_of_week": activity_by_day,
        "activity_by_hour": activity_by_hour,
        "mood_tag_correlation": mood_tag_correlation,
        "tag_trend": tag_trend,
        "mood_stability_score": mood_stability_score,
        "milestones": milestones,
        "word_trend_by_month": word_trend_by_month,
    }

    return {
        "note_count": len(notes),
        "word_count": acc.word_count,
        "char_count": acc.char_count,
        "mood_metric": dict(acc.mood_count),
        "total_unique_tag_count": len(acc.raw_tag_counter),
        "ai_fix_count": acc.ai_fix_count,
        "metrics": metrics,
    }


# Maps a NOTE_METRICS_SCALAR_FIELDS name to its key in _compute_metrics_bundle()'s
# return dict, for the one field whose name differs between the two
# (character_count here vs. char_count there); every other field name matches.
_BUNDLE_KEY_BY_FIELD = {"character_count": "char_count"}


async def update_notes_metrics(user_id: str):
    logger.debug("background task for metrics")
    started_at = datetime.datetime.now(datetime.timezone.utc)

    query_metric = Select(NoteMetrics).where(NoteMetrics.user_id == user_id)
    metric_data = _safe_record(await db_ops.read_one_record(query=query_metric))

    query = Select(Notes).where(Notes.user_id == user_id).limit(10000).offset(0)
    # read_query() returns a DatabaseErrorResult (a dict) on failure rather
    # than raising - iterating that directly yields its string keys, which
    # is what produced the "'str' object has no attribute 'date_created'"
    # crash here when the underlying query failed (e.g. a table missing).
    notes = _safe_list(await db_ops.read_query(query=query))
    notes = [_metrics_note_from_model(note) for note in notes]
    bundle = _compute_metrics_bundle(notes=notes)

    is_postgres = settings.is_postgres
    # On Postgres, the scalar fields below live in the note_metrics_scalars
    # materialized view (derived straight from `notes`, refreshed below) -
    # not in this table. On SQLite they stay ordinary stored columns.
    # Field names are sourced from NOTE_METRICS_SCALAR_FIELDS (db_tables.py)
    # rather than repeated here, so this dict, the zero-value fallback in
    # get_or_refresh() below, and the column definitions it mirrors can't
    # silently drift apart; `_BUNDLE_KEY_BY_FIELD` covers the one field
    # whose name differs in `bundle` itself (character_count/char_count).
    scalar_values = (
        {}
        if is_postgres
        else {
            field: bundle[_BUNDLE_KEY_BY_FIELD.get(field, field)]
            for field in NOTE_METRICS_SCALAR_FIELDS
        }
    )

    if metric_data is None:
        write_query = insert(NoteMetrics).values(
            pkid=str(uuid.uuid4()),
            metrics=bundle["metrics"],
            user_id=user_id,
            **scalar_values,
        )
    else:
        write_query = (
            update(NoteMetrics)
            .where(NoteMetrics.pkid == metric_data.pkid)
            .values(metrics=bundle["metrics"], user_id=user_id, **scalar_values)
        )

    if is_postgres:
        # Materialized views don't auto-update - this is the only place
        # note_metrics_scalars' data ever changes. Plain (non-CONCURRENT)
        # refresh: simpler than CONCURRENTLY (which needs its own unique-
        # index maintenance ceremony - the unique index created alongside
        # the view keeps that upgrade path open), and cheap at current
        # scale. Takes an ACCESS EXCLUSIVE lock on the view for its
        # duration, briefly blocking concurrent reads of it - acceptable
        # for a single-user app; revisit if that ever changes. Run
        # concurrently with the NoteMetrics write below - the view is
        # derived straight from `notes`, not from the NoteMetrics row, so
        # the two have no data dependency on each other and don't need to
        # be serialized into two round trips.
        result, refresh_result = await asyncio.gather(
            db_ops.execute_one(write_query),
            db_ops.execute_one(text("REFRESH MATERIALIZED VIEW note_metrics_scalars")),
        )
        if _is_db_error(refresh_result):
            logger.error(
                "Failed to refresh note_metrics_scalars for user {}: {}",
                user_id,
                refresh_result,
            )
    else:
        result = await db_ops.execute_one(write_query)

    logger.debug(result)
    elapsed_ms = int(
        (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
        * 1000
    )
    logger.info("Note metrics updated for user {} in {}ms", user_id, elapsed_ms)


def _merge_scalar_metrics(result: dict, scalars: dict | None) -> dict:
    """
    Merges NoteMetricsScalars' scalar fields into `result` in place (or the
    zero-value fallback shape when `scalars` is None - a user with zero
    notes never appears in the view's GROUP BY output, the same shape
    _compute_metrics_bundle() would produce for an empty note list).

    Pulled out of get_or_refresh() as a plain-dict function so this merge -
    the exact place a field-name mismatch between NOTE_METRICS_SCALAR_FIELDS
    and the view would surface - can be unit-tested without a live
    Postgres-backed NoteMetricsScalars query (that class is only defined at
    all when settings.is_postgres was true at db_tables.py import time,
    which pytest's memory-driver test suite never is).
    """
    result.update(
        scalars
        if scalars is not None
        else {
            field: {} if field == "mood_metric" else 0
            for field in NOTE_METRICS_SCALAR_FIELDS
        }
    )
    return result


async def get_or_refresh(user_id: str) -> dict | None:
    """
    Fetch the user's note metrics, computing them for the first time (via
    update_notes_metrics()) if they don't exist yet.

    On Postgres, the plain scalar aggregates (word_count, note_count, etc.)
    live in the note_metrics_scalars materialized view rather than on
    NoteMetrics itself (see db_tables.py) - this merges both sources so
    callers see the same flat shape regardless of dialect.

    Returns:
        A dict of the combined fields, or None if metrics still couldn't be
        loaded after attempting to compute them (e.g. a DB error on insert).
    """
    query = Select(NoteMetrics).where(NoteMetrics.user_id == user_id)

    async def _fetch():
        # On Postgres, these are two independent per-user reads (one row,
        # one materialized-view row) with no data dependency on each other -
        # fire them concurrently rather than paying two serial round trips
        # on every dashboard/metrics-counts load.
        if settings.is_postgres:
            scalars_query = Select(NoteMetricsScalars).where(
                NoteMetricsScalars.user_id == user_id
            )
            note_metrics_raw, scalars_raw = await asyncio.gather(
                db_ops.read_one_record(query=query),
                db_ops.read_one_record(query=scalars_query),
            )
            return _safe_record(note_metrics_raw), _safe_record(scalars_raw)
        return _safe_record(await db_ops.read_one_record(query=query)), None

    note_metrics, scalars = await _fetch()

    if note_metrics is None:
        await update_notes_metrics(user_id=user_id)
        # Re-fetch both: the row created above and, on Postgres, the view
        # data that update_notes_metrics() just refreshed - the `scalars`
        # from the fetch above (taken before that row existed) is stale.
        note_metrics, scalars = await _fetch()

    if note_metrics is None:
        return None

    result = note_metrics.to_dict()

    if settings.is_postgres:
        _merge_scalar_metrics(
            result, scalars.to_dict() if scalars is not None else None
        )

    return result
