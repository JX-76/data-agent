# -*- coding: utf-8 -*-
"""Schema introspection helpers for readonly database adapters."""


class SchemaIntrospector(object):
    """Small adapter-agnostic schema introspection facade."""

    def __init__(self, db):
        self.db = db

    def describe_schema(self):
        if self.db is None:
            return {}
        if hasattr(self.db, "describe_schema"):
            return self.db.describe_schema()
        return {}

    def list_tables(self):
        return sorted(self.describe_schema().keys())

    def describe_table(self, table_name):
        return self.describe_schema().get(table_name, [])

    def to_semantic_tables(self):
        tables = {}
        for table_name, columns in self.describe_schema().items():
            normalized_columns = []
            for col in columns:
                if isinstance(col, dict):
                    normalized_columns.append(dict(col))
                else:
                    normalized_columns.append({"name": col, "type": None})
            tables[table_name] = {"name": table_name, "columns": normalized_columns}
        return tables


def introspect_schema(db):
    return SchemaIntrospector(db).describe_schema()


def build_semantic_table_index(db):
    return SchemaIntrospector(db).to_semantic_tables()


__all__ = ["SchemaIntrospector", "introspect_schema", "build_semantic_table_index"]
