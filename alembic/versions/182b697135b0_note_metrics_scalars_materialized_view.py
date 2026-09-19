# -*- coding: utf-8 -*-
"""note_metrics_scalars materialized view

Revision ID: 182b697135b0
Revises: aba742792ce0
Create Date: 2026-09-13 21:11:06.883909

Moves NoteMetrics' plain scalar aggregates (word_count, character_count,
note_count, ai_fix_count, total_unique_tag_count, mood_metric) into a
materialized view computed directly from `notes`, grouped by user_id.
The `metrics` JSON column (rolling averages, streaks/milestones, mood-
weighted means, tag trends) stays a stored, Python-computed column - none
of that is expressible in plain SQL. See
src/functions/notes_metrics.py::_compute_metrics_bundle() and
src/db_tables.py's NoteMetrics/NoteMetricsScalars split.

Alembic only ever runs against Postgres (see src/resources.py -
create_tables() remains the schema manager for every SQLite driver), so
this migration doesn't need a dialect guard.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "182b697135b0"
down_revision: Union[str, Sequence[str], None] = "aba742792ce0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CREATE_VIEW_SQL = """
CREATE MATERIALIZED VIEW note_metrics_scalars AS
SELECT
    n.user_id AS user_id,
    COALESCE(SUM(n.word_count), 0)::integer AS word_count,
    COALESCE(SUM(n.character_count), 0)::integer AS character_count,
    COUNT(*)::integer AS note_count,
    COUNT(*) FILTER (WHERE n.ai_fix)::integer AS ai_fix_count,
    -- LOWER(tag) mirrors _add_tag()'s tag_lower dedup in notes_metrics.py -
    -- keep both in sync if tag normalization ever changes there. The
    -- LATERAL join expands each note's JSON tag array into one row per
    -- tag without multiplying the outer per-user row (a plain join would
    -- require a separate outer GROUP BY to undo that fan-out).
    COALESCE((
        SELECT COUNT(DISTINCT LOWER(tag))
        FROM notes n2, LATERAL json_array_elements_text(COALESCE(n2.tags, '[]'::json)) AS tag
        WHERE n2.user_id = n.user_id
    ), 0)::integer AS total_unique_tag_count,
    -- NULLIF(mood, '') + COALESCE(..., 'neutral') mirrors
    -- `note.mood or "neutral"` in notes_metrics.py (line ~30) exactly,
    -- including its empty-string fallback - keep both in sync if that
    -- default ever changes.
    COALESCE((
        SELECT jsonb_object_agg(mood_group.mood, mood_group.cnt)
        FROM (
            SELECT COALESCE(NULLIF(n3.mood, ''), 'neutral') AS mood, COUNT(*) AS cnt
            FROM notes n3 WHERE n3.user_id = n.user_id
            GROUP BY COALESCE(NULLIF(n3.mood, ''), 'neutral')
        ) AS mood_group
    ), '{}'::jsonb) AS mood_metric
FROM notes n
GROUP BY n.user_id;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CREATE_VIEW_SQL)
    op.execute(
        "CREATE UNIQUE INDEX ix_note_metrics_scalars_user_id "
        "ON note_metrics_scalars (user_id)"
    )
    op.drop_column("note_metrics", "character_count")
    op.drop_column("note_metrics", "mood_metric")
    op.drop_column("note_metrics", "ai_fix_count")
    op.drop_column("note_metrics", "note_count")
    op.drop_column("note_metrics", "word_count")
    op.drop_column("note_metrics", "total_unique_tag_count")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "note_metrics",
        sa.Column("total_unique_tag_count", sa.Integer(), server_default="0"),
    )
    op.add_column(
        "note_metrics", sa.Column("word_count", sa.Integer(), server_default="0")
    )
    op.add_column(
        "note_metrics", sa.Column("note_count", sa.Integer(), server_default="0")
    )
    op.add_column(
        "note_metrics", sa.Column("ai_fix_count", sa.Integer(), server_default="0")
    )
    op.add_column("note_metrics", sa.Column("mood_metric", sa.JSON()))
    op.add_column(
        "note_metrics", sa.Column("character_count", sa.Integer(), server_default="0")
    )
    op.execute("DROP INDEX IF EXISTS ix_note_metrics_scalars_user_id")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS note_metrics_scalars")
