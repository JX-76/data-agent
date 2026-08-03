"""File watcher for hot-reloading business rules.

Usage:
    from rule_watcher import RuleWatcher
    
    watcher = RuleWatcher("rules/business_rules.yaml", router)
    watcher.start()  # Start watching in background thread
    ...
    watcher.stop()   # Stop watching
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("rule_watcher")

# Try to import watchdog, fallback to polling if not available
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = None


class RuleFileHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    """Handles file change events for rule files."""
    
    def __init__(self, router, callback=None):
        self.router = router
        self.callback = callback
        self._last_reload = 0
    
    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle_change(event.src_path)
    
    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_change(event.src_path)
    
    def _handle_change(self, path):
        # Debounce: only reload if last reload was > 1 second ago
        now = time.time()
        if now - self._last_reload < 1:
            return
        self._last_reload = now
        
        logger.info(f"Rule file changed: {path}")
        try:
            self.router.reload()
            if self.callback:
                self.callback()
            logger.info("Rules reloaded successfully")
        except Exception as e:
            logger.error(f"Failed to reload rules: {e}")


class RuleWatcher:
    """Watches business rule files for changes and auto-reloads.
    
    Uses watchdog library if available, falls back to polling.
    """
    
    def __init__(self, rules_path: str, router, poll_interval: float = 5.0):
        """
        Args:
            rules_path: Path to rules YAML file
            router: BusinessRuleRouter instance to reload
            poll_interval: Polling interval in seconds (fallback mode)
        """
        self.rules_path = Path(rules_path)
        self.router = router
        self.poll_interval = poll_interval
        self._observer = None
        self._polling_thread = None
        self._stop_event = threading.Event()
        self._last_mtime = 0
    
    def start(self):
        """Start watching for changes."""
        if WATCHDOG_AVAILABLE:
            self._start_watchdog()
        else:
            self._start_polling()
    
    def stop(self):
        """Stop watching for changes."""
        self._stop_event.set()
        if self._observer:
            self._observer.stop()
            self._observer.join()
        if self._polling_thread:
            self._polling_thread.join(timeout=1)
    
    def _start_watchdog(self):
        """Start watchdog-based file monitoring."""
        handler = RuleFileHandler(self.router)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.rules_path.parent), recursive=False)
        self._observer.start()
        logger.info(f"Started watchdog watcher for {self.rules_path}")
    
    def _start_polling(self):
        """Start polling-based file monitoring (fallback)."""
        self._polling_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._polling_thread.start()
        logger.info(f"Started polling watcher for {self.rules_path} (interval={self.poll_interval}s)")
    
    def _poll_loop(self):
        """Poll for file changes."""
        while not self._stop_event.is_set():
            try:
                mtime = self.rules_path.stat().st_mtime
                if mtime > self._last_mtime:
                    self._last_mtime = mtime
                    logger.info(f"Rule file changed: {self.rules_path}")
                    try:
                        self.router.reload()
                        logger.info("Rules reloaded successfully")
                    except Exception as e:
                        logger.error(f"Failed to reload rules: {e}")
            except FileNotFoundError:
                pass
            time.sleep(self.poll_interval)
