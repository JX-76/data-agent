"""SchemaRecall: Automatic schema retrieval and caching.

Automatically recalls relevant table schemas based on user queries.
Uses embedding similarity to find relevant tables/columns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("schema_recall")


@dataclass
class TableSchema:
    """Schema information for a single table."""
    name: str
    columns: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    primary_key: str | None = None
    foreign_keys: list[dict[str, Any]] = field(default_factory=list)
    indexes: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": self.columns,
            "description": self.description,
            "primary_key": self.primary_key,
            "foreign_keys": self.foreign_keys,
            "indexes": self.indexes,
        }


@dataclass
class SchemaRecallResult:
    """Result of schema recall."""
    tables: list[TableSchema]
    confidence: float
    query: str


class SchemaRecall:
    """Automatic schema recall based on query relevance."""
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        self._schema_cache: dict[str, TableSchema] = {}
        self._embedding_cache: dict[str, list[float]] = {}
    
    def recall(self, query: str, top_k: int = 3) -> SchemaRecallResult:
        """Recall relevant schemas for a query.
        
        Args:
            query: User query
            top_k: Number of top tables to return
        
        Returns:
            Schema recall result
        """
        # Get all table names
        all_tables = self._get_all_tables()
        
        # Score relevance (simple keyword matching for now)
        scored = []
        for table in all_tables:
            score = self._score_relevance(query, table)
            scored.append((table, score))
        
        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Return top-k
        top_tables = [t for t, _ in scored[:top_k]]
        
        return SchemaRecallResult(
            tables=top_tables,
            confidence=scored[0][1] if scored else 0.0,
            query=query,
        )
    
    def _get_all_tables(self) -> list[TableSchema]:
        """Get all tables from database."""
        if not self.db:
            return []
        
        try:
            cursor = self.db.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = []
            for row in cursor.fetchall():
                table_name = row[0]
                schema = self._get_table_schema(table_name)
                tables.append(schema)
            return tables
        except Exception as e:
            logger.warning("schema_recall_failed", error=str(e))
            return []
    
    def _get_table_schema(self, table_name: str) -> TableSchema:
        """Get schema for a specific table."""
        if table_name in self._schema_cache:
            return self._schema_cache[table_name]
        
        if not self.db:
            return TableSchema(name=table_name)
        
        try:
            cursor = self.db.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = []
            for row in cursor.fetchall():
                columns.append({
                    "name": row[1],
                    "type": row[2],
                    "not_null": row[3],
                    "default": row[4],
                    "pk": row[5],
                })
            
            schema = TableSchema(
                name=table_name,
                columns=columns,
            )
            
            self._schema_cache[table_name] = schema
            return schema
        except Exception as e:
            logger.warning("table_schema_failed", table=table_name, error=str(e))
            return TableSchema(name=table_name)
    
    def _score_relevance(self, query: str, table: TableSchema) -> float:
        """Score relevance of a table to a query.
        
        Simple keyword matching. In production, use embeddings.
        """
        query_lower = query.lower()
        table_name = table.name.lower()
        
        # Exact match
        if table_name in query_lower:
            return 1.0
        
        # Partial match
        score = 0.0
        for word in query_lower.split():
            if word in table_name:
                score += 0.5
        
        # Column match
        for col in table.columns:
            col_name = col["name"].lower()
            if col_name in query_lower:
                score += 0.3
        
        return min(score, 1.0)
    
    def get_schema_for_query(self, query: str) -> dict[str, Any]:
        """Get schema information for a query.
        
        Args:
            query: User query
        
        Returns:
            Schema information
        """
        result = self.recall(query)
        
        return {
            "query": query,
            "tables": [t.to_dict() for t in result.tables],
            "confidence": result.confidence,
        }


def recall_schema(query: str, db_connection=None) -> dict[str, Any]:
    """Convenience function to recall schema for a query.
    
    Args:
        query: User query
        db_connection: Optional database connection
    
    Returns:
        Schema information
    """
    recall = SchemaRecall(db_connection)
    return recall.get_schema_for_query(query)
