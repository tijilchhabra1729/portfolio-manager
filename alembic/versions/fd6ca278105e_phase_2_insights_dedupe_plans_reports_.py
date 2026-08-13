"""phase 2: insights dedupe, plans, reports, analyses, usage

Revision ID: fd6ca278105e
Revises: 9b1c4d7e2a01

Adds the agentic layer's storage: a dedupe key on insights (so a nightly rerun updates a
condition in place instead of stacking duplicates), plans for billing, weekly reports, a
shared cache of Explore analyses, and the free-plan daily-usage counter.

Every new user-owned table gets the same PostgREST lockdown as migration 9b1c4d7e2a01:
RLS on, anon/authenticated grants revoked. Without it, Supabase would expose these tables
(plans, reports) through its public REST API to anyone holding the browser's anon key.
`stock_analyses` is shared/public data, but we lock it too — the app reads it via
SQLAlchemy as the table owner, so nothing legitimate needs the anon grant.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "fd6ca278105e"
down_revision = "9b1c4d7e2a01"
branch_labels = None
depends_on = None

NEW_TABLES = ("user_plans", "reports", "stock_analyses", "explore_usage")
SUPABASE_ROLES = ("anon", "authenticated")


def upgrade() -> None:
    # --- insights: dedupe key ---
    op.add_column("insights", sa.Column("dedupe_key", sa.String(length=120), nullable=True))
    op.create_unique_constraint(
        "uq_insight_key", "insights", ["user_id", "market", "source", "dedupe_key"]
    )

    # --- user_plans ---
    op.create_table(
        "user_plans",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(length=64)),
        sa.Column("stripe_subscription_id", sa.String(length=64)),
        sa.Column("status", sa.String(length=32)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- reports ---
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("health_score", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("red_flags", JSONB()),
        sa.Column("generated_by", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "market", "period_start", name="uq_report_week"),
    )

    # --- stock_analyses (shared cache) ---
    op.create_table(
        "stock_analyses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("ticker", sa.String(length=24), nullable=False),
        sa.Column("body", JSONB(), nullable=False),
        sa.Column("generated_by", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market", "ticker", name="uq_stock_analysis"),
    )

    # --- explore_usage (the daily cap) ---
    op.create_table(
        "explore_usage",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "day", name="uq_explore_usage_day"),
    )

    # --- lock every new table away from Supabase's public REST API ---
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
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
    for table in NEW_TABLES:
        op.drop_table(table)
    op.drop_constraint("uq_insight_key", "insights", type_="unique")
    op.drop_column("insights", "dedupe_key")
