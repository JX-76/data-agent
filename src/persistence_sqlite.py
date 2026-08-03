# -*- coding: utf-8 -*-
"""Tenant-scoped SQLite development adapter for Phase 2 repositories.

SQLite remains a local/single-node adapter. Production must use Postgres with
RLS and Redis-backed session/cache state; see the fail-closed skeletons.
Existing R31 methods remain available for backward compatibility.
"""
from __future__ import unicode_literals
import hashlib
import json
import sqlite3
import time

from repository_contracts import (SessionRepository, MemoryRepository,
    ClarificationRepository, HumanReviewRepository, AuditRepository,
    CacheRepository, EvidenceRepository)


class RetentionPolicy(object):
    def __init__(self, ttl_seconds=None):
        self.ttl_seconds = dict(ttl_seconds or {
            "history": 2592000, "audit": 7776000, "gate": 7776000,
            "cache": 86400, "dashboard": 2592000, "sessions": 2592000,
            "memory": 2592000, "clarification": 1800, "human_review": 7776000,
            "idempotency": 86400,
        })


class SQLitePersistence(SessionRepository, MemoryRepository, ClarificationRepository,
                        HumanReviewRepository, AuditRepository, CacheRepository,
                        EvidenceRepository):
    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS history(tenant_id TEXT,user_id TEXT,record_id TEXT,body TEXT,created REAL,PRIMARY KEY(tenant_id,record_id));
        CREATE TABLE IF NOT EXISTS audit(tenant_id TEXT,event_id TEXT,body TEXT,previous_hash TEXT,event_hash TEXT,created REAL,PRIMARY KEY(tenant_id,event_id));
        CREATE TABLE IF NOT EXISTS gate(tenant_id TEXT,gate_id TEXT,body TEXT,created REAL,PRIMARY KEY(tenant_id,gate_id));
        CREATE TABLE IF NOT EXISTS cache(tenant_id TEXT,cache_key TEXT,body TEXT,expiry REAL,PRIMARY KEY(tenant_id,cache_key));
        CREATE TABLE IF NOT EXISTS dashboard(tenant_id TEXT,aggregate_id TEXT,body TEXT,created REAL,PRIMARY KEY(tenant_id,aggregate_id));
        CREATE TABLE IF NOT EXISTS sessions(tenant_id TEXT,user_id TEXT,session_id TEXT,body TEXT,created REAL,updated REAL,PRIMARY KEY(tenant_id,session_id));
        CREATE TABLE IF NOT EXISTS memory(tenant_id TEXT,memory_id TEXT,body TEXT,expires_at REAL,created REAL,PRIMARY KEY(tenant_id,memory_id));
        CREATE TABLE IF NOT EXISTS clarification(tenant_id TEXT,session_id TEXT,body TEXT,expires_at REAL,created REAL,PRIMARY KEY(tenant_id,session_id));
        CREATE TABLE IF NOT EXISTS human_review(tenant_id TEXT,session_id TEXT,body TEXT,created REAL,PRIMARY KEY(tenant_id,session_id));
        CREATE TABLE IF NOT EXISTS idempotency(tenant_id TEXT,idempotency_key TEXT,body TEXT,expiry REAL,PRIMARY KEY(tenant_id,idempotency_key));
        CREATE TABLE IF NOT EXISTS evidence(tenant_id TEXT,session_id TEXT,evidence_id TEXT,body TEXT,expires_at REAL,created REAL,PRIMARY KEY(tenant_id,session_id,evidence_id));
        """)
        self.connection.commit()

    def close(self):
        self.connection.close()

    def _tenant(self, tenant_id):
        return tenant_id or "default"

    def _access_tenant(self, access):
        return self._tenant(getattr(access, "tenant_id", None))

    def _access_user(self, access):
        return getattr(access, "user_id", None) or "anonymous"

    def _json(self, value):
        return json.dumps(value or {}, sort_keys=True)

    def _decode(self, row):
        return json.loads(row[0]) if row else None

    # Legacy R31 history API.
    def save_history(self, tenant_id, user_id, record_id, body):
        self.connection.execute("INSERT OR REPLACE INTO history VALUES(?,?,?,?,?)", (self._tenant(tenant_id), user_id, record_id, self._json(body), time.time()))
        self.connection.commit()

    def get_history(self, tenant_id, record_id):
        return self._decode(self.connection.execute("SELECT body FROM history WHERE tenant_id=? AND record_id=?", (self._tenant(tenant_id), record_id)).fetchone())

    def list_history(self, tenant_id):
        return [json.loads(row[0]) for row in self.connection.execute("SELECT body FROM history WHERE tenant_id=? ORDER BY created", (self._tenant(tenant_id),))]

    # Session repository.
    def save_session(self, access, session_id, payload):
        now = time.time(); tenant = self._access_tenant(access)
        self.connection.execute("INSERT OR REPLACE INTO sessions(tenant_id,user_id,session_id,body,created,updated) VALUES(?,?,?, ?,COALESCE((SELECT created FROM sessions WHERE tenant_id=? AND session_id=?),?),?)", (tenant, self._access_user(access), session_id, self._json(payload), tenant, session_id, now, now))
        self.connection.commit()

    def get_session(self, access, session_id):
        return self._decode(self.connection.execute("SELECT body FROM sessions WHERE tenant_id=? AND session_id=?", (self._access_tenant(access), session_id)).fetchone())

    def list_sessions(self, access, limit=100):
        return [json.loads(row[0]) for row in self.connection.execute("SELECT body FROM sessions WHERE tenant_id=? ORDER BY updated DESC LIMIT ?", (self._access_tenant(access), int(limit))) ]

    # Memory repository.
    def save_memory(self, access, memory_id, payload):
        tenant = self._access_tenant(access); now = time.time(); expiry = (payload or {}).get("expires_at")
        self.connection.execute("INSERT OR REPLACE INTO memory VALUES(?,?,?,?,?)", (tenant, memory_id, self._json(payload), expiry, now)); self.connection.commit()

    def get_memory(self, access, memory_id):
        now = time.time(); row = self.connection.execute("SELECT body,expires_at FROM memory WHERE tenant_id=? AND memory_id=?", (self._access_tenant(access), memory_id)).fetchone()
        return json.loads(row[0]) if row and (row[1] is None or row[1] > now) else None

    def list_memory(self, access, limit=100):
        now = time.time(); return [json.loads(row[0]) for row in self.connection.execute("SELECT body FROM memory WHERE tenant_id=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created DESC LIMIT ?", (self._access_tenant(access), now, int(limit)))]

    def delete_expired_memory(self, access, now=None):
        cursor = self.connection.execute("DELETE FROM memory WHERE tenant_id=? AND expires_at IS NOT NULL AND expires_at<=?", (self._access_tenant(access), now if now is not None else time.time())); self.connection.commit(); return cursor.rowcount

    # Clarification and human-review repositories.
    def save_clarification(self, access, session_id, payload):
        value = payload or {}; self.connection.execute("INSERT OR REPLACE INTO clarification VALUES(?,?,?,?,?)", (self._access_tenant(access), session_id, self._json(value), value.get("expires_at"), time.time())); self.connection.commit()

    def get_clarification(self, access, session_id):
        row = self.connection.execute("SELECT body,expires_at FROM clarification WHERE tenant_id=? AND session_id=?", (self._access_tenant(access), session_id)).fetchone()
        return json.loads(row[0]) if row and (row[1] is None or row[1] > time.time()) else None

    def delete_clarification(self, access, session_id):
        cursor = self.connection.execute("DELETE FROM clarification WHERE tenant_id=? AND session_id=?", (self._access_tenant(access), session_id)); self.connection.commit(); return cursor.rowcount > 0

    def save_review(self, access, session_id, payload):
        self.connection.execute("INSERT OR REPLACE INTO human_review VALUES(?,?,?,?)", (self._access_tenant(access), session_id, self._json(payload), time.time())); self.connection.commit()

    def get_review(self, access, session_id):
        return self._decode(self.connection.execute("SELECT body FROM human_review WHERE tenant_id=? AND session_id=?", (self._access_tenant(access), session_id)).fetchone())

    def delete_review(self, access, session_id):
        cursor = self.connection.execute("DELETE FROM human_review WHERE tenant_id=? AND session_id=?", (self._access_tenant(access), session_id)); self.connection.commit(); return cursor.rowcount > 0

    # Audit repository and legacy names.
    def append_audit(self, tenant_id, event_id, body):
        tenant = self._tenant(tenant_id); previous = self.connection.execute("SELECT event_hash FROM audit WHERE tenant_id=? ORDER BY created DESC LIMIT 1", (tenant,)).fetchone(); previous = previous[0] if previous else ""
        encoded = self._json(body); event_hash = hashlib.sha256((encoded + previous).encode("utf-8")).hexdigest()
        self.connection.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)", (tenant, event_id, encoded, previous, event_hash, time.time())); self.connection.commit(); return event_hash

    def append_event(self, access, event_id, payload):
        return self.append_audit(self._access_tenant(access), event_id, payload)

    def list_audit(self, tenant_id):
        return [dict(body=json.loads(row[0]), previous_hash=row[1], event_hash=row[2]) for row in self.connection.execute("SELECT body,previous_hash,event_hash FROM audit WHERE tenant_id=? ORDER BY created", (self._tenant(tenant_id),))]

    def list_events(self, access, limit=100):
        return self.list_audit(self._access_tenant(access))[-int(limit):]

    def verify_audit(self, tenant_id):
        previous = ""
        for event in self.list_audit(tenant_id):
            if event["previous_hash"] != previous or hashlib.sha256((self._json(event["body"]) + previous).encode("utf-8")).hexdigest() != event["event_hash"]: return False
            previous = event["event_hash"]
        return True

    def verify_chain(self, access):
        return self.verify_audit(self._access_tenant(access))

    def save_gate(self, tenant_id, gate_id, body): self._save("gate", tenant_id, gate_id, body)
    def get_gate(self, tenant_id, gate_id): return self._get("gate", tenant_id, gate_id)
    def save_dashboard(self, tenant_id, aggregate_id, body): self._save("dashboard", tenant_id, aggregate_id, body)
    def get_dashboard(self, tenant_id, aggregate_id): return self._get("dashboard", tenant_id, aggregate_id)
    def _save(self, table, tenant_id, key, body): self.connection.execute("INSERT OR REPLACE INTO %s VALUES(?,?,?,?)" % table, (self._tenant(tenant_id), key, self._json(body), time.time())); self.connection.commit()
    def _get(self, table, tenant_id, key):
        identifier = "gate" if table == "gate" else "aggregate"; return self._decode(self.connection.execute("SELECT body FROM %s WHERE tenant_id=? AND %s_id=?" % (table, identifier), (self._tenant(tenant_id), key)).fetchone())

    # Cache repository and idempotency helpers are tenant-namespaced.
    def set_cache(self, tenant_id, key, body, ttl=300): self.connection.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?)", (self._tenant(tenant_id), key, self._json(body), time.time() + ttl)); self.connection.commit()
    def get_cache(self, tenant_id, key):
        row = self.connection.execute("SELECT body,expiry FROM cache WHERE tenant_id=? AND cache_key=?", (self._tenant(tenant_id), key)).fetchone(); return json.loads(row[0]) if row and row[1] > time.time() else None
    def set_value(self, access, key, payload, ttl_seconds=300): self.set_cache(self._access_tenant(access), key, payload, ttl_seconds)
    def get_value(self, access, key): return self.get_cache(self._access_tenant(access), key)
    def delete_value(self, access, key):
        cursor = self.connection.execute("DELETE FROM cache WHERE tenant_id=? AND cache_key=?", (self._access_tenant(access), key)); self.connection.commit(); return cursor.rowcount > 0
    def save_idempotency(self, access, key, payload, ttl_seconds=86400): self.connection.execute("INSERT OR REPLACE INTO idempotency VALUES(?,?,?,?)", (self._access_tenant(access), key, self._json(payload), time.time() + ttl_seconds)); self.connection.commit()
    def get_idempotency(self, access, key):
        row = self.connection.execute("SELECT body,expiry FROM idempotency WHERE tenant_id=? AND idempotency_key=?", (self._access_tenant(access), key)).fetchone(); return json.loads(row[0]) if row and row[1] > time.time() else None

    # Evidence repository: persists only verified EvidenceBus records.
    def save_evidence(self, access, session_id, evidence_id, payload):
        value = payload or {}; now = time.time(); expires_at = value.get("expires_at")
        self.connection.execute("INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?,?)", (self._access_tenant(access), session_id, evidence_id, self._json(value), expires_at, now)); self.connection.commit()
        return value

    def get_evidence(self, access, session_id, evidence_id):
        row = self.connection.execute("SELECT body,expires_at FROM evidence WHERE tenant_id=? AND session_id=? AND evidence_id=?", (self._access_tenant(access), session_id, evidence_id)).fetchone()
        return json.loads(row[0]) if row and (row[1] is None or row[1] > time.time()) else None

    def list_evidence(self, access, session_id, limit=100):
        now = time.time(); return [json.loads(row[0]) for row in self.connection.execute("SELECT body FROM evidence WHERE tenant_id=? AND session_id=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created DESC LIMIT ?", (self._access_tenant(access), session_id, now, int(limit)))]

    def delete_expired_evidence(self, access, now=None):
        cursor = self.connection.execute("DELETE FROM evidence WHERE tenant_id=? AND expires_at IS NOT NULL AND expires_at<=?", (self._access_tenant(access), now if now is not None else time.time())); self.connection.commit(); return cursor.rowcount

    def cleanup(self, policy, dry_run=True):
        now = time.time(); counts = {}; created_tables = set(["history", "audit", "gate", "dashboard", "sessions", "memory", "clarification", "human_review", "evidence"])
        for table, ttl in policy.ttl_seconds.items():
            if table not in created_tables and table not in ("cache", "idempotency"): continue
            if table in ("cache", "idempotency"): where, param = "expiry<?", now
            elif table == "memory": where, param = "expires_at IS NOT NULL AND expires_at<?", now
            elif table == "clarification": where, param = "expires_at IS NOT NULL AND expires_at<?", now
            elif table == "evidence": where, param = "expires_at IS NOT NULL AND expires_at<?", now
            else: where, param = "created<?", now - ttl
            counts[table] = self.connection.execute("SELECT count(*) FROM %s WHERE %s" % (table, where), (param,)).fetchone()[0]
            if not dry_run: self.connection.execute("DELETE FROM %s WHERE %s" % (table, where), (param,))
        if not dry_run: self.connection.commit()
        return counts


__all__ = ["SQLitePersistence", "RetentionPolicy"]
