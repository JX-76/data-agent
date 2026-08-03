from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_core import AgentRuntime, Dataset, validate_sql


def test_runtime_core_exports():
    rt = AgentRuntime()
    assert rt.counter == 0
    assert isinstance(Dataset("d1", "m", "select 1", ["c"]), Dataset)


def test_runtime_core_validate_sql():
    ok, reason = validate_sql("WITH d1 AS (SELECT 1) SELECT * FROM d1 LIMIT 10")
    assert ok is True
    assert reason == "ok"

    ok, reason = validate_sql("DELETE FROM fct_orders")
    assert ok is False
    assert "forbidden" in reason.lower() or "dangerous" in reason.lower()
