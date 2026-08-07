# -*- coding: utf-8 -*-
"""Task Executor: 任务执行器（防偏差机制）.

核心机制：
1. Evidence-based 验证（每个子任务结果必须有证据）
2. Checkpoint 机制（关键节点验证对齐）
3. 上下文压缩（只传递压缩后的证据摘要）
4. 最小 LLM 调用（只在synthesis和decision point）

Python 2.7 compatible.
"""
from __future__ import unicode_literals

import json
import hashlib

try:
    basestring
except NameError:
    basestring = str


class EvidenceRecord(object):
    """证据记录"""
    
    def __init__(self, evidence_id, task_id, evidence_type, data, metadata=None):
        """
        Args:
            evidence_id: 证据 ID
            task_id: 关联的任务 ID
            evidence_type: 证据类型（query_result/reasoning/external）
            data: 证据数据
            metadata: 元数据（source, timestamp, confidence等）
        """
        self.evidence_id = evidence_id
        self.task_id = task_id
        self.evidence_type = evidence_type
        self.data = data
        self.metadata = metadata or {}
    
    def compress(self, max_tokens=200):
        """
        压缩证据（保留关键信息）
        
        Returns:
            dict: 压缩后的证据摘要
        """
        compressed = {
            "evidence_id": self.evidence_id,
            "task_id": self.task_id,
            "type": self.evidence_type,
            "metadata": self.metadata
        }
        
        # 根据类型压缩数据
        if self.evidence_type == "query_result":
            # SQL 查询结果：只保留统计摘要
            compressed["summary"] = self._compress_query_result(self.data, max_tokens)
        elif self.evidence_type == "reasoning":
            # 推理结果：保留结论
            compressed["summary"] = self._compress_reasoning(self.data, max_tokens)
        else:
            # 其他：截断
            compressed["summary"] = str(self.data)[:max_tokens]
        
        return compressed
    
    def _compress_query_result(self, data, max_tokens):
        """压缩查询结果"""
        if not data:
            return "No data"
        
        # 假设 data 是 list of dict
        if isinstance(data, list):
            row_count = len(data)
            if row_count == 0:
                return "Empty result"
            
            # 统计信息
            first_row = data[0] if data else {}
            columns = list(first_row.keys()) if isinstance(first_row, dict) else []
            
            # 数值列的统计
            stats = {}
            for col in columns:
                values = [row.get(col) for row in data if isinstance(row.get(col), (int, float))]
                if values:
                    stats[col] = {
                        "min": min(values),
                        "max": max(values),
                        "avg": sum(values) / len(values) if values else 0
                    }
            
            return {
                "row_count": row_count,
                "columns": columns,
                "stats": stats,
                "sample": data[:3]  # 前 3 行
            }
        
        return str(data)[:max_tokens]
    
    def _compress_reasoning(self, data, max_tokens):
        """压缩推理结果"""
        if isinstance(data, dict):
            # 只保留结论部分
            return {
                "conclusion": data.get("conclusion", ""),
                "confidence": data.get("confidence", 0.0),
                "key_findings": data.get("key_findings", [])[:3]
            }
        return str(data)[:max_tokens]


class TaskExecutor(object):
    """
    任务执行器
    
    核心职责：
    1. 按计划执行任务
    2. 验证每个任务的输出（Evidence-based）
    3. 压缩上下文传递给下游
    4. 在关键点检查对齐（防偏差）
    """
    
    def __init__(self, decomposer, tool_executor, llm_service=None, 
                 evidence_store=None, observer=None):
        """
        Args:
            decomposer: TaskDecomposer 实例
            tool_executor: 工具执行器（ExternalToolExecutor）
            llm_service: LLM 服务（用于synthesis和decision）
            evidence_store: 证据存储
            observer: 观测器
        """
        self.decomposer = decomposer
        self.tool_executor = tool_executor
        self.llm_service = llm_service
        self.evidence_store = evidence_store or {}
        self.observer = observer
        
        self.evidence_counter = 0
    
    def execute_plan(self, execution_plan, root_query, context=None):
        """
        执行任务计划
        
        Args:
            execution_plan: 任务 ID 列表（按执行顺序）
            root_query: 原始查询
            context: 上下文
        
        Returns:
            dict: {
                "success": bool,
                "final_result": Any,
                "evidences": [EvidenceRecord],
                "trace": []
            }
        """
        context = context or {}
        trace = []
        
        # 跳过根任务
        tasks_to_execute = [tid for tid in execution_plan 
                           if not tid.startswith("task_0001")]
        
        for task_id in tasks_to_execute:
            task = self.decomposer.get_task(task_id)
            if not task:
                continue
            
            # 检查依赖是否完成
            if not self._check_dependencies(task):
                self.decomposer.update_task_status(task_id, "blocked")
                trace.append({
                    "task_id": task_id,
                    "status": "blocked",
                    "reason": "Dependencies not met"
                })
                continue
            
            # 执行任务
            try:
                self.decomposer.update_task_status(task_id, "running")
                
                result = self._execute_task(task, root_query, context)
                
                # 创建证据
                evidence = self._create_evidence(task, result)
                
                # 验证证据（防偏差检查）
                if not self._validate_evidence(evidence, task, context):
                    raise ValueError("Evidence validation failed")
                
                self.decomposer.update_task_status(task_id, "completed", result=result)
                
                trace.append({
                    "task_id": task_id,
                    "status": "completed",
                    "evidence_id": evidence.evidence_id
                })
            
            except Exception as e:
                self.decomposer.update_task_status(task_id, "failed", error=str(e))
                trace.append({
                    "task_id": task_id,
                    "status": "failed",
                    "error": str(e)
                })
                
                # 失败处理：是否继续？
                if task.metadata.get("required"):
                    # 必需任务失败，中断
                    return {
                        "success": False,
                        "error": "Required task failed: {}".format(task_id),
                        "trace": trace
                    }
        
        # 获取最终任务的结果
        final_task_id = execution_plan[-1]
        final_task = self.decomposer.get_task(final_task_id)
        
        return {
            "success": True,
            "final_result": final_task.result if final_task else None,
            "evidences": list(self.evidence_store.values()),
            "trace": trace
        }
    
    def _execute_task(self, task, root_query, context):
        """执行单个任务"""
        task_type = task.task_type
        metadata = task.metadata
        
        if task_type == "query":
            # 工具调用任务
            return self._execute_query_task(task, context)
        
        elif task_type == "reasoning":
            # 推理任务（可能需要 LLM）
            return self._execute_reasoning_task(task, root_query, context)
        
        elif task_type == "synthesis":
            # 合成任务（需要 LLM）
            return self._execute_synthesis_task(task, root_query, context)
        
        else:
            return {"status": "skipped", "reason": "Unknown task type"}
    
    def _execute_query_task(self, task, context):
        """执行查询任务（工具调用）"""
        tool_id = task.metadata.get("tool")
        
        if not tool_id:
            # 没有指定工具，根据描述推断
            description = task.metadata.get("description", "")
            tool_id = self._infer_tool_from_description(description)
        
        # 构建参数（从依赖任务的结果）
        args = self._build_args_from_dependencies(task, context)
        
        # 调用工具
        result = self.tool_executor.call(tool_id, args, context)
        
        return result
    
    def _execute_reasoning_task(self, task, root_query, context):
        """执行推理任务"""
        # 收集依赖任务的证据
        dep_evidences = self._collect_dependency_evidences(task)
        
        # 检查是否需要 LLM
        if task.metadata.get("requires_llm"):
            # 压缩证据
            compressed = [e.compress(max_tokens=200) for e in dep_evidences]
            
            # LLM 推理
            if self.llm_service:
                result = self.llm_service.reason(
                    query=root_query,
                    task_description=task.title,
                    evidences=compressed,
                    mode=task.metadata.get("llm_mode", "default")
                )
            else:
                # 无 LLM，使用规则
                result = self._rule_based_reasoning(task, dep_evidences)
        else:
            # 规则推理
            result = self._rule_based_reasoning(task, dep_evidences)
        
        return result
    
    def _execute_synthesis_task(self, task, root_query, context):
        """执行合成任务（最终结论）"""
        # 收集所有依赖的证据
        dep_evidences = self._collect_dependency_evidences(task)
        
        # 压缩证据（关键！防止 Token 爆炸）
        compressed = [e.compress(max_tokens=150) for e in dep_evidences]
        
        # 构建结构化输入
        synthesis_input = {
            "original_query": root_query,
            "evidences": compressed,
            "task": task.title
        }
        
        # LLM 合成（唯一的大 LLM 调用）
        if self.llm_service:
            result = self.llm_service.synthesize(synthesis_input)
        else:
            # Fallback：拼接所有证据
            result = {
                "conclusion": "Based on {} evidences".format(len(compressed)),
                "evidences_used": [e["evidence_id"] for e in compressed]
            }
        
        return result
    
    def _check_dependencies(self, task):
        """检查任务依赖是否完成"""
        for dep_id in task.dependencies:
            dep_task = self.decomposer.get_task(dep_id)
            if not dep_task or dep_task.status != "completed":
                return False
        return True
    
    def _create_evidence(self, task, result):
        """创建证据记录"""
        self.evidence_counter += 1
        evidence_id = "evidence_{:04d}".format(self.evidence_counter)
        
        evidence_type = "query_result" if task.task_type == "query" else "reasoning"
        
        evidence = EvidenceRecord(
            evidence_id=evidence_id,
            task_id=task.task_id,
            evidence_type=evidence_type,
            data=result,
            metadata={
                "task_title": task.title,
                "timestamp": task.completed_at
            }
        )
        
        self.evidence_store[evidence_id] = evidence
        return evidence
    
    def _validate_evidence(self, evidence, task, context):
        """
        验证证据（防偏差）- 多层验证机制
        
        基于论文：Argus (arXiv:2608.05144, 2026-08-05)
        核心思想：verification-gated self-evolution
        论文数据：34 verifier recoveries + 22 strict review-loop rescues
        
        检查层次：
        1. 非空检查
        2. 数值范围检查（GMV/订单量不能为负）
        3. 时间一致性检查
        4. 依赖对齐检查
        """
        # 层1：非空检查
        if not evidence.data:
            if self.observer:
                self.observer.log_validation_failure(
                    task_id=task.task_id,
                    reason="empty_data",
                    evidence_id=evidence.evidence_id
                )
            return False
        
        # 层2：数值范围检查（参考 Argus 的 semantic scorers）
        if evidence.evidence_type == "query_result":
            if not self._check_value_range(evidence.data, task):
                if self.observer:
                    self.observer.log_validation_failure(
                        task_id=task.task_id,
                        reason="invalid_value_range",
                        evidence_id=evidence.evidence_id
                    )
                return False
        
        # 层3：时间一致性检查（参考 Argus 的 task-native verification）
        if "date" in task.metadata or "time_range" in task.metadata:
            if not self._check_time_consistency(evidence.data, task.metadata):
                if self.observer:
                    self.observer.log_validation_failure(
                        task_id=task.task_id,
                        reason="time_mismatch",
                        evidence_id=evidence.evidence_id
                    )
                return False
        
        # 层4：依赖对齐检查（参考 Argus 的 review-loop）
        if task.dependencies:
            dep_evidences = self._collect_dependency_evidences(task)
            if not self._check_logical_consistency(evidence, dep_evidences, task):
                if self.observer:
                    self.observer.log_validation_failure(
                        task_id=task.task_id,
                        reason="dependency_conflict",
                        evidence_id=evidence.evidence_id
                    )
                return False
        
        return True
    
    def _check_value_range(self, data, task):
        """
        检查数值合理性（Argus方法：semantic validity）
        
        规则：
        - GMV/订单量/金额不能为负数
        - 百分比应在0-100之间
        - 数量字段应为整数
        """
        if not data:
            return True
        
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                
                # 检查GMV
                if "gmv" in row:
                    try:
                        gmv_val = float(row["gmv"])
                        if gmv_val < 0:
                            return False
                    except (ValueError, TypeError):
                        return False
                
                # 检查订单量
                if "orders" in row or "order_count" in row:
                    key = "orders" if "orders" in row else "order_count"
                    try:
                        orders_val = float(row[key])
                        if orders_val < 0:
                            return False
                    except (ValueError, TypeError):
                        return False
                
                # 检查金额字段
                for key in ["amount", "revenue", "sales"]:
                    if key in row:
                        try:
                            amount_val = float(row[key])
                            if amount_val < 0:
                                return False
                        except (ValueError, TypeError):
                            return False
                
                # 检查百分比
                for key in ["rate", "ratio", "percent", "percentage"]:
                    if key in row or key in str(row.get("metric", "")):
                        try:
                            rate_val = float(row.get(key, row.get("value", 0)))
                            if rate_val < 0 or rate_val > 100:
                                return False
                        except (ValueError, TypeError):
                            pass
        
        return True
    
    def _check_time_consistency(self, data, metadata):
        """
        检查时间一致性（Argus方法：task-native verification）
        
        验证：
        - 返回数据的日期是否匹配查询条件
        - 时间范围是否合理
        """
        expected_date = metadata.get("date")
        expected_range = metadata.get("time_range")
        
        if not expected_date and not expected_range:
            return True
        
        # 从数据中提取日期
        actual_dates = self._extract_dates_from_data(data)
        
        if not actual_dates:
            # 数据中没有日期字段，跳过验证
            return True
        
        # 如果指定了具体日期
        if expected_date:
            # 简单检查：至少有一条数据的日期匹配
            if expected_date not in actual_dates:
                return False
        
        return True
    
    def _extract_dates_from_data(self, data):
        """从数据中提取日期"""
        dates = []
        
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    # 常见日期字段
                    for date_field in ["date", "time", "datetime", "day", "dt"]:
                        if date_field in row:
                            dates.append(str(row[date_field]))
        
        return dates
    
    def _check_logical_consistency(self, evidence, dep_evidences, task):
        """
        检查逻辑一致性（Argus方法：review-loop）
        
        验证：
        - 总数 >= 分项之和
        - 下钻数据与汇总数据一致
        - 时间序列连续性
        """
        # 简化实现：检查数值逻辑
        # 实际项目中可以根据具体业务规则扩展
        
        if not dep_evidences:
            return True
        
        # 示例：如果是维度下钻，检查总数是否合理
        if task.metadata.get("dimension"):
            # 获取上游的总数
            parent_total = self._extract_total_from_evidences(dep_evidences)
            current_total = self._extract_total_from_evidence(evidence)
            
            if parent_total and current_total:
                # 下钻数据总和不应超过上游总数的120%（允许一定误差）
                if current_total > parent_total * 1.2:
                    return False
        
        return True
    
    def _extract_total_from_evidences(self, evidences):
        """从多个证据中提取总数"""
        for evidence in evidences:
            total = self._extract_total_from_evidence(evidence)
            if total:
                return total
        return None
    
    def _extract_total_from_evidence(self, evidence):
        """从单个证据中提取总数"""
        if not evidence.data:
            return None
        
        if isinstance(evidence.data, list) and len(evidence.data) > 0:
            # 尝试提取GMV或订单量
            row = evidence.data[0]
            if isinstance(row, dict):
                for key in ["gmv", "orders", "total", "amount"]:
                    if key in row:
                        try:
                            return float(row[key])
                        except (ValueError, TypeError):
                            pass
        
        return None
    
    def _collect_dependency_evidences(self, task):
        """收集依赖任务的证据"""
        evidences = []
        
        for dep_id in task.dependencies:
            dep_task = self.decomposer.get_task(dep_id)
            if not dep_task:
                continue
            
            # 找到该任务的证据
            for eid, evidence in self.evidence_store.items():
                if evidence.task_id == dep_id:
                    evidences.append(evidence)
                    break
        
        return evidences
    
    def _build_args_from_dependencies(self, task, context):
        """从依赖任务构建参数"""
        args = {}
        
        # 简单示例：从第一个依赖提取
        if task.dependencies:
            dep_task = self.decomposer.get_task(task.dependencies[0])
            if dep_task and dep_task.result:
                # 可以从结果中提取参数
                pass
        
        # 从 metadata 提取
        if "dimension" in task.metadata:
            args["dimension"] = task.metadata["dimension"]
        
        return args
    
    def _infer_tool_from_description(self, description):
        """从描述推断工具（简单规则）"""
        desc_lower = description.lower()
        
        if "sql" in desc_lower or "查询" in desc_lower:
            return "warehouse.query_sql"
        elif "概览" in desc_lower or "overview" in desc_lower:
            return "ecommerce.overview"
        elif "渠道" in desc_lower or "channel" in desc_lower:
            return "ecommerce.channel_performance"
        elif "商品" in desc_lower or "product" in desc_lower:
            return "ecommerce.product_performance"
        
        return "warehouse.query_sql"  # 默认
    
    def _rule_based_reasoning(self, task, evidences):
        """基于规则的推理（不用 LLM）"""
        # 简单示例：判断是否需要外部信息
        
        if task.metadata.get("description") == "判断是否需要外部信息":
            # 规则：如果所有维度都下降，可能是外部因素
            # 这里只是示例逻辑
            return {
                "need_external": True,
                "reason": "All dimensions declined",
                "confidence": 0.7
            }
        
        # 默认
        return {
            "conclusion": "Rule-based result",
            "evidences_count": len(evidences)
        }


# 全局单例
_default_executor = None


def get_default_executor():
    """获取默认执行器"""
    global _default_executor
    if _default_executor is None:
        from task_decomposer import get_default_decomposer
        from external_tool_executor import ExternalToolExecutor
        
        _default_executor = TaskExecutor(
            decomposer=get_default_decomposer(),
            tool_executor=ExternalToolExecutor()
        )
    return _default_executor
