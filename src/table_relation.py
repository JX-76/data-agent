"""TableRelation: Table relationship analysis.

Analyzes relationships between tables:
- Foreign key relationships
- Join paths
- Table dependencies
- Relationship types (one-to-one, one-to-many, many-to-many)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("table_relation")


@dataclass
class TableRelationship:
    """A relationship between two tables."""
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_type: str  # one_to_one, one_to_many, many_to_many
    constraint: str | None = None


@dataclass
class TableGraph:
    """Graph of table relationships."""
    tables: list[str] = field(default_factory=list)
    relationships: list[TableRelationship] = field(default_factory=list)
    
    def get_related_tables(self, table: str) -> list[str]:
        """Get all tables related to a given table."""
        related = []
        for rel in self.relationships:
            if rel.from_table == table:
                related.append(rel.to_table)
            elif rel.to_table == table:
                related.append(rel.from_table)
        return list(set(related))
    
    def get_join_path(self, from_table: str, to_table: str) -> list[TableRelationship] | None:
        """Find join path between two tables.
        
        Uses BFS to find shortest path.
        """
        if from_table == to_table:
            return []
        
        # BFS
        from collections import deque
        queue = deque([(from_table, [])])
        visited = {from_table}
        
        while queue:
            current, path = queue.popleft()
            
            for rel in self.relationships:
                if rel.from_table == current and rel.to_table not in visited:
                    new_path = path + [rel]
                    if rel.to_table == to_table:
                        return new_path
                    visited.add(rel.to_table)
                    queue.append((rel.to_table, new_path))
                elif rel.to_table == current and rel.from_table not in visited:
                    new_path = path + [rel]
                    if rel.from_table == to_table:
                        return new_path
                    visited.add(rel.from_table)
                    queue.append((rel.from_table, new_path))
        
        return None


class TableRelationAnalyzer:
    """Analyzes table relationships."""
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        self._graph: TableGraph | None = None
    
    def analyze(self) -> TableGraph:
        """Analyze all table relationships.
        
        Returns:
            Table relationship graph
        """
        if self._graph:
            return self._graph
        
        tables = self._get_all_tables()
        relationships = []
        
        for table in tables:
            fks = self._get_foreign_keys(table)
            for fk in fks:
                relationships.append(TableRelationship(
                    from_table=table,
                    from_column=fk["from"],
                    to_table=fk["table"],
                    to_column=fk["to"],
                    relationship_type="many_to_one",  # Default assumption
                ))
        
        self._graph = TableGraph(
            tables=tables,
            relationships=relationships,
        )
        
        return self._graph
    
    def get_table_graph(self) -> TableGraph:
        """Get the table relationship graph.
        
        Returns:
            Table graph
        """
        return self.analyze()
    
    def suggest_joins(self, tables: list[str]) -> list[TableRelationship]:
        """Suggest joins for a list of tables.
        
        Args:
            tables: List of tables to join
        
        Returns:
            List of suggested relationships
        """
        graph = self.analyze()
        
        if len(tables) < 2:
            return []
        
        # Find relationships between all pairs
        joins = []
        for i, t1 in enumerate(tables):
            for t2 in tables[i+1:]:
                path = graph.get_join_path(t1, t2)
                if path:
                    joins.extend(path)
        
        return joins
    
    def _get_all_tables(self) -> list[str]:
        """Get all tables from database."""
        if not self.db:
            return []
        
        try:
            cursor = self.db.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning("get_tables_failed", error=str(e))
            return []
    
    def _get_foreign_keys(self, table: str) -> list[dict[str, str]]:
        """Get foreign keys for a table.
        
        Args:
            table: Table name
        
        Returns:
            List of foreign key definitions
        """
        if not self.db:
            return []
        
        try:
            cursor = self.db.cursor()
            cursor.execute(f"PRAGMA foreign_key_list({table})")
            fks = []
            for row in cursor.fetchall():
                fks.append({
                    "id": row[0],
                    "seq": row[1],
                    "table": row[2],
                    "from": row[3],
                    "to": row[4],
                    "on_update": row[5],
                    "on_delete": row[6],
                })
            return fks
        except Exception as e:
            logger.warning("get_fks_failed", table=table, error=str(e))
            return []


def analyze_table_relations(db_connection=None) -> TableGraph:
    """Convenience function to analyze table relations.
    
    Args:
        db_connection: Database connection
    
    Returns:
        Table relationship graph
    """
    analyzer = TableRelationAnalyzer(db_connection)
    return analyzer.analyze()
