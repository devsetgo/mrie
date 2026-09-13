# -*- coding: utf-8 -*-
"""

Author:
    Mike Ryan
    MIT Licensed
"""

import datetime
import json
import statistics
import uuid
from collections import Counter, defaultdict, deque
from typing import Any

from loguru import logger
from sqlalchemy import Select, insert, update

from ..db_tables import NoteMetrics, Notes
from ..functions.db_guards import safe_list as _safe_list
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
    by_week = _sort_by_period(dict(acc.by_week), "%Y-%W")
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


async def update_notes_metrics(user_id: str):
    logger.debug("background task for metrics")
    started_at = datetime.datetime.now(datetime.timezone.utc)

    query_metric = Select(NoteMetrics).where(NoteMetrics.user_id == user_id)
    metric_data = await db_ops.read_one_record(query=query_metric)

    query = Select(Notes).where(Notes.user_id == user_id).limit(10000).offset(0)
    # read_query() returns a DatabaseErrorResult (a dict) on failure rather
    # than raising - iterating that directly yields its string keys, which
    # is what produced the "'str' object has no attribute 'date_created'"
    # crash here when the underlying query failed (e.g. a table missing).
    notes = _safe_list(await db_ops.read_query(query=query))
    notes = [_metrics_note_from_model(note) for note in notes]
    bundle = _compute_metrics_bundle(notes=notes)

    if metric_data is None:
        result = await db_ops.execute_one(
            insert(NoteMetrics).values(
                pkid=str(uuid.uuid4()),
                word_count=bundle["word_count"],
                note_count=bundle["note_count"],
                character_count=bundle["char_count"],
                mood_metric=bundle["mood_metric"],
                total_unique_tag_count=bundle["total_unique_tag_count"],
                metrics=bundle["metrics"],
                ai_fix_count=bundle["ai_fix_count"],
                user_id=user_id,
            )
        )
    else:
        note_metrics = {
            "word_count": bundle["word_count"],
            "note_count": bundle["note_count"],
            "character_count": bundle["char_count"],
            "mood_metric": bundle["mood_metric"],
            "total_unique_tag_count": bundle["total_unique_tag_count"],
            "metrics": bundle["metrics"],
            "user_id": user_id,
            "ai_fix_count": bundle["ai_fix_count"],
        }

        # Update the database
        result = await db_ops.execute_one(
            update(NoteMetrics)
            .where(NoteMetrics.pkid == metric_data.pkid)
            .values(**note_metrics)
        )

    logger.debug(result)
    elapsed_ms = int(
        (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
        * 1000
    )
    logger.info("Note metrics updated for user {} in {}ms", user_id, elapsed_ms)
