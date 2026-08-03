# -*- coding: utf-8 -*-
"""Lightweight memory store for session/task context.

This module keeps the first version intentionally small: a shared in-memory store
that can later be replaced with persistence without changing the calling API.
"""


class MemoryItem(object):
    def __init__(self, scope, key, value, metadata=None):
        self.scope = scope
        self.key = key
        self.value = value
        self.metadata = metadata or {}


class MemoryStore(object):
    def __init__(self):
        self._items = []

    def remember(self, scope, key, value, **metadata):
        item = MemoryItem(scope=scope, key=key, value=value, metadata=dict(metadata))
        self._items.append(item)
        return item

    def recall(self, scope=None, key=None):
        items = self._items
        if scope is not None:
            items = [i for i in items if i.scope == scope]
        if key is not None:
            items = [i for i in items if i.key == key]
        return list(items)

    def clear(self, scope=None):
        if scope is None:
            self._items[:] = []
            return
        self._items = [i for i in self._items if i.scope != scope]


_MEMORY = MemoryStore()


def get_memory_store():
    return _MEMORY


__all__ = ["MemoryItem", "MemoryStore", "get_memory_store"]
