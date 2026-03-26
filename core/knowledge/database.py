"""
Aleph Framework — Knowledge Database
======================================
Postgres connection and auto-bootstrap for knowledge base.
Separate schema from habits — independent lifecycle.
"""

from __future__ import annotations

import logging
import os

import asyncpg

from core.registry.schema import KnowledgeConfig

logger = logging.getLogger("aleph.knowledge")


class KnowledgeDatabase:
    """Manages Postgres connections for the knowledge module."""

    def __init__(self, config: KnowledgeConfig):
        self.config = config
        self._pool: asyncpg.Pool | None = None

    @property
    def database_url(self) -> str:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise ValueError(
                "DATABASE_URL environment variable not set. "
                "Required when knowledge.enabled=true."
            )
        return url

    @property
    def migration_url(self) -> str:
        return os.environ.get("DATABASE_MIGRATION_URL", "") or self.database_url

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        logger.info("Knowledge database connected (pool: 1-5)")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Knowledge database disconnected")

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._pool

    async def bootstrap(self) -> None:
        """Auto-bootstrap: create schema, extensions, table, indexes, RPC."""
        if not self.config.auto_migrate:
            logger.info("Knowledge auto_migrate=false, skipping bootstrap")
            return

        schema = self.config.schema
        table = self.config.table_name
        full_table = f"{schema}.{table}" if schema != "public" else table
        dims = self.config.embedding_dimensions

        logger.info("Running knowledge bootstrap (table: %s)...", full_table)

        conn = await asyncpg.connect(self.migration_url, timeout=15)
        try:
            # --- Extensions ---
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
            logger.info("Extensions OK (vector, unaccent)")

            # --- Schema ---
            if schema != "public":
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")

            # --- Table ---
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {full_table} (
                    id BIGSERIAL PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    context TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    chunk_index INT NOT NULL DEFAULT 0,
                    embedding vector({dims}),
                    search_text TSVECTOR,
                    metadata JSONB DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
            """)
            logger.info("Table OK: %s", full_table)

            # --- Indexes ---
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_search_text
                ON {full_table} USING GIN (search_text);
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_embedding
                ON {full_table} USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_client_id
                ON {full_table} (client_id);
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_source
                ON {full_table} (client_id, source);
            """)

            logger.info("Indexes OK")

            # --- Hybrid search RRF function ---
            await conn.execute(f"""
                CREATE OR REPLACE FUNCTION buscar_conhecimento_hibrido(
                    p_client_id TEXT,
                    p_query TEXT,
                    p_embedding vector({dims}),
                    p_match_count INT DEFAULT 5,
                    p_rrf_k INT DEFAULT 60
                )
                RETURNS TABLE (
                    id BIGINT,
                    content TEXT,
                    context TEXT,
                    source TEXT,
                    chunk_index INT,
                    metadata JSONB,
                    rrf_score DOUBLE PRECISION,
                    semantic_rank BIGINT,
                    fulltext_rank BIGINT
                )
                LANGUAGE sql STABLE
                AS $$
                    WITH semantic AS (
                        SELECT
                            k.id,
                            ROW_NUMBER() OVER (
                                ORDER BY k.embedding <=> p_embedding
                            ) AS rank
                        FROM {full_table} k
                        WHERE k.client_id = p_client_id
                        ORDER BY k.embedding <=> p_embedding
                        LIMIT p_match_count * 3
                    ),
                    fulltext AS (
                        SELECT
                            k.id,
                            ROW_NUMBER() OVER (
                                ORDER BY ts_rank_cd(
                                    k.search_text,
                                    plainto_tsquery('portuguese', unaccent(p_query))
                                ) DESC
                            ) AS rank
                        FROM {full_table} k
                        WHERE k.client_id = p_client_id
                          AND k.search_text @@ plainto_tsquery('portuguese', unaccent(p_query))
                        ORDER BY ts_rank_cd(
                            k.search_text,
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
                        k.id,
                        k.content,
                        k.context,
                        k.source,
                        k.chunk_index,
                        k.metadata,
                        c.rrf_score,
                        c.semantic_rank,
                        c.fulltext_rank
                    FROM combined c
                    JOIN {full_table} k ON k.id = c.id
                    ORDER BY c.rrf_score DESC
                    LIMIT p_match_count;
                $$;
            """)
            logger.info("Function OK: buscar_conhecimento_hibrido")

            # --- Auto-update tsvector trigger ---
            await conn.execute(f"""
                CREATE OR REPLACE FUNCTION {table}_update_search_text()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    NEW.search_text :=
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(NEW.context, ''))), 'A') ||
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(NEW.content, ''))), 'B');
                    RETURN NEW;
                END;
                $$;
            """)

            await conn.execute(f"""
                DROP TRIGGER IF EXISTS trg_{table}_search_text ON {full_table};
            """)
            await conn.execute(f"""
                CREATE TRIGGER trg_{table}_search_text
                BEFORE INSERT OR UPDATE ON {full_table}
                FOR EACH ROW
                EXECUTE FUNCTION {table}_update_search_text();
            """)
            logger.info("Trigger OK: auto-update search_text (context=A, content=B)")

            logger.info("Knowledge bootstrap complete: %s", full_table)

        finally:
            await conn.close()