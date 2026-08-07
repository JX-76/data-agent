# -*- coding: utf-8 -*-
"""快速测试脚本 - 验证3个优化点"""
import sys
import os
import io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# 设置UTF-8输出
sys.stdout = io.open(sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False)

from task_decomposer import TaskDecomposer, Task
from task_executor import TaskExecutor, EvidenceRecord
from clarification_handler import ClarificationHandler

def test_dimensions():
    """测试维度识别"""
    print("\n========== Test 1: Dimension Recognition ==========")
    decomposer = TaskDecomposer()
    
    tests = [
        ("price test", "price", ["price"]),
        ("customer test", "customer", ["customer"]),
        ("activity test", "activity", ["activity"]),
    ]
    
    passed = 0
    for name, query, expected_dims in tests:
        result = decomposer._extract_dimensions(query)
        success = all(dim in result for dim in expected_dims)
        status = "PASS" if success else "FAIL"
        print("  [{}] {}: {}".format(status, name, result))
        if success:
            passed += 1
    
    print("\n  Result: {}/{} passed".format(passed, len(tests)))
    return passed == len(tests)

def test_evidence_validation():
    """测试Evidence验证"""
    print("\n========== Test 2: Evidence Validation ==========")
    decomposer = TaskDecomposer()
    executor = TaskExecutor(decomposer=decomposer, tool_executor=None)
    task = Task("t1", "test", "query")
    
    tests = [
        ("Empty data", EvidenceRecord("e1", "t1", "query_result", data=None), False),
        ("Negative GMV", EvidenceRecord("e1", "t1", "query_result", data=[{"gmv": -100}]), False),
        ("Valid GMV", EvidenceRecord("e1", "t1", "query_result", data=[{"gmv": 10000}]), True),
    ]
    
    passed = 0
    for name, evidence, expected in tests:
        result = executor._validate_evidence(evidence, task, {})
        success = (result == expected)
        status = "PASS" if success else "FAIL"
        print("  [{}] {}: got {}, expected {}".format(status, name, result, expected))
        if success:
            passed += 1
    
    print("\n  Result: {}/{} passed".format(passed, len(tests)))
    return passed == len(tests)

def test_followup_detection():
    """测试追问识别"""
    print("\n========== Test 3: Followup Detection ==========")
    handler = ClarificationHandler()
    
    tests = [
        ("Followup 1", "continue analysis", "followup"),
        ("Followup 2", "what about orders", "followup"),
        ("Normal query", "why GMV dropped", "anomaly"),
    ]
    
    passed = 0
    for name, query, expected in tests:
        result = handler._guess_intent(query)
        success = (result == expected)
        status = "PASS" if success else "FAIL"
        print("  [{}] {}: got '{}', expected '{}'".format(status, name, result, expected))
        if success:
            passed += 1
    
    print("\n  Result: {}/{} passed".format(passed, len(tests)))
    return passed == len(tests)

if __name__ == "__main__":
    print("=" * 60)
    print("QUICK TEST - Optimization Verification")
    print("=" * 60)
    
    results = []
    results.append(test_dimensions())
    results.append(test_evidence_validation())
    results.append(test_followup_detection())
    
    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    print("  Tests passed: {}/3".format(sum(results)))
    print("  Tests failed: {}/3".format(3 - sum(results)))
    
    if all(results):
        print("\n  STATUS: ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("\n  STATUS: SOME TESTS FAILED")
        sys.exit(1)
