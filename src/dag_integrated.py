"""DAG integrated extension implementation.

This module owns the high-level experimental integration API that used to live
inside `dag_agent.py`.
"""

from config import ANALYSIS_MODEL, DEEPSEEK_BASE, DEEPSEEK_KEY, ROUTER_MODEL, SEMANTIC_SUMMARY

# Optional advanced imports
try:
    from sql_retry import SQLRetryHandler
    from evidence_recall import EvidenceRecall
    from table_relation import TableRelationAnalyzer
    from python_retry import PythonRetryHandler
    ERROR_RECOVERY_AVAILABLE = True
except ImportError:
    ERROR_RECOVERY_AVAILABLE = False

try:
    from streaming_sse import StreamingSSE
    from human_feedback import HumanFeedbackNode
    from reward_steps import RewardCalculator
    from log_metric_query import LogMetricQuery
    from file_locator import FileLocator
    from code_fix import CodeFixer
    from test_gen import TestGenerator
    from docker_execute import DockerExecutor
    ADVANCED_FEATURES_AVAILABLE = True
except ImportError:
    ADVANCED_FEATURES_AVAILABLE = False

from dag_routing import route_and_plan


class IntegratedDataAgent(object):
    """Integrated Data Agent with all P0-P3 features."""

    def __init__(self):
        self._facade = None
        self.streaming_sse = StreamingSSE() if ADVANCED_FEATURES_AVAILABLE else None
        self.human_feedback = HumanFeedbackNode() if ADVANCED_FEATURES_AVAILABLE else None
        self.reward_calculator = RewardCalculator() if ADVANCED_FEATURES_AVAILABLE else None
        self.log_metric_query = LogMetricQuery() if ADVANCED_FEATURES_AVAILABLE else None
        self.file_locator = FileLocator() if ADVANCED_FEATURES_AVAILABLE else None
        self.code_fixer = CodeFixer() if ADVANCED_FEATURES_AVAILABLE else None
        self.test_generator = TestGenerator() if ADVANCED_FEATURES_AVAILABLE else None
        self.docker_executor = DockerExecutor() if ADVANCED_FEATURES_AVAILABLE else None
        self.sql_retry_handler = SQLRetryHandler() if ERROR_RECOVERY_AVAILABLE else None
        self.python_retry_handler = PythonRetryHandler() if ERROR_RECOVERY_AVAILABLE else None
        self.evidence_recall = EvidenceRecall() if ERROR_RECOVERY_AVAILABLE else None
        self.table_relation_analyzer = TableRelationAnalyzer() if ERROR_RECOVERY_AVAILABLE else None

    def execute_with_streaming(self, query, state):
        if self.streaming_sse:
            self.streaming_sse.emit_plan({"query": query, "status": "started"})
        result = route_and_plan(query)
        if self.streaming_sse:
            self.streaming_sse.emit_result(result, step=1)
            self.streaming_sse.emit_complete(result)
        return result

    def get_facade(self, session_id=None):
        if self._facade is None or session_id is not None:
            from agent_facade import AgentFacade
            self._facade = AgentFacade(session_id=session_id)
        return self._facade

    def execute_via_facade(self, query, session_id=None, use_llm=False):
        facade = self.get_facade(session_id=session_id)
        return facade.ask(query, use_llm=use_llm)

    def execute_with_human_feedback(self, query, state):
        if self.human_feedback:
            request_id = self.human_feedback.request_feedback(step=1, node="route", content=query, context=state)
            feedback = self.human_feedback.submit_feedback(request_id, "approve")
            if feedback.action == "approve":
                return route_and_plan(query)
            if feedback.action == "reject":
                return {"status": "rejected", "reason": "human_rejected"}
            if feedback.action == "modify":
                return route_and_plan(feedback.modified_content or query)
        return route_and_plan(query)

    def execute_with_reward(self, query, state):
        result = route_and_plan(query)
        if self.reward_calculator:
            plan = state.get("plan", {})
            reward = self.reward_calculator.calculate_step_reward(step=1, plan=plan, result=result)
            result["reward"] = {
                "total": reward.total,
                "instruction_correctness": reward.instruction_correctness,
                "tool_selection": reward.tool_selection,
                "parameter_sufficiency": reward.parameter_sufficiency,
                "execution_success": reward.execution_success,
                "output_quality": reward.output_quality,
            }
        return result

    def execute_with_sql_retry(self, sql, db_connection=None):
        if self.sql_retry_handler:
            self.sql_retry_handler.db = db_connection
            result = self.sql_retry_handler.execute_with_retry(sql)
            return {"success": result.success, "sql": result.sql, "error": result.error, "attempts": result.attempts, "corrections": result.corrections}
        try:
            cursor = db_connection.cursor()
            cursor.execute(sql)
            return {"success": True, "sql": sql, "results": cursor.fetchall()}
        except Exception as e:
            return {"success": False, "sql": sql, "error": str(e)}

    def execute_with_python_retry(self, code):
        if self.python_retry_handler:
            result = self.python_retry_handler.execute_with_retry(code)
            return {"success": result.success, "code": result.code, "output": result.output, "error": result.error, "attempts": result.attempts, "corrections": result.corrections}
        try:
            exec_globals = {}
            exec(code, exec_globals)
            return {"success": True, "code": code, "output": exec_globals.get("_result")}
        except Exception as e:
            return {"success": False, "code": code, "error": str(e)}

    def execute_with_evidence_recall(self, query):
        if self.evidence_recall:
            evidence_result = self.evidence_recall.recall(query)
            return {"evidence": [{"id": e.id, "type": e.type, "content": e.content, "relevance": e.relevance_score} for e in evidence_result.evidence], "total_score": evidence_result.total_score}
        return {"evidence": [], "total_score": 0.0}

    def execute_with_table_relation(self, tables):
        if self.table_relation_analyzer:
            _graph = self.table_relation_analyzer.analyze()
            joins = self.table_relation_analyzer.suggest_joins(tables)
            return {"tables": tables, "joins": [{"from_table": j.from_table, "from_column": j.from_column, "to_table": j.to_table, "to_column": j.to_column, "type": j.relationship_type} for j in joins]}
        return {"tables": tables, "joins": []}

    def execute_with_file_locator(self, pattern):
        if self.file_locator:
            files = self.file_locator.find_files(pattern)
            return {"files": [{"path": f.path, "name": f.name, "size": f.size} for f in files]}
        return {"files": []}

    def execute_with_code_fix(self, code, language="python"):
        if self.code_fixer:
            if language == "sql":
                fixed, fixes = self.code_fixer.fix_sql(code)
            elif language == "python":
                fixed, fixes = self.code_fixer.fix_python(code)
            else:
                fixed, fixes = code, []
            return {"original": code, "fixed": fixed, "fixes": [{"issue": f.issue, "line": f.line, "confidence": f.confidence} for f in fixes]}
        return {"original": code, "fixed": code, "fixes": []}

    def execute_with_test_gen(self, code, language="python"):
        if self.test_generator:
            suite = self.test_generator.generate_tests(code, language)
            return {"suite_name": suite.name, "cases": [{"name": c.name, "description": c.description} for c in suite.cases]}
        return {"suite_name": "", "cases": []}

    def execute_with_docker(self, code, language="python"):
        if self.docker_executor:
            result = self.docker_executor.execute(code, language)
            return {"success": result.success, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code, "duration": result.duration}
        return {"success": False, "stderr": "Docker not available"}


_integrated_agent = None


def get_integrated_agent():
    global _integrated_agent
    if _integrated_agent is None:
        _integrated_agent = IntegratedDataAgent()
    return _integrated_agent



def ask(query, session_id=None, use_llm=False):
    return get_integrated_agent().execute_via_facade(query, session_id=session_id, use_llm=use_llm)


__all__ = ["IntegratedDataAgent", "get_integrated_agent", "ask"]
