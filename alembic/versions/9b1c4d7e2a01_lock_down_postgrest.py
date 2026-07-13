"""Lock the tables away from Supabase's public REST API

Revision ID: 9b1c4d7e2a01
Revises: 84463efe9adf

Supabase exposes every table in the `public` schema through PostgREST, and its default
privileges grant the `anon` and `authenticated` roles access to them. The anon key is
public by design -- the browser needs it to sign in, and this app serves it from
/api/config. Without this migration, anyone who viewed source on the dashboard could
call https://<project>.supabase.co/rest/v1/transactions and read the whole portfolio,
never touching our API or its auth at all.

Two locks, because one is not enough on its own:

  1. REVOKE the grants, so those roles have no privileges on our tables.
  2. ENABLE ROW LEVEL SECURITY with *no policies*, which denies everything by default.
     The table owner (`postgres`, which is who our app and these migrations connect as)
     is exempt from RLS unless FORCE ROW LEVEL SECURITY is set -- so the application
     keeps working untouched while every other role is shut out.

The whole thing is a no-op on local Postgres, where the `anon` role does not exist.
"""

from __future__ import annotations

from alembic import op

revision = "9b1c4d7e2a01"
down_revision = "84463efe9adf"
branch_labels = None
depends_on = None

TABLES = (
    "instruments",
    "transactions",
    "price_snapshots",
    "portfolio_snapshots",
    "insights",
)

SUPABASE_ROLES = ("anon", "authenticated")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    for role in SUPABASE_ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};
                -- Supabase re-grants on every *future* table too, so shut that off as
                -- well or the next migration silently re-opens the hole.
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                  REVOKE ALL ON TABLES FROM {role};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                  REVOKE ALL ON SEQUENCES FROM {role};
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Deliberately not re-granting anon/authenticated. Reopening a data leak is not
    # something a downgrade should do quietly.
