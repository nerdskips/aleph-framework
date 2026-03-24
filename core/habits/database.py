"""
Zuper Agent Framework — Habits Database
==========================================
Manages Postgres connection and auto-bootstrap for operational habits.

Supports any Postgres with pgvector extension:
  - Self-hosted Postgres
  - Supabase (direct connection for migrations, pooler for queries)
  - Any managed Postgres (RDS, Cloud SQL, etc)

Environment:
  DATABASE_URL          — Main connection (queries). Required when habits.enabled=true.
  DATABASE_MIGRATION_URL — DDL/migration connection (optional). Falls back to DATABASE_URL.
                           Needed for Supabase pooler setups where the pooler
                           (port 6543) doesn't support CREATE EXTENSION/DDL.

Auto-bootstrap (when habits.auto_migrate=true):
  1. CREATE EXTENSION IF NOT EXISTS vector
  2. CREATE EXTENSION IF NOT EXISTS unaccent
  3. CREATE TABLE IF NOT EXISTS operational_habits
  4. CREATE INDEX IF NOT EXISTS (GIN tsvector, IVFFlat vector, btree)
  5. CREATE OR REPLACE FUNCTION buscar_habito_hibrido (hybrid search RRF)
"""

from __future__ import annotations

import logging
import os

import asyncpg

from core.registry.schema import HabitsConfig

logger = logging.getLogger("zuper.habits")


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

class HabitsDatabase:
    """Manages Postgres connections for the habits module.

    Two connection modes:
      - query_pool: for reads/writes (uses DATABASE_URL, possibly pooler)
      - migration_conn: for DDL (uses DATABASE_MIGRATION_URL or DATABASE_URL)
    """

    def __init__(self, config: HabitsConfig):
        self.config = config
        self._pool: asyncpg.Pool | None = None

    @property
    def database_url(self) -> str:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise ValueError(
                "DATABASE_URL environment variable not set. "
                "Required when habits.enabled=true."
            )
        return url

    @property
    def migration_url(self) -> str:
        """Migration URL for DDL operations.
        Falls back to DATABASE_URL if DATABASE_MIGRATION_URL not set."""
        return os.environ.get("DATABASE_MIGRATION_URL", "") or self.database_url

    async def connect(self) -> None:
        """Initialize the query connection pool."""
        self._pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        logger.info("Habits database connected (pool: 1-5)")

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Habits database disconnected")

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._pool

    # -------------------------------------------------------------------
    # Auto-bootstrap (migrations)
    # -------------------------------------------------------------------

    async def bootstrap(self) -> None:
        """Run auto-bootstrap: create extensions, table, indexes, functions.

        Uses migration_url (direct connection) for DDL compatibility.
        Safe to run multiple times (all statements use IF NOT EXISTS).
        """
        if not self.config.auto_migrate:
            logger.info("Habits auto_migrate=false, skipping bootstrap")
            return

        schema = self.config.schema
        table = self.config.table_name
        full_table = f"{schema}.{table}" if schema != "public" else table
        dims = self.config.embedding_dimensions

        logger.info("Running habits bootstrap (table: %s)...", full_table)

        conn = await asyncpg.connect(self.migration_url, timeout=15)
        try:
            # --- Extensions ---
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
            logger.info("Extensions OK (vector, unaccent)")

            # --- Schema (if not public) ---
            if schema != "public":
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")

            # --- Table ---
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {full_table} (
                    id BIGSERIAL PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    human_instruction TEXT NOT NULL DEFAULT '',
                    is_unique BOOLEAN NOT NULL DEFAULT false,
                    embedding vector({dims}),
                    search_text TSVECTOR,
                    metadata JSONB DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                );
            """)
            logger.info("Table OK: %s", full_table)

            # --- Indexes ---
            # GIN index for full-text search (tsvector)
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_search_text
                ON {full_table} USING GIN (search_text);
            """)

            # IVFFlat index for vector similarity search
            # lists = sqrt(n) rule of thumb, start with 100 for small datasets
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_embedding
                ON {full_table} USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            """)

            # btree for filtering
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_client_id
                ON {full_table} (client_id);
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_is_unique
                ON {full_table} (is_unique);
            """)

            logger.info("Indexes OK")

            # --- Hybrid search RRF function ---
            await conn.execute(f"""
                CREATE OR REPLACE FUNCTION buscar_habito_hibrido(
                    p_client_id TEXT,
                    p_query TEXT,
                    p_embedding vector({dims}),
                    p_match_count INT DEFAULT 3,
                    p_rrf_k INT DEFAULT 60
                )
                RETURNS TABLE (
                    id BIGINT,
                    question TEXT,
                    answer TEXT,
                    human_instruction TEXT,
                    metadata JSONB,
                    rrf_score DOUBLE PRECISION,
                    semantic_rank BIGINT,
                    fulltext_rank BIGINT
                )
                LANGUAGE sql STABLE
                AS $$
                    WITH semantic AS (
                        SELECT
                            h.id,
                            ROW_NUMBER() OVER (
                                ORDER BY h.embedding <=> p_embedding
                            ) AS rank
                        FROM {full_table} h
                        WHERE h.client_id = p_client_id
                          AND h.is_unique = false
                        ORDER BY h.embedding <=> p_embedding
                        LIMIT p_match_count * 3
                    ),
                    fulltext AS (
                        SELECT
                            h.id,
                            ROW_NUMBER() OVER (
                                ORDER BY ts_rank_cd(
                                    h.search_text,
                                    plainto_tsquery('portuguese', unaccent(p_query))
                                ) DESC
                            ) AS rank
                        FROM {full_table} h
                        WHERE h.client_id = p_client_id
                          AND h.is_unique = false
                          AND h.search_text @@ plainto_tsquery('portuguese', unaccent(p_query))
                        ORDER BY ts_rank_cd(
                            h.search_text,
                            plainto_tsquery('portuguese', unaccent(p_query))
                        ) DESC
                        LIMIT p_match_count * 3
                    ),
                    combined AS (
                        SELECT
                            COALESCE(s.id, f.id) AS id,
                            COALESCE(1.0 / (p_rrf_k + s.rank), 0.0)
                              + COALESCE(1.0 / (p_rrf_k + f.rank), 0.0) AS rrf_score,
                            s.rank AS semantic_rank,
                            f.rank AS fulltext_rank
                        FROM semantic s
                        FULL OUTER JOIN fulltext f ON s.id = f.id
                    )
                    SELECT
                        h.id,
                        h.question,
                        h.answer,
                        h.human_instruction,
                        h.metadata,
                        c.rrf_score,
                        c.semantic_rank,
                        c.fulltext_rank
                    FROM combined c
                    JOIN {full_table} h ON h.id = c.id
                    ORDER BY c.rrf_score DESC
                    LIMIT p_match_count;
                $$;
            """)
            logger.info("Function OK: buscar_habito_hibrido")

            # --- Auto-update tsvector trigger ---
            await conn.execute(f"""
                CREATE OR REPLACE FUNCTION {table}_update_search_text()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    NEW.search_text :=
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(NEW.question, ''))), 'A') ||
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(NEW.answer, ''))), 'B');
                    NEW.updated_at := now();
                    RETURN NEW;
                END;
                $$;
            """)

            # Drop and recreate trigger (CREATE OR REPLACE not supported for triggers)
            await conn.execute(f"""
                DROP TRIGGER IF EXISTS trg_{table}_search_text ON {full_table};
            """)
            await conn.execute(f"""
                CREATE TRIGGER trg_{table}_search_text
                BEFORE INSERT OR UPDATE ON {full_table}
                FOR EACH ROW
                EXECUTE FUNCTION {table}_update_search_text();
            """)
            logger.info("Trigger OK: auto-update search_text (question=A, answer=B)")

            logger.info("Bootstrap complete: %s", full_table)

        finally:
            await conn.close()