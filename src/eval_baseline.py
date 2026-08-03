# -*- coding: utf-8 -*-
"""Evaluation baseline and deterministic Agent benchmark helpers.

This module keeps evaluation logic independent from any specific agent entrypoint.
Callers provide cases plus a runner(query)->result function; the evaluator returns
stable aggregate metrics and per-case failures.
"""

try:
    import json
except ImportError:
    json = None


BASELINE_DEFAULTS = {
    "route_accuracy_min": 0.95,
    "task_type_accuracy_min": 0.90,
    "contract_pass_rate_min": 0.98,
    "execution_success_rate_min": 0.90,
    "clarification_hit_rate_min": 0.90,
    "sql_success_min": 0.90,
    "blocked_precision_min": 0.98,
    "clarification_precision_min": 0.90,
    "avg_latency_ms_max": 1500,
}


class EvalBaseline(object):
    def __init__(self, name="default", metrics=None, metadata=None):
        self.name = name
        self.metrics = dict(BASELINE_DEFAULTS)
        if metrics:
            self.metrics.update(metrics)
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "name": self.name,
            "metrics": dict(self.metrics),
            "metadata": dict(self.metadata),
        }

    def to_json(self):
        if json is None:
            return str(self.to_dict())
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        return cls(
            name=data.get("name", "default"),
            metrics=data.get("metrics") or {},
            metadata=data.get("metadata") or {},
        )


class EvalCase(object):
    def __init__(self, case_id, query, expected=None, category="", metadata=None):
        self.case_id = case_id
        self.query = query
        self.expected = expected or {}
        self.category = category
        self.metadata = metadata or {}

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        return cls(
            case_id=data.get("id") or data.get("case_id") or data.get("query", "case"),
            query=data.get("query", ""),
            expected=data.get("expected") or {},
            category=data.get("category", ""),
            metadata=data.get("metadata") or {},
        )

    def to_dict(self):
        return {
            "id": self.case_id,
            "query": self.query,
            "expected": dict(self.expected),
            "category": self.category,
            "metadata": dict(self.metadata),
        }


class EvalGateResult(object):
    def __init__(self, passed=False, failures=None, checked=None):
        self.passed = bool(passed)
        self.failures = failures or []
        self.checked = checked or {}

    def to_dict(self):
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "checked": dict(self.checked),
        }


class EvalRunResult(object):
    def __init__(self, total=0, passed=0, failed=None, metrics=None):
        self.total = total
        self.passed = passed
        self.failed = failed or []
        self.metrics = metrics or {}

    def to_dict(self):
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": list(self.failed),
            "metrics": dict(self.metrics),
        }


def _normalize(v):
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, list):
        return list(v)
    return v


def _expected_keys(expected):
    return [k for k in ["status", "intent", "task_type", "metric", "dimensions", "chart_type"] if k in expected]


def _get_chart_type(result):
    chart = result.get("chart") or result.get("chart_hint") or result.get("visualization") or {}
    if isinstance(chart, dict):
        return chart.get("type")
    return None


def evaluate_case(case, result):
    """Return a list of deterministic mismatch messages for one case."""
    expected = case.expected if isinstance(case, EvalCase) else (case.get("expected") or {})
    errors = []
    for key in _expected_keys(expected):
        if key == "chart_type":
            got = _get_chart_type(result)
        else:
            got = result.get(key)
        exp = expected.get(key)
        if _normalize(got) != _normalize(exp):
            errors.append("%s expected=%s got=%s" % (key, exp, got))

    if expected.get("status") == "ok" and expected.get("requires_sql", True) and not result.get("sql"):
        errors.append("sql missing")
    return errors


def evaluate_cases(cases, runner):
    """Evaluate benchmark cases with a provided runner(query)->dict callable."""
    normalized_cases = [c if isinstance(c, EvalCase) else EvalCase.from_dict(c) for c in (cases or [])]
    failed = []
    counters = {
        "route": [0, 0],
        "task_type": [0, 0],
        "contract": [0, 0],
        "execution": [0, 0],
        "clarification": [0, 0],
    }

    for case in normalized_cases:
        result = runner(case.query) or {}
        errors = evaluate_case(case, result)
        exp = case.expected

        if "intent" in exp:
            counters["route"][1] += 1
            if result.get("intent") == exp.get("intent"):
                counters["route"][0] += 1
        if "task_type" in exp:
            counters["task_type"][1] += 1
            if result.get("task_type") == exp.get("task_type"):
                counters["task_type"][0] += 1
        counters["contract"][1] += 1
        if result.get("status") in ("ok", "blocked", "need_clarification", "error"):
            counters["contract"][0] += 1
        if exp.get("status") == "ok":
            counters["execution"][1] += 1
            if result.get("status") == "ok" and (not exp.get("requires_sql", True) or bool(result.get("sql"))):
                counters["execution"][0] += 1
        if exp.get("status") == "need_clarification":
            counters["clarification"][1] += 1
            if result.get("status") == "need_clarification":
                counters["clarification"][0] += 1

        if errors:
            failed.append({
                "id": case.case_id,
                "query": case.query,
                "category": case.category,
                "errors": errors,
                "result": result,
            })

    total = len(normalized_cases)
    passed = total - len(failed)

    def rate(name):
        good, denom = counters[name]
        if denom == 0:
            return None
        return good * 1.0 / denom

    metrics = {
        "pass_rate": passed * 1.0 / total if total else 0.0,
        "route_accuracy": rate("route"),
        "task_type_accuracy": rate("task_type"),
        "contract_pass_rate": rate("contract"),
        "execution_success_rate": rate("execution"),
        "clarification_hit_rate": rate("clarification"),
    }
    return EvalRunResult(total=total, passed=passed, failed=failed, metrics=metrics)


DEFAULT_EVAL_BASELINE = EvalBaseline()


def get_eval_baseline():
    return DEFAULT_EVAL_BASELINE


def build_eval_baseline(metrics=None, metadata=None, name="custom"):
    return EvalBaseline(name=name, metrics=metrics, metadata=metadata)


def evaluate_gate(run_result, baseline=None):
    """Compare run metrics against baseline thresholds.

    Supported naming convention:
    - ``*_min`` means actual metric must be >= threshold
    - ``*_max`` means actual metric must be <= threshold
    Missing/None metrics are ignored so partial benchmarks can still run.
    """
    if hasattr(run_result, "to_dict"):
        run_result = run_result.to_dict()
    if isinstance(run_result, list):
        failures = []
        checked = {}
        for idx, item in enumerate(run_result):
            if isinstance(item, dict) and item.get("passed") is False:
                failures.append("item[%s] failed: %s" % (idx, item.get("query") or item.get("suite") or "unknown"))
        return EvalGateResult(passed=(len(failures) == 0), failures=failures, checked=checked)
    run_result = run_result or {}
    baseline = baseline or DEFAULT_EVAL_BASELINE
    if hasattr(baseline, "to_dict"):
        baseline = baseline.to_dict()

    actual = dict(run_result.get("metrics") or {})
    thresholds = dict((baseline or {}).get("metrics") or {})
    failures = []
    checked = {}

    for key, threshold in sorted(thresholds.items()):
        if key.endswith("_min"):
            metric_name = key[:-4]
            value = actual.get(metric_name)
            if value is None:
                continue
            checked[key] = {"metric": metric_name, "actual": value, "threshold": threshold, "operator": ">="}
            if value < threshold:
                failures.append("%s actual=%s below min=%s" % (metric_name, value, threshold))
        elif key.endswith("_max"):
            metric_name = key[:-4]
            value = actual.get(metric_name)
            if value is None:
                continue
            checked[key] = {"metric": metric_name, "actual": value, "threshold": threshold, "operator": "<="}
            if value > threshold:
                failures.append("%s actual=%s above max=%s" % (metric_name, value, threshold))

    return EvalGateResult(passed=(len(failures) == 0), failures=failures, checked=checked)


__all__ = [
    "BASELINE_DEFAULTS",
    "EvalBaseline",
    "EvalCase",
    "EvalGateResult",
    "EvalRunResult",
    "DEFAULT_EVAL_BASELINE",
    "get_eval_baseline",
    "build_eval_baseline",
    "evaluate_case",
    "evaluate_cases",
    "evaluate_gate",
]
