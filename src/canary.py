"""Canary deployment support for Data Agent.

Provides:
- Traffic splitting
- Feature flags
- Rollback support
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional


class FeatureFlag:
    """Feature flag for gradual rollout."""
    
    def __init__(self, name: str, enabled: bool = False, percentage: int = 0):
        self.name = name
        self.enabled = enabled
        self.percentage = percentage
    
    def is_enabled(self, user_id: Optional[str] = None) -> bool:
        """Check if feature is enabled for user."""
        if not self.enabled:
            return False
        
        if self.percentage >= 100:
            return True
        
        if user_id:
            # Consistent hashing for same user
            hash_val = int(hashlib.md5(f"{self.name}:{user_id}".encode()).hexdigest(), 16)
            return (hash_val % 100) < self.percentage
        
        return random.randint(0, 99) < self.percentage


class FeatureFlags:
    """Manager for feature flags."""
    
    def __init__(self):
        self._flags: dict[str, FeatureFlag] = {}
    
    def register(self, name: str, enabled: bool = False, percentage: int = 0):
        """Register a feature flag."""
        self._flags[name] = FeatureFlag(name, enabled, percentage)
    
    def is_enabled(self, name: str, user_id: Optional[str] = None) -> bool:
        """Check if feature is enabled."""
        flag = self._flags.get(name)
        if not flag:
            return False
        return flag.is_enabled(user_id)
    
    def enable(self, name: str):
        """Enable a feature flag."""
        if name in self._flags:
            self._flags[name].enabled = True
    
    def disable(self, name: str):
        """Disable a feature flag."""
        if name in self._flags:
            self._flags[name].enabled = False
    
    def set_percentage(self, name: str, percentage: int):
        """Set rollout percentage."""
        if name in self._flags:
            self._flags[name].percentage = max(0, min(100, percentage))


class CanaryDeployment:
    """Canary deployment with traffic splitting."""
    
    def __init__(self):
        self.canary_percentage = 0
        self.stable_version = "v1"
        self.canary_version = "v2"
    
    def set_canary_percentage(self, percentage: int):
        """Set canary traffic percentage."""
        self.canary_percentage = max(0, min(100, percentage))
    
    def route(self, user_id: str) -> str:
        """Route user to stable or canary version."""
        if self.canary_percentage <= 0:
            return self.stable_version
        
        if self.canary_percentage >= 100:
            return self.canary_version
        
        # Consistent hashing
        hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
        if (hash_val % 100) < self.canary_percentage:
            return self.canary_version
        return self.stable_version
    
    def promote(self):
        """Promote canary to stable."""
        self.stable_version = self.canary_version
        self.canary_percentage = 0
    
    def rollback(self):
        """Rollback canary."""
        self.canary_percentage = 0


# Global instances
feature_flags = FeatureFlags()
canary = CanaryDeployment()
