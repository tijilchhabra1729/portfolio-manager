"""phase 3: hierarchical briefings

Revision ID: c7e1a2b3d4f5
Revises: fd6ca278105e

Adds the `briefings` table for the phase-3 hierarchical agent briefing: one cross-market
document per user per week (the orchestrator agent sits above both markets, so a briefing
is a single document and there is no market column -- unlike `reports`).

Same PostgREST lockdown as `9b1c4d7e2a01` / `fd6ca278105e`: RLS on, anon/authenticated
grants revoked, so Supabase never exposes a user's briefing through its public REST API to
anyone holding the browser's anon key. The app reads it via SQLAlchemy as the table owner.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c7e1a2b3d4f5"
down_revision = "fd6ca278105e"
branch_labels = None
depends_on = None

SUPABASE_ROLES = ("anon", "authenticated")


def upgrade() -> None:
    op.create_table(
        "briefings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("headline", sa.String(length=200), nullable=False),
        sa.Column("overall_direction", sa.String(length=16), nullable=False, server_default="neutral"),
        sa.Column("body", JSONB(), nullable=False),
        sa.Column("generated_by", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "period_start", name="uq_briefing_week"),
    )

    op.execute("ALTER TABLE briefings ENABLE ROW LEVEL SECURITY")
    for role in SUPABASE_ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    op.drop_table("briefings")
