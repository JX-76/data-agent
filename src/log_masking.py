"""Log masking and sanitization for sensitive data.

Provides:
- API key masking
- Password masking
- PII detection and masking
- Structured log filtering
"""

from __future__ import annotations

import re
from typing import Any


class LogMasker:
    """Masks sensitive data in log messages."""
    
    # Patterns for sensitive data
    PATTERNS = {
        "api_key": [
            re.compile(r'(api[_-]?key[=:\s]+)([\w-]+)', re.IGNORECASE),
            re.compile(r'(x-api-key[=:\s]+)([\w-]+)', re.IGNORECASE),
            re.compile(r'(bearer\s+)([\w-]+)', re.IGNORECASE),
        ],
        "password": [
            re.compile(r'(password[=:\s]+)(\S+)', re.IGNORECASE),
            re.compile(r'(passwd[=:\s]+)(\S+)', re.IGNORECASE),
            re.compile(r'(pwd[=:\s]+)(\S+)', re.IGNORECASE),
        ],
        "token": [
            re.compile(r'(token[=:\s]+)([\w-]+)', re.IGNORECASE),
            re.compile(r'(access[_-]?token[=:\s]+)([\w-]+)', re.IGNORECASE),
            re.compile(r'(refresh[_-]?token[=:\s]+)([\w-]+)', re.IGNORECASE),
        ],
        "secret": [
            re.compile(r'(secret[=:\s]+)(\S+)', re.IGNORECASE),
            re.compile(r'(client[_-]?secret[=:\s]+)(\S+)', re.IGNORECASE),
            re.compile(r'(app[_-]?secret[=:\s]+)(\S+)', re.IGNORECASE),
        ],
        "credit_card": [
            re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
        ],
        "email": [
            re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
        ],
        "phone": [
            re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
        ],
    }
    
    MASK = "***MASKED***"
    
    def mask_string(self, text: str) -> str:
        """Mask sensitive data in a string.
        
        Args:
            text: Input string
            
        Returns:
            Masked string
        """
        if not text:
            return text
        
        for pattern_list in self.PATTERNS.values():
            for pattern in pattern_list:
                text = pattern.sub(lambda m: m.group(1) + self.MASK if len(m.groups()) > 1 else self.MASK, text)
        
        return text
    
    def mask_dict(self, data: dict) -> dict:
        """Mask sensitive data in a dictionary.
        
        Args:
            data: Dictionary to mask
            
        Returns:
            Masked dictionary
        """
        if not isinstance(data, dict):
            return data
        
        result = {}
        for key, value in data.items():
            # Check if key itself is sensitive
            if self._is_sensitive_key(key):
                result[key] = self.MASK
            elif isinstance(value, dict):
                result[key] = self.mask_dict(value)
            elif isinstance(value, list):
                result[key] = [self.mask_dict(item) if isinstance(item, dict) else item for item in value]
            elif isinstance(value, str):
                result[key] = self.mask_string(value)
            else:
                result[key] = value
        
        return result
    
    def _is_sensitive_key(self, key: str) -> bool:
        """Check if a key name indicates sensitive data."""
        sensitive_keys = {
            "password", "passwd", "pwd", "secret", "token",
            "api_key", "apikey", "access_token", "refresh_token",
            "client_secret", "app_secret", "private_key",
            "credit_card", "cc_number", "ssn"
        }
        return key.lower() in sensitive_keys


class StructuredLogFilter:
    """Filters sensitive data from structured logs."""
    
    def __init__(self):
        self.masker = LogMasker()
    
    def filter(self, event_dict: dict) -> dict:
        """Filter sensitive data from a structlog event.
        
        Args:
            event_dict: structlog event dictionary
            
        Returns:
            Filtered event dictionary
        """
        # Mask the message
        if "event" in event_dict:
            event_dict["event"] = self.masker.mask_string(event_dict["event"])
        
        # Mask other fields
        for key in ["query", "sql", "error", "message"]:
            if key in event_dict and isinstance(event_dict[key], str):
                event_dict[key] = self.masker.mask_string(event_dict[key])
        
        # Recursively mask nested dicts
        for key, value in event_dict.items():
            if isinstance(value, dict):
                event_dict[key] = self.masker.mask_dict(value)
        
        return event_dict


# Global instances
log_masker = LogMasker()
log_filter = StructuredLogFilter()
