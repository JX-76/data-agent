"""Security audit logging.

Provides:
- Audit trail for sensitive operations
- Tamper-evident logs
- Compliance reporting
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


class AuditEvent:
    """Represents a single audit event."""
    
    def __init__(self, event_type: str, user_id: str, action: str,
                 resource: str, details: Optional[dict] = None):
        self.timestamp = datetime.utcnow().isoformat()
        self.event_type = event_type
        self.user_id = user_id
        self.action = action
        self.resource = resource
        self.details = details or {}
        self.event_hash = self._calculate_hash()
    
    def _calculate_hash(self) -> str:
        """Calculate hash for tamper detection."""
        data = f"{self.timestamp}:{self.event_type}:{self.user_id}:{self.action}:{self.resource}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "user_id": self.user_id,
            "action": self.action,
            "resource": self.resource,
            "details": self.details,
            "event_hash": self.event_hash,
        }


class AuditLogger:
    """Logger for security audit events."""
    
    def __init__(self, log_dir: str = "/var/log/data-agent"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_log = self.log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.log"
    
    def log(self, event_type: str, user_id: str, action: str,
            resource: str, details: Optional[dict] = None):
        """Log an audit event."""
        event = AuditEvent(event_type, user_id, action, resource, details)
        
        # Write to log file
        with open(self._current_log, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")
        
        return event
    
    def log_query(self, user_id: str, query: str, intent: str,
                  sql: Optional[str] = None):
        """Log a query execution."""
        return self.log(
            event_type="query",
            user_id=user_id,
            action="execute",
            resource="query",
            details={
                "query": query,
                "intent": intent,
                "sql": sql,
            }
        )
    
    def log_auth(self, user_id: str, action: str, success: bool,
                 details: Optional[dict] = None):
        """Log an authentication event."""
        return self.log(
            event_type="auth",
            user_id=user_id,
            action=action,
            resource="auth",
            details={
                "success": success,
                **(details or {})
            }
        )
    
    def log_data_access(self, user_id: str, table: str, action: str,
                        rows_affected: int = 0):
        """Log data access event."""
        return self.log(
            event_type="data_access",
            user_id=user_id,
            action=action,
            resource=table,
            details={
                "rows_affected": rows_affected,
            }
        )
    
    def log_config_change(self, user_id: str, config_key: str,
                          old_value: str, new_value: str):
        """Log configuration change."""
        return self.log(
            event_type="config_change",
            user_id=user_id,
            action="update",
            resource=config_key,
            details={
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    
    def get_events(self, start_time: Optional[str] = None,
                   end_time: Optional[str] = None,
                   event_type: Optional[str] = None,
                   user_id: Optional[str] = None) -> list[dict]:
        """Get audit events with filtering."""
        events = []
        
        # Read all log files
        for log_file in self.log_dir.glob("audit_*.log"):
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        
                        # Apply filters
                        if start_time and event["timestamp"] < start_time:
                            continue
                        if end_time and event["timestamp"] > end_time:
                            continue
                        if event_type and event["event_type"] != event_type:
                            continue
                        if user_id and event["user_id"] != user_id:
                            continue
                        
                        events.append(event)
                    except json.JSONDecodeError:
                        continue
        
        return sorted(events, key=lambda x: x["timestamp"])
    
    def verify_integrity(self) -> bool:
        """Verify log integrity."""
        for log_file in self.log_dir.glob("audit_*.log"):
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        expected_hash = event.get("event_hash")
                        if not expected_hash:
                            continue
                        
                        # Recalculate hash
                        data = f"{event['timestamp']}:{event['event_type']}:{event['user_id']}:{event['action']}:{event['resource']}"
                        calculated_hash = hashlib.sha256(data.encode()).hexdigest()[:16]
                        
                        if expected_hash != calculated_hash:
                            return False
                    except (json.JSONDecodeError, KeyError):
                        continue
        
        return True


class AuditService:
    """Compatibility facade used by the HTTP API.

    Older endpoints expect a get_audit().log(...keyword...) / query(...) shape,
    while the underlying implementation stores tamper-evident AuditEvent rows.
    """

    def __init__(self, logger: AuditLogger):
        self._logger = logger

    def log(self, **kwargs):
        user_id = kwargs.get("identity") or kwargs.get("user_id") or "unknown"
        return self._logger.log(
            event_type="query",
            user_id=user_id,
            action="execute",
            resource="query",
            details={k: v for k, v in kwargs.items() if k not in {"identity", "user_id"}},
        )

    def query(self, identity: Optional[str] = None, status: Optional[str] = None,
              since: Optional[str] = None, limit: int = 100) -> list[dict]:
        events = self._logger.get_events(start_time=since, user_id=identity)
        if status:
            events = [e for e in events if e.get("details", {}).get("status") == status]
        return events[-limit:]


# Global instance
audit_logger = AuditLogger()
_audit_service = AuditService(audit_logger)


def get_audit() -> AuditService:
    return _audit_service
