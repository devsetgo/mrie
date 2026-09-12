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
            (datetime.datetime.now(datetime.timezone.utc).date() - first_date.date()).days
            // 365,
        )
        if years_active >= 1:
            milestones.append(f"Journaling for {years_active} years")

    return milestones


async def _compute_metrics_bundle(notes: list[dict[str, Any]]) -> dict[str, Any]:
    mood_values = {"negative": -1, "neutral": 0, "positive": 1}
    mood_weights_dict = dict(settings.mood_analysis_weights)

    word_count = 0
    char_count = 0
    ai_fix_count = 0
    mood_count = Counter()
    raw_tag_counter = Counter()
    display_tag_counter = Counter()

    by_year = defaultdict(lambda: {"note_count": 0, "word_count": 0})
    by_month = defaultdict(lambda: {"note_count": 0, "word_count": 0})
    by_week = defaultdict(lambda: {"note_count": 0, "word_count": 0})
    mood_by_month_counts = defaultdict(lambda: defaultdict(int))

    mood_sum_by_month = defaultdict(float)
    mood_count_by_month = defaultdict(int)
    mood_values_by_month = defaultdict(list)
    mood_analysis_sum_by_month = defaultdict(float)
    mood_analysis_count_by_month = defaultdict(int)
    weekday_activity = defaultdict(int)
    hour_activity = defaultdict(int)
    mood_tag_counts = {
        "positive": Counter(),
        "neutral": Counter(),
        "negative": Counter(),
    }
    recent_tag_counts = Counter()
    prior_tag_counts = Counter()
    unique_written_dates: set[datetime.date] = set()

    latest_created = (
        max(note["date_created"] for note in notes)
        if notes
        else datetime.datetime.now(datetime.timezone.utc)
    )
    recent_start = latest_created - datetime.timedelta(days=90)
    prior_start = recent_start - datetime.timedelta(days=90)

    for note in notes:
        created = note["date_created"]
        note_word_count = note["word_count"] or 0
        note_char_count = note["character_count"] or 0
        mood_raw = note["mood"] or "neutral"
        mood_normalized = mood_raw.lower()
        mood_analysis = (note["mood_analysis"] or "").lower()

        if mood_normalized not in mood_values:
            mood_normalized = "neutral"

        tags = note.get("tags") or []
        created_date = created.date()

        word_count += note_word_count
        char_count += note_char_count
        if note.get("ai_fix"):
            ai_fix_count += 1

        mood_count[mood_raw] += 1

        year = created.strftime("%Y")
        month = created.strftime("%Y-%m")
        week_year = created.isocalendar()[0:2]
        week = f"{week_year[0]}-{week_year[1]:02d}"

        by_year[year]["note_count"] += 1
        by_year[year]["word_count"] += note_word_count

        by_month[month]["note_count"] += 1
        by_month[month]["word_count"] += note_word_count

        by_week[week]["note_count"] += 1
        by_week[week]["word_count"] += note_word_count

        mood_by_month_counts[month][mood_raw] += 1

        mood_sum_by_month[month] += mood_values[mood_normalized]
        mood_count_by_month[month] += 1
        mood_values_by_month[month].append(mood_values[mood_normalized])

        if mood_analysis in mood_weights_dict:
            mood_analysis_sum_by_month[month] += mood_weights_dict[mood_analysis]
            mood_analysis_count_by_month[month] += 1

        weekday_activity[created.weekday()] += 1
        hour_activity[created.hour] += 1
        unique_written_dates.add(created_date)

        for tag in tags:
            tag_text = str(tag)
            tag_lower = tag_text.lower()
            raw_tag_counter[tag_lower] += 1
            display_tag = tag_text.capitalize()
            display_tag_counter[display_tag] += 1
            mood_tag_counts[mood_normalized][display_tag] += 1

            if created >= recent_start:
                recent_tag_counts[display_tag] += 1
            elif created >= prior_start:
                prior_tag_counts[display_tag] += 1

    by_year = _sort_by_period(dict(by_year), "%Y")
    by_month = _sort_by_period(dict(by_month), "%Y-%m")
    by_week = _sort_by_period(dict(by_week), "%Y-%W")
    mood_by_month_counts = _sort_by_period(
        {k: dict(v) for k, v in mood_by_month_counts.items()}, "%Y-%m"
    )

    mood_mean_by_month = {}
    for month, total in mood_sum_by_month.items():
        mood_mean_by_month[month] = round(total / mood_count_by_month[month], 3)
    mood_mean_by_month = _sort_by_period(mood_mean_by_month, "%Y-%m")

    mood_median_by_month = {}
    for month, values in mood_values_by_month.items():
        mood_median_by_month[month] = round(statistics.median(values), 3)
    mood_median_by_month = _sort_by_period(mood_median_by_month, "%Y-%m")

    rolling_avg = {}
    months = deque(maxlen=3)
    for month, avg in mood_mean_by_month.items():
        months.append(avg)
        rolling_avg[month] = round(sum(months) / len(months), 3)

    mood_analysis_mean_by_month = {}
    for month, total in mood_analysis_sum_by_month.items():
        mood_analysis_mean_by_month[month] = round(
            total / mood_analysis_count_by_month[month], 3
        )
    mood_analysis_mean_by_month = _sort_by_period(mood_analysis_mean_by_month, "%Y-%m")

    tags_common = dict(
        sorted(display_tag_counter.items(), key=lambda item: item[1], reverse=True)[:30]
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
        weekday_labels[index]: weekday_activity.get(index, 0) for index in range(7)
    }

    activity_by_hour = {f"{hour:02d}": hour_activity.get(hour, 0) for hour in range(24)}

    mood_tag_correlation = {
        mood: dict(counter.most_common(10)) for mood, counter in mood_tag_counts.items()
    }

    tag_deltas = []
    for tag in set(recent_tag_counts) | set(prior_tag_counts):
        delta = recent_tag_counts.get(tag, 0) - prior_tag_counts.get(tag, 0)
        if delta != 0:
            tag_deltas.append((tag, delta))

    rising_tags = dict(
        sorted([item for item in tag_deltas if item[1] > 0], key=lambda item: item[1], reverse=True)[:5]
    )
    falling_tags = dict(
        sorted([item for item in tag_deltas if item[1] < 0], key=lambda item: item[1])[:5]
    )

    mood_stability_score = round(
        statistics.pstdev(mood_mean_by_month.values()), 3
    ) if len(mood_mean_by_month) > 1 else 0.0

    writing_streak = _compute_writing_streaks(unique_written_dates)
    milestones = _compute_milestones(
        note_count=len(notes),
        longest_streak=writing_streak["longest"],
        unique_days=len(unique_written_dates),
        notes=notes,
    )

    word_trend_by_month = {
        month: {
            "total_words": data["word_count"],
            "avg_words_per_note": round(
                data["word_count"] / data["note_count"], 2
            ) if data["note_count"] else 0,
            "note_count": data["note_count"],
        }
        for month, data in by_month.items()
    }

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
        "tag_trend": {"rising": rising_tags, "falling": falling_tags},
        "mood_stability_score": mood_stability_score,
        "milestones": milestones,
        "word_trend_by_month": word_trend_by_month,
    }

    return {
        "note_count": len(notes),
        "word_count": word_count,
        "char_count": char_count,
        "mood_metric": dict(mood_count),
        "total_unique_tag_count": len(raw_tag_counter),
        "ai_fix_count": ai_fix_count,
        "metrics": metrics,
    }


async def update_notes_metrics(user_id: str):
    logger.debug("background task for metrics")
    started_at = datetime.datetime.utcnow()

    query_metric = Select(NoteMetrics).where(NoteMetrics.user_id == user_id)
    metric_data = await db_ops.read_one_record(query=query_metric)

    query = Select(Notes).where(Notes.user_id == user_id).limit(10000).offset(0)
    # read_query() returns a DatabaseErrorResult (a dict) on failure rather
    # than raising - iterating that directly yields its string keys, which
    # is what produced the "'str' object has no attribute 'date_created'"
    # crash here when the underlying query failed (e.g. a table missing).
    notes = _safe_list(await db_ops.read_query(query=query))
    notes = [_metrics_note_from_model(note) for note in notes]
    bundle = await _compute_metrics_bundle(notes=notes)

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
    elapsed_ms = int((datetime.datetime.utcnow() - started_at).total_seconds() * 1000)
    logger.info("Note metrics updated for user {} in {}ms", user_id, elapsed_ms)
