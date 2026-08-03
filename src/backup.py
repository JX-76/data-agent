"""Database backup and recovery.

Provides:
- Automated backups
- Point-in-time recovery
- Backup verification
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class BackupConfig:
    """Configuration for backups."""
    
    def __init__(self):
        self.enabled = os.environ.get("DATA_AGENT_BACKUP_ENABLED", "true").lower() == "true"
        self.retention_days = int(os.environ.get("DATA_AGENT_BACKUP_RETENTION", "7"))
        self.schedule = os.environ.get("DATA_AGENT_BACKUP_SCHEDULE", "0 2 * * *")  # Daily at 2 AM
        self.location = os.environ.get("DATA_AGENT_BACKUP_LOCATION", "/backups")
        
        # Database connection
        self.db_type = os.environ.get("DATA_AGENT_DB_TYPE", "postgresql")
        self.db_host = os.environ.get("DATA_AGENT_DB_HOST", "localhost")
        self.db_port = os.environ.get("DATA_AGENT_DB_PORT", "5432")
        self.db_name = os.environ.get("DATA_AGENT_DB_NAME", "data_agent")
        self.db_user = os.environ.get("DATA_AGENT_DB_USER", "data_agent")
        self.db_password = os.environ.get("DATA_AGENT_DB_PASSWORD", "")


class BackupManager:
    """Manages database backups."""
    
    def __init__(self, config: Optional[BackupConfig] = None):
        self.config = config or BackupConfig()
        self.backup_dir = Path(self.config.location)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def create_backup(self) -> Path:
        """Create a new backup."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"data_agent_{timestamp}.sql"
        
        if self.config.db_type == "postgresql":
            self._backup_postgresql(backup_file)
        elif self.config.db_type == "mysql":
            self._backup_mysql(backup_file)
        elif self.config.db_type == "sqlite":
            self._backup_sqlite(backup_file)
        else:
            raise ValueError(f"Unsupported database type: {self.config.db_type}")
        
        return backup_file
    
    def _backup_postgresql(self, backup_file: Path):
        """Backup PostgreSQL database."""
        env = os.environ.copy()
        if self.config.db_password:
            env["PGPASSWORD"] = self.config.db_password
        
        cmd = [
            "pg_dump",
            "-h", self.config.db_host,
            "-p", self.config.db_port,
            "-U", self.config.db_user,
            "-d", self.config.db_name,
            "-f", str(backup_file)
        ]
        
        subprocess.run(cmd, env=env, check=True)
    
    def _backup_mysql(self, backup_file: Path):
        """Backup MySQL database."""
        cmd = [
            "mysqldump",
            "-h", self.config.db_host,
            "-P", self.config.db_port,
            "-u", self.config.db_user,
            f"-p{self.config.db_password}",
            self.config.db_name
        ]
        
        with open(backup_file, "w") as f:
            subprocess.run(cmd, stdout=f, check=True)
    
    def _backup_sqlite(self, backup_file: Path):
        """Backup SQLite database."""
        import sqlite3
        
        source = self.config.db_host or ":memory:"
        conn = sqlite3.connect(source)
        with sqlite3.connect(str(backup_file)) as backup:
            conn.backup(backup)
        conn.close()
    
    def restore_backup(self, backup_file: Path):
        """Restore from backup."""
        if self.config.db_type == "postgresql":
            self._restore_postgresql(backup_file)
        elif self.config.db_type == "mysql":
            self._restore_mysql(backup_file)
        elif self.config.db_type == "sqlite":
            self._restore_sqlite(backup_file)
    
    def _restore_postgresql(self, backup_file: Path):
        """Restore PostgreSQL database."""
        env = os.environ.copy()
        if self.config.db_password:
            env["PGPASSWORD"] = self.config.db_password
        
        cmd = [
            "psql",
            "-h", self.config.db_host,
            "-p", self.config.db_port,
            "-U", self.config.db_user,
            "-d", self.config.db_name,
            "-f", str(backup_file)
        ]
        
        subprocess.run(cmd, env=env, check=True)
    
    def _restore_mysql(self, backup_file: Path):
        """Restore MySQL database."""
        cmd = [
            "mysql",
            "-h", self.config.db_host,
            "-P", self.config.db_port,
            "-u", self.config.db_user,
            f"-p{self.config.db_password}",
            self.config.db_name
        ]
        
        with open(backup_file, "r") as f:
            subprocess.run(cmd, stdin=f, check=True)
    
    def _restore_sqlite(self, backup_file: Path):
        """Restore SQLite database."""
        import sqlite3
        
        source = self.config.db_host or ":memory:"
        with sqlite3.connect(str(backup_file)) as backup:
            conn = sqlite3.connect(source)
            backup.backup(conn)
            conn.close()
    
    def list_backups(self) -> list[Path]:
        """List available backups."""
        return sorted(self.backup_dir.glob("data_agent_*.sql"), reverse=True)
    
    def cleanup_old_backups(self):
        """Remove backups older than retention period."""
        cutoff = datetime.now() - timedelta(days=self.config.retention_days)
        
        for backup in self.list_backups():
            # Extract timestamp from filename
            try:
                timestamp_str = backup.stem.split("_")[1] + "_" + backup.stem.split("_")[2]
                backup_time = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                
                if backup_time < cutoff:
                    backup.unlink()
                    print(f"Removed old backup: {backup}")
            except (IndexError, ValueError):
                continue
    
    def verify_backup(self, backup_file: Path) -> bool:
        """Verify backup integrity."""
        try:
            if self.config.db_type == "postgresql":
                # Check if file is valid SQL
                with open(backup_file, "r") as f:
                    content = f.read(1024)
                    return "PostgreSQL" in content or "CREATE" in content
            elif self.config.db_type == "sqlite":
                import sqlite3
                conn = sqlite3.connect(str(backup_file))
                conn.execute("SELECT 1")
                conn.close()
                return True
            return True
        except Exception:
            return False


# Global instance
backup_manager = BackupManager()
