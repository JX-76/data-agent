# -*- coding: utf-8 -*-
"""Structured SQL preflight checks for the execution boundary.

The preflight is intentionally fail-closed for production profiles.  It keeps
legacy lightweight checks, then adds AST-level inspection when ``sqlglot`` is
available.  The AST pass walks every table/column/subquery/UNION reference so
nested exfiltration attempts cannot hide behind an outer SELECT whitelist.
"""
from __future__ import unicode_literals

import re

try:  # optional dependency; production installs it from requirements.txt
    import sqlglot
    from sqlglot import expressions as exp
except Exception:  # pragma: no cover - exercised in environments without sqlglot
    sqlglot = None
    exp = None

_READONLY_PREFIX = re.compile(r"^\s*(with|select)\b", re.I)
_DANGEROUS_TOKENS = re.compile(r"\b(delete|update|insert|drop|alter|truncate|grant|revoke|attach|pragma|copy|merge|call|exec|execute)\b", re.I)
_COMMENT_TOKENS = re.compile(r"(--|/\*|\*/)")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_FORBIDDEN_TABLE_HINTS = set([
    "users", "user", "accounts", "account", "password", "secrets", "secret",
    "credentials", "credential", "api_keys", "apikeys", "tokens", "token",
    "bank_cards", "id_cards", "pii", "admin_users",
])
_DEFAULT_FORBIDDEN_FIELD_HINTS = set([
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "credential", "id_card", "idcard", "bank_card", "bankcard", "phone",
    "mobile", "email", "address", "real_name", "ssn",
])


def _metadata_validation(plan, catalog):
    """Validate the requested semantic objects before database execution."""
    errors = []
    warnings = []
    if not plan or not catalog:
        return errors, warnings
    metrics = catalog.get("metrics") or {}
    dimensions = catalog.get("dimensions") or {}
    models = catalog.get("models") or {}
    metric_id = plan.get("metric") or "gmv"
    model_id = plan.get("model") or "order_detail"
    metric = metrics.get(metric_id)
    if not metric:
        errors.append("metadata: unknown metric %s" % metric_id)
    if models and model_id not in models:
        errors.append("metadata: unknown model %s" % model_id)
    allowed = set((metric or {}).get("allowed_dimensions") or [])
    for dimension in plan.get("dimensions") or []:
        if dimension not in dimensions:
            errors.append("metadata: unknown dimension %s" % dimension)
        elif allowed and dimension not in allowed:
            errors.append("metadata: dimension %s not allowed for metric %s" % (dimension, metric_id))
    return errors, warnings


def _norm_identifier(value):
    text = str(value or "").strip().strip('`"[]')
    if "." in text:
        text = text.split(".")[-1]
    return text.lower()


def _catalog_allowed_objects(metadata_catalog):
    """Extract table/field allowlists from the semantic catalog if present."""
    catalog = metadata_catalog or {}
    tables = set()
    fields = set()
    for key, table in (catalog.get("tables") or {}).items():
        tables.add(_norm_identifier(key))
        if isinstance(table, dict):
            if table.get("table"):
                tables.add(_norm_identifier(table.get("table")))
            if table.get("name"):
                tables.add(_norm_identifier(table.get("name")))
            for f in table.get("fields") or table.get("columns") or []:
                if isinstance(f, dict):
                    fields.add(_norm_identifier(f.get("name") or f.get("field")))
                else:
                    fields.add(_norm_identifier(f))
    for model in (catalog.get("models") or {}).values():
        if isinstance(model, dict):
            for key in ("table", "table_name", "from"):
                if model.get(key):
                    tables.add(_norm_identifier(model.get(key)))
            for f in model.get("fields") or model.get("columns") or []:
                fields.add(_norm_identifier(f.get("name") if isinstance(f, dict) else f))
    for metric in (catalog.get("metrics") or {}).values():
        if isinstance(metric, dict):
            for key in ("field", "time_field"):
                if metric.get(key):
                    fields.add(_norm_identifier(metric.get(key)))
    for dim in (catalog.get("dimensions") or {}).values():
        if isinstance(dim, dict) and dim.get("field"):
            fields.add(_norm_identifier(dim.get("field")))
    tables.discard("")
    fields.discard("")
    return tables, fields


def _looks_sensitive(name, hints):
    value = _norm_identifier(name)
    if not value:
        return False
    if value in hints:
        return True
    return any(part in hints for part in re.split(r"[^a-z0-9]+", value) if part)


def _fallback_static_structure_scan(text, metadata_catalog=None, allow_subquery=False, allow_union=False, allow_join=True):
    """Conservative fallback when sqlglot is not installed.

    It is not a substitute for AST parsing, but it still blocks the common
    bypasses from the red-team set instead of silently accepting them.
    """
    errors = []
    low = (text or "").lower()
    if not allow_union and re.search(r"\bunion\b", low):
        errors.append("AST: UNION is forbidden unless explicitly allowed")
    if not allow_subquery and re.search(r"\bfrom\s*\(", low):
        errors.append("AST: subquery in FROM is forbidden unless explicitly allowed")
    if not allow_join and re.search(r"\bjoin\b", low):
        errors.append("AST: JOIN is forbidden unless explicitly allowed")
    for table in re.findall(r"\bfrom\s+([A-Za-z_][A-Za-z0-9_\.]*)|\bjoin\s+([A-Za-z_][A-Za-z0-9_\.]*)", low):
        name = table[0] or table[1]
        if _looks_sensitive(name, _DEFAULT_FORBIDDEN_TABLE_HINTS):
            errors.append("AST: forbidden sensitive table reference %s" % name)
    for col in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", low):
        if _looks_sensitive(col, _DEFAULT_FORBIDDEN_FIELD_HINTS):
            errors.append("AST: forbidden sensitive field reference %s" % col)
    allowed_tables, _ = _catalog_allowed_objects(metadata_catalog)
    if allowed_tables:
        for name in re.findall(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_\.]*)", low):
            norm = _norm_identifier(name)
            if norm not in allowed_tables and not norm.startswith("d") and not norm.startswith("_"):
                errors.append("AST: table %s is not in metadata allowlist" % norm)
    return errors, {"available": False, "parser": "fallback_static_scan"}


def _ast_validation(text, metadata_catalog=None, allow_subquery=False, allow_union=False, allow_join=True, sql_dialect=None):
    errors = []
    warnings = []
    allowed_tables, allowed_fields = _catalog_allowed_objects(metadata_catalog)
    ast_meta = {"available": sqlglot is not None, "parser": "sqlglot" if sqlglot is not None else "fallback_static_scan",
                "tables": [], "columns": [], "has_union": False, "has_subquery": False, "has_join": False}
    if sqlglot is None:
        fallback_errors, fallback_meta = _fallback_static_structure_scan(text, metadata_catalog, allow_subquery, allow_union, allow_join)
        ast_meta.update(fallback_meta)
        return fallback_errors, warnings, ast_meta
    try:
        statements = sqlglot.parse(text, read=sql_dialect)
    except Exception as exc:
        return ["AST: SQL parse failed: %s" % str(exc)], warnings, ast_meta
    if len(statements) != 1:
        errors.append("AST: exactly one SQL statement is allowed")
    for tree in statements:
        if tree is None:
            errors.append("AST: empty statement")
            continue
        if not isinstance(tree, (exp.Select, exp.Union)) and tree.key not in ("select", "with", "union"):
            errors.append("AST: only SELECT/WITH query statements are allowed")
        for node in tree.walk():
            if isinstance(node, exp.Union):
                ast_meta["has_union"] = True
                if not allow_union:
                    errors.append("AST: UNION is forbidden unless explicitly allowed")
            if isinstance(node, exp.Join):
                ast_meta["has_join"] = True
                if not allow_join:
                    errors.append("AST: JOIN is forbidden unless explicitly allowed")
            if isinstance(node, exp.Subquery):
                ast_meta["has_subquery"] = True
                if not allow_subquery:
                    errors.append("AST: subquery is forbidden unless explicitly allowed")
            if isinstance(node, exp.Table):
                name = _norm_identifier(node.name)
                if name:
                    ast_meta["tables"].append(name)
                    if _looks_sensitive(name, _DEFAULT_FORBIDDEN_TABLE_HINTS):
                        errors.append("AST: forbidden sensitive table reference %s" % name)
                    if allowed_tables and name not in allowed_tables and not name.startswith("d") and not name.startswith("_"):
                        errors.append("AST: table %s is not in metadata allowlist" % name)
            if isinstance(node, exp.Column):
                name = _norm_identifier(node.name)
                if name:
                    ast_meta["columns"].append(name)
                    if _looks_sensitive(name, _DEFAULT_FORBIDDEN_FIELD_HINTS):
                        errors.append("AST: forbidden sensitive field reference %s" % name)
                    if allowed_fields and name not in allowed_fields and name != "*":
                        # Keep this a warning: SQL compiled from semantic expressions may use aliases not enumerated.
                        warnings.append("AST: column %s is not in metadata field allowlist" % name)
    ast_meta["tables"] = sorted(set(ast_meta["tables"]))
    ast_meta["columns"] = sorted(set(ast_meta["columns"]))
    errors = sorted(set(errors))
    warnings = sorted(set(warnings))
    return errors, warnings, ast_meta


def validate_sql_preflight(sql, validator=None, require_runtime_cte=True, plan=None, metadata_catalog=None,
                           allow_subquery=False, allow_union=False, allow_join=True, sql_dialect=None):
    """Return a stable SQL preflight report without executing the query.

    ``plan`` and ``metadata_catalog`` are optional to preserve legacy call sites.
    ``allow_subquery``/``allow_union`` default to fail-closed and should only be
    enabled for trusted SQL emitted by the semantic compiler after review.
    """
    text = sql or ""
    errors = []
    warnings = []

    if not text.strip():
        errors.append("sql is empty")
    elif not _READONLY_PREFIX.match(text):
        errors.append("sql must start with SELECT or WITH")
    if _DANGEROUS_TOKENS.search(text):
        errors.append("sql contains forbidden mutating statement")
    if _COMMENT_TOKENS.search(text):
        errors.append("sql comments are forbidden")
    if ";" in text.rstrip().rstrip(";"):
        errors.append("multiple SQL statements are forbidden")

    ast_errors, ast_warnings, ast_meta = ([], [], {"available": sqlglot is not None})
    if text.strip() and not errors:
        ast_errors, ast_warnings, ast_meta = _ast_validation(
            text, metadata_catalog=metadata_catalog, allow_subquery=allow_subquery,
            allow_union=allow_union, allow_join=allow_join, sql_dialect=sql_dialect)
        errors.extend(ast_errors)
        warnings.extend(ast_warnings)

    legacy_ok = None
    legacy_reason = None
    if not errors and validator is not None:
        legacy_ok, legacy_reason = validator(text)
        if not legacy_ok:
            errors.append(legacy_reason or "legacy SQL validator rejected query")
    elif require_runtime_cte and not errors:
        low = text.lower()
        if not low.strip().startswith("with") or "select * from d" not in low:
            errors.append("runtime SQL must be a CTE chain ending in a dataid")

    metadata_errors, metadata_warnings = _metadata_validation(plan, metadata_catalog)
    errors.extend(metadata_errors)
    warnings.extend(metadata_warnings)
    normalized = text.strip().rstrip(";")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "contract": "sql_preflight_v1",
        "statement_type": "with" if normalized.lower().startswith("with") else "select",
        "sql_length": len(text),
        "legacy_validator": {"applied": validator is not None, "valid": legacy_ok, "reason": legacy_reason},
        "metadata": {"applied": metadata_catalog is not None, "catalog_fingerprint": (metadata_catalog or {}).get("fingerprint"),
                     "errors": metadata_errors, "warnings": metadata_warnings},
        "ast": ast_meta,
    }


__all__ = ["validate_sql_preflight"]
