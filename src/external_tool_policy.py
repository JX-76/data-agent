# -*- coding: utf-8 -*-
"""Policy checks for external tools."""
from __future__ import unicode_literals

import re

from masking_policy import sanitize_output
from permission_policy import PermissionPolicy

try:  # pragma: no cover - Python 3 compatibility
    basestring
except NameError:
    basestring = str


class ExternalToolPolicyResult(object):
    def __init__(self, allowed=True, failure_type=None, errors=None, warnings=None, metadata=None):
        self.allowed = allowed
        self.failure_type = failure_type
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "failure_type": self.failure_type,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


class ExternalToolPolicy(object):
    def __init__(self, permission_policy=None):
        self.permission_policy = permission_policy or PermissionPolicy()

    def validate(self, spec, args, context=None):
        spec = spec or {}
        args = args or {}
        context = context or {}
        errors = []
        warnings = []

        intent = context.get("intent")
        allowed_intents = spec.get("allowed_intents") or []
        if intent and allowed_intents and intent not in allowed_intents:
            errors.append("intent_not_allowed: %s" % intent)

        if spec.get("requires_human_review") and context.get("approval_status") != "approved":
            errors.append("human_review_required")

        permission_plan = spec.get("permission_plan") or {}
        if permission_plan:
            decision = self.permission_policy.evaluate(context.get("access_context") or context, permission_plan, context.get("query") or "")
            if not decision.allowed:
                errors.append(decision.reason or decision.decision)
            if decision.requires_human_review:
                errors.append("human_review_required")
            if decision.masked_fields:
                warnings.append("sensitive_fields_masked: %s" % ",".join(decision.masked_fields))
            context.setdefault("masked_fields", decision.masked_fields)

        if spec.get("side_effect") != "read_only" and not context.get("allow_side_effect"):
            errors.append("side_effect_not_allowed")

        input_schema = spec.get("input_schema") or {}
        allow_unknown = not bool(input_schema.get("additionalProperties") is False)
        schema_errors = self._validate_schema(input_schema, args, prefix="arg", allow_unknown=allow_unknown)
        errors.extend(schema_errors)

        if spec.get("tool_id") == "warehouse.query_sql":
            sql_error = self._validate_readonly_sql(args.get("sql"), spec.get("forbidden_operations") or [])
            if sql_error:
                errors.append(sql_error)

        metadata = {"masked_fields": list(context.get("masked_fields") or spec.get("masked_output_fields") or [])}
        if errors:
            return ExternalToolPolicyResult(False, self._classify(errors), errors, warnings, metadata)
        return ExternalToolPolicyResult(True, None, [], warnings, metadata)

    def validate_output(self, spec, output):
        spec = spec or {}
        output = output or {}
        errors = []
        schema = spec.get("output_schema") or {}
        errors.extend(self._validate_schema(schema, output, prefix="output", allow_unknown=True))
        if "rows" in output and not isinstance(output.get("rows"), list):
            errors.append("output_type_mismatch: rows")
        if "row_count" in output and (not isinstance(output.get("row_count"), int) or isinstance(output.get("row_count"), bool)):
            errors.append("output_type_mismatch: row_count")
        if "schema" in output and not isinstance(output.get("schema"), dict):
            errors.append("output_type_mismatch: schema")
        if "rows" in output and len(output.get("rows") or []) > int(schema.get("max_rows") or 1000):
            errors.append("output_too_large")
        if self._contains_unmasked_sensitive(output, set(spec.get("masked_output_fields") or [])):
            errors.append("unmasked_sensitive_output")
        if errors:
            failure_type = "external_tool_output_too_large" if "output_too_large" in errors else "external_tool_output_contract_error"
            return ExternalToolPolicyResult(False, failure_type, errors, [])
        return ExternalToolPolicyResult(True, None, [], [])

    def sanitize_args(self, spec, args, context=None):
        context = context or {}
        masked = set(context.get("masked_fields") or [])
        masked.update(spec.get("masked_input_fields") or [])
        return sanitize_output(args or {}, masked_fields=masked)

    def sanitize_output(self, spec, output, context=None):
        context = context or {}
        masked = set(context.get("masked_fields") or [])
        masked.update(spec.get("masked_output_fields") or [])
        return sanitize_output(output or {}, masked_fields=masked)

    def _validate_schema(self, schema, args, prefix="arg", allow_unknown=True):
        errors = []
        schema = schema or {}
        args = args or {}
        required = schema.get("required") or []
        props = schema.get("properties") or {}
        if not isinstance(args, dict):
            errors.append("%s_type_mismatch: root" % prefix)
            return errors
        for key in required:
            if key not in args or args.get(key) in (None, ""):
                errors.append("missing_required_%s: %s" % (prefix, key))
        if not allow_unknown:
            for key in args.keys():
                if key not in props:
                    errors.append("unknown_%s: %s" % (prefix, key))
        for key, value in args.items():
            if key not in props:
                continue
            errors.extend(self._validate_value(key, value, props.get(key) or {}, prefix))
        return errors

    def _validate_value(self, path, value, rule, prefix):
        errors = []
        rule = rule or {}
        typ = rule.get("type")
        if typ == "string" and not isinstance(value, basestring):
            errors.append("%s_type_mismatch: %s" % (prefix, path))
        if typ == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append("%s_type_mismatch: %s" % (prefix, path))
        if typ == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            errors.append("%s_type_mismatch: %s" % (prefix, path))
        if typ == "boolean" and not isinstance(value, bool):
            errors.append("%s_type_mismatch: %s" % (prefix, path))
        if typ == "object" and not isinstance(value, dict):
            errors.append("%s_type_mismatch: %s" % (prefix, path))
        if typ == "array" and not isinstance(value, list):
            errors.append("%s_type_mismatch: %s" % (prefix, path))
        if "enum" in rule and value not in (rule.get("enum") or []):
            errors.append("%s_enum_violation: %s" % (prefix, path))
        if typ == "integer" and isinstance(value, int) and not isinstance(value, bool):
            if "min" in rule and value < int(rule.get("min")):
                errors.append("%s_min_violation: %s" % (prefix, path))
            if "max" in rule and value > int(rule.get("max")):
                errors.append("%s_max_violation: %s" % (prefix, path))
        if typ == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            if "min" in rule and value < float(rule.get("min")):
                errors.append("%s_min_violation: %s" % (prefix, path))
            if "max" in rule and value > float(rule.get("max")):
                errors.append("%s_max_violation: %s" % (prefix, path))
        if typ == "string" and isinstance(value, basestring):
            if "min_len" in rule and len(value) < int(rule.get("min_len")):
                errors.append("%s_min_len_violation: %s" % (prefix, path))
            if "max_len" in rule and len(value) > int(rule.get("max_len")):
                errors.append("%s_max_len_violation: %s" % (prefix, path))
            if rule.get("pattern") and not re.search(rule.get("pattern"), value):
                errors.append("%s_pattern_violation: %s" % (prefix, path))
        if typ == "object" and isinstance(value, dict):
            child_schema = {
                "required": rule.get("required") or [],
                "properties": rule.get("properties") or {},
                "additionalProperties": rule.get("additionalProperties"),
            }
            child_unknown = not bool(rule.get("additionalProperties") is False)
            child_errors = self._validate_schema(child_schema, value, prefix=prefix, allow_unknown=child_unknown)
            errors.extend([err.replace(": ", ": %s." % path, 1) if ": " in err else err for err in child_errors])
        if typ == "array" and isinstance(value, list):
            if "min_items" in rule and len(value) < int(rule.get("min_items")):
                errors.append("%s_min_items_violation: %s" % (prefix, path))
            if "max_items" in rule and len(value) > int(rule.get("max_items")):
                errors.append("%s_max_items_violation: %s" % (prefix, path))
            item_rule = rule.get("items") or {}
            for idx, item in enumerate(value):
                errors.extend(self._validate_value("%s[%s]" % (path, idx), item, item_rule, prefix))
        return errors

    def _contains_unmasked_sensitive(self, value, allowed_masked_fields=None):
        allowed_masked_fields = set([str(item).lower() for item in (allowed_masked_fields or [])])
        if isinstance(value, list):
            return any(self._contains_unmasked_sensitive(item, allowed_masked_fields) for item in value)
        if not isinstance(value, dict):
            return False
        for key, item in value.items():
            low = str(key).lower()
            # Declared masked fields are allowed to be raw at validate_output time
            # because executor sanitizes them before returning/storing trace data.
            # Undeclared sensitive-looking keys are blocked to avoid accidental PII leakage.
            sensitive = low not in allowed_masked_fields and any(token in low for token in (
                "email", "phone", "mobile", "password", "secret", "token", "api_key", "id_card", "idcard"))
            if sensitive and item not in (None, "", "***"):
                if not (isinstance(item, basestring) and ("*" in item or item.startswith("hash:"))):
                    return True
            if self._contains_unmasked_sensitive(item, allowed_masked_fields):
                return True
        return False

    def _validate_readonly_sql(self, sql, forbidden):
        text = (sql or "").strip().lower()
        if not (text.startswith("select") or text.startswith("with")):
            return "readonly_violation"
        for op in forbidden:
            pattern = r"(^|\W)%s(\W|$)" % re.escape(str(op).lower())
            if re.search(pattern, text):
                return "forbidden_sql_operation: %s" % op
        return None

    def _classify(self, errors):
        joined = ";".join(errors)
        if "timeout" in joined:
            return "external_tool_timeout"
        if "readonly" in joined or "forbidden_sql" in joined or "side_effect" in joined:
            return "external_tool_policy_denied"
        if "missing_required_arg" in joined or "arg_" in joined or "unknown_arg" in joined:
            return "external_tool_contract_error"
        if "human_review" in joined or "sensitive_field" in joined or "export_or_detail" in joined:
            return "external_tool_human_review_required"
        return "external_tool_policy_denied"


__all__ = ["ExternalToolPolicy", "ExternalToolPolicyResult"]
