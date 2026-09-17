"""portfolio settings: investable amount + options income

Revision ID: e3a9c1b7d2f4
Revises: c7e1a2b3d4f5

Adds `portfolio_settings`: the two per-market numbers the ledger cannot derive and the
user enters by hand -- the total amount set aside to invest (cash position = investable -
invested) and income from options premiums (folded into net P&L). One row per user per
market; the trade ledger itself needs no change, since `transactions` already records
priced BUY/SELL rows.

Same PostgREST lockdown as the earlier revisions: RLS on, anon/authenticated grants
revoked, so Supabase never exposes a user's settings through its public REST API.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "e3a9c1b7d2f4"
down_revision = "c7e1a2b3d4f5"
branch_labels = None
depends_on = None

SUPABASE_ROLES = ("anon", "authenticated")


def upgrade() -> None:
    op.create_table(
        "portfolio_settings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("total_investable", sa.Numeric(20, 4), nullable=True),
        sa.Column("options_income", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "market", name="uq_portfolio_settings"),
    )

    op.execute("ALTER TABLE portfolio_settings ENABLE ROW LEVEL SECURITY")
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
    op.drop_table("portfolio_settings")
