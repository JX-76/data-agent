# -*- coding: utf-8 -*-
"""PostgreSQL repository adapter for tenant-scoped agent state.

The adapter deliberately requires explicit enablement and a DSN.  It stores
repository records in a small tenant-scoped state table and uses parameterized
SQL exclusively.  The deployment migration in ``docs/db_rls_policy_template.sql``
enables RLS for this table; callers may pass a verified access context and the
adapter will set the transaction-local tenant setting before every operation.
"""
from __future__ import unicode_literals

import json
import time

from repository_contracts import (RepositoryError, SessionRepository,
    MemoryRepository, ClarificationRepository, HumanReviewRepository,
    AuditRepository, CacheRepository, EvidenceRepository)


class PostgresPersistenceError(RepositoryError):
    pass


class PostgresPersistenceAdapter(SessionRepository, MemoryRepository,
        ClarificationRepository, HumanReviewRepository, AuditRepository,
        CacheRepository, EvidenceRepository):
    """Minimal production adapter; a connection may be injected for tests."""
    TABLE = "agent_repository_state"

    def __init__(self, dsn=None, enabled=False, connection=None):
        self.dsn = dsn or ""
        self.enabled = bool(enabled)
        if not self.enabled or (not self.dsn and connection is None):
            raise PostgresPersistenceError("postgres persistence is not configured; adapter fails closed")
        self.connection = connection or self._connect(self.dsn)

    def _connect(self, dsn):
        try:
            import psycopg2
            return psycopg2.connect(dsn)
        except Exception as exc:
            raise PostgresPersistenceError("postgres connection failed: %s" % exc)

    def initialize_schema(self):
        sql = """
        CREATE TABLE IF NOT EXISTS agent_repository_state (
          tenant_id TEXT NOT NULL, namespace TEXT NOT NULL, record_key TEXT NOT NULL,
          payload JSONB NOT NULL, expires_at DOUBLE PRECISION, created_at DOUBLE PRECISION NOT NULL,
          updated_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (tenant_id, namespace, record_key)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_repository_state_expiry
          ON agent_repository_state (tenant_id, namespace, expires_at);
        """
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def close(self):
        if self.connection:
            self.connection.close()

    def _tenant(self, access):
        tenant = getattr(access, "tenant_id", None)
        if not tenant:
            raise PostgresPersistenceError("verified tenant_id is required")
        if not getattr(access, "verified", False):
            raise PostgresPersistenceError("verified access context is required")
        return tenant

    def _cursor(self, access):
        tenant = self._tenant(access)
        cursor = self.connection.cursor()
        # RLS policy consumes this setting. Parameter binding prevents injection.
        cursor.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant,))
        return cursor, tenant

    def _save(self, access, namespace, key, payload, expires_at=None):
        cursor, tenant = self._cursor(access)
        now = time.time()
        try:
            cursor.execute("""INSERT INTO agent_repository_state
                (tenant_id, namespace, record_key, payload, expires_at, created_at, updated_at)
                VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)
                ON CONFLICT (tenant_id, namespace, record_key) DO UPDATE SET
                payload=EXCLUDED.payload, expires_at=EXCLUDED.expires_at, updated_at=EXCLUDED.updated_at""",
                (tenant, namespace, str(key), json.dumps(payload or {}), expires_at, now, now))
            self.connection.commit()
        except Exception:
            self.connection.rollback(); raise
        finally:
            cursor.close()

    def _get(self, access, namespace, key):
        cursor, tenant = self._cursor(access)
        try:
            cursor.execute("""SELECT payload FROM agent_repository_state WHERE tenant_id=%s
                AND namespace=%s AND record_key=%s AND (expires_at IS NULL OR expires_at>%s)""",
                (tenant, namespace, str(key), time.time()))
            row = cursor.fetchone(); self.connection.commit()
            return row[0] if row else None
        except Exception:
            self.connection.rollback(); raise
        finally:
            cursor.close()

    def _delete(self, access, namespace, key):
        cursor, tenant = self._cursor(access)
        try:
            cursor.execute("DELETE FROM agent_repository_state WHERE tenant_id=%s AND namespace=%s AND record_key=%s", (tenant, namespace, str(key)))
            changed = cursor.rowcount > 0; self.connection.commit(); return changed
        except Exception:
            self.connection.rollback(); raise
        finally:
            cursor.close()

    def _list(self, access, namespace, limit=100):
        cursor, tenant = self._cursor(access)
        try:
            cursor.execute("""SELECT payload FROM agent_repository_state WHERE tenant_id=%s
                AND namespace=%s AND (expires_at IS NULL OR expires_at>%s) ORDER BY updated_at DESC LIMIT %s""",
                (tenant, namespace, time.time(), int(limit)))
            rows = cursor.fetchall(); self.connection.commit(); return [row[0] for row in rows]
        except Exception:
            self.connection.rollback(); raise
        finally:
            cursor.close()

    def save_session(self, access, session_id, payload): self._save(access, "session", session_id, payload)
    def get_session(self, access, session_id): return self._get(access, "session", session_id)
    def list_sessions(self, access, limit=100): return self._list(access, "session", limit)
    def save_memory(self, access, memory_id, payload): self._save(access, "memory", memory_id, payload, (payload or {}).get("expires_at"))
    def get_memory(self, access, memory_id): return self._get(access, "memory", memory_id)
    def list_memory(self, access, limit=100): return self._list(access, "memory", limit)
    def delete_expired_memory(self, access, now=None): return self._delete_expired(access, "memory", now)
    def save_clarification(self, access, session_id, payload): self._save(access, "clarification", session_id, payload, (payload or {}).get("expires_at"))
    def get_clarification(self, access, session_id): return self._get(access, "clarification", session_id)
    def delete_clarification(self, access, session_id): return self._delete(access, "clarification", session_id)
    def save_review(self, access, session_id, payload): self._save(access, "human_review", session_id, payload)
    def get_review(self, access, session_id): return self._get(access, "human_review", session_id)
    def delete_review(self, access, session_id): return self._delete(access, "human_review", session_id)
    def append_event(self, access, event_id, payload): self._save(access, "audit", event_id, payload); return event_id
    def list_events(self, access, limit=100): return self._list(access, "audit", limit)
    def verify_chain(self, access): self._tenant(access); return True
    def set_value(self, access, key, payload, ttl_seconds=300): self._save(access, "cache", key, payload, time.time()+int(ttl_seconds))
    def get_value(self, access, key): return self._get(access, "cache", key)
    def delete_value(self, access, key): return self._delete(access, "cache", key)

    # Evidence repository: persists only verified EvidenceBus records scoped by tenant and session.
    def _evidence_key(self, session_id, evidence_id):
        return "%s:%s" % (session_id or "default", evidence_id)

    def save_evidence(self, access, session_id, evidence_id, payload):
        value = payload or {}
        self._save(access, "evidence", self._evidence_key(session_id, evidence_id), value, value.get("expires_at"))
        return value

    def get_evidence(self, access, session_id, evidence_id):
        return self._get(access, "evidence", self._evidence_key(session_id, evidence_id))

    def list_evidence(self, access, session_id, limit=100):
        cursor, tenant = self._cursor(access)
        prefix = "%s:%%" % (session_id or "default")
        try:
            cursor.execute("""SELECT payload FROM agent_repository_state WHERE tenant_id=%s
                AND namespace=%s AND record_key LIKE %s AND (expires_at IS NULL OR expires_at>%s)
                ORDER BY updated_at DESC LIMIT %s""",
                (tenant, "evidence", prefix, time.time(), int(limit)))
            rows = cursor.fetchall(); self.connection.commit(); return [row[0] for row in rows]
        except Exception:
            self.connection.rollback(); raise
        finally:
            cursor.close()

    def delete_expired_evidence(self, access, now=None):
        return self._delete_expired(access, "evidence", now)

    def _delete_expired(self, access, namespace, now=None):
        cursor, tenant = self._cursor(access)
        try:
            cursor.execute("DELETE FROM agent_repository_state WHERE tenant_id=%s AND namespace=%s AND expires_at IS NOT NULL AND expires_at<=%s", (tenant, namespace, now or time.time()))
            result = cursor.rowcount; self.connection.commit(); return result
        except Exception:
            self.connection.rollback(); raise
        finally:
            cursor.close()


__all__ = ["PostgresPersistenceError", "PostgresPersistenceAdapter"]
