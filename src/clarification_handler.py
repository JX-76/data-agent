# -*- coding: utf-8 -*-
"""Clarification Handler: 处理用户澄清和修正.

解决问题：
1. 当模型理解不准确时，如何高效修正
2. 如何避免完全重新规划（节省成本）
3. 如何从修正中学习

核心策略：
- 主动澄清：规划前检测歧义
- 增量修正：只改受影响部分
- 记忆学习：存储修正经验

Python 2.7 compatible.
"""
from __future__ import unicode_literals

import json
import time
import re

try:
    basestring
except NameError:
    basestring = str


class AmbiguityDetector(object):
    """歧义检测器"""
    
    # 歧义关键词
    AMBIGUOUS_TERMS = {
        "time": ["本月", "上月", "最近", "今天", "昨天", "这个月", "去年"],
        "comparison": ["增长", "下降", "变化", "对比", "差异"],
        "scope": ["全部", "所有", "整体", "部分", "主要"],
        "metric": ["多", "少", "高", "低", "好", "坏"]
    }
    
    def detect(self, query, understanding):
        """
        检测查询中的歧义
        
        Returns:
            list: [
                {
                    "type": "time",
                    "term": "本月",
                    "options": ["自然月", "最近30天"],
                    "confidence": 0.8
                }
            ]
        """
        ambiguities = []
        
        # 检测时间歧义
        for term in self.AMBIGUOUS_TERMS["time"]:
            if term in query:
                if term in ["本月", "这个月"]:
                    ambiguities.append({
                        "type": "time",
                        "term": term,
                        "question": "'{}'是指自然月还是最近30天？".format(term),
                        "options": ["自然月（当月1日-当前）", "最近30天"],
                        "default": "自然月",
                        "confidence": 0.7
                    })
        
        # 检测对比类型歧义
        for term in self.AMBIGUOUS_TERMS["comparison"]:
            if term in query:
                ambiguities.append({
                    "type": "comparison",
                    "term": term,
                    "question": "您想看环比（与上期对比）还是同比（与去年同期对比）？",
                    "options": ["环比", "同比"],
                    "default": "环比",
                    "confidence": 0.6
                })
                break  # 只问一次
        
        # 检测指标歧义
        for term in self.AMBIGUOUS_TERMS["metric"]:
            if term in query and "怎么样" in query:
                ambiguities.append({
                    "type": "metric",
                    "term": term,
                    "question": "'怎么样'是指：",
                    "options": ["绝对值", "环比变化", "同比变化", "与目标对比"],
                    "default": "环比变化",
                    "confidence": 0.8
                })
                break
        
        # 去重
        seen = set()
        unique_ambiguities = []
        for amb in ambiguities:
            key = amb["type"]
            if key not in seen:
                seen.add(key)
                unique_ambiguities.append(amb)
        
        return unique_ambiguities


class CorrectionExtractor(object):
    """修正意图提取器"""
    
    def extract(self, user_feedback, original_understanding):
        """
        从用户反馈中提取修正意图
        
        Args:
            user_feedback: "不对，我的意思是同比，不是环比"
            original_understanding: 原始理解
        
        Returns:
            dict: {
                "correction_type": "comparison_type",
                "from_value": "mom",
                "to_value": "yoy",
                "confidence": 0.9
            }
        """
        corrections = []
        
        # 规则 1：对比类型修正
        if "同比" in user_feedback and "环比" in original_understanding.get("goal", ""):
            corrections.append({
                "correction_type": "comparison_type",
                "aspect": "time_comparison",
                "from_value": "mom",
                "to_value": "yoy",
                "user_words": user_feedback,
                "confidence": 0.9
            })
        elif "环比" in user_feedback and "同比" in original_understanding.get("goal", ""):
            corrections.append({
                "correction_type": "comparison_type",
                "aspect": "time_comparison",
                "from_value": "yoy",
                "to_value": "mom",
                "user_words": user_feedback,
                "confidence": 0.9
            })
        
        # 规则 2：时间范围修正
        if "自然月" in user_feedback or "月初" in user_feedback:
            corrections.append({
                "correction_type": "time_range",
                "aspect": "time_definition",
                "to_value": "calendar_month",
                "user_words": user_feedback,
                "confidence": 0.85
            })
        elif "30天" in user_feedback or "最近30" in user_feedback:
            corrections.append({
                "correction_type": "time_range",
                "aspect": "time_definition",
                "to_value": "last_30_days",
                "user_words": user_feedback,
                "confidence": 0.85
            })
        
        # 规则 3：维度修正
        if "渠道" in user_feedback and "商品" in original_understanding.get("goal", ""):
            corrections.append({
                "correction_type": "dimension",
                "aspect": "breakdown_dimension",
                "from_value": "product",
                "to_value": "channel",
                "user_words": user_feedback,
                "confidence": 0.8
            })
        
        # 规则 4：意图修正
        intent_keywords = {
            "为什么": "anomaly",
            "原因": "attribution",
            "对比": "comparison",
            "看看": "breakdown"
        }
        for keyword, intent in intent_keywords.items():
            if keyword in user_feedback:
                if original_understanding.get("intent_primary") != intent:
                    corrections.append({
                        "correction_type": "intent",
                        "aspect": "intent_primary",
                        "from_value": original_understanding.get("intent_primary"),
                        "to_value": intent,
                        "user_words": user_feedback,
                        "confidence": 0.75
                    })
                    break
        
        return corrections if corrections else [
            {
                "correction_type": "unclear",
                "user_words": user_feedback,
                "confidence": 0.3
            }
        ]


class PlanRefiner(object):
    """Plan 修正器"""
    
    def __init__(self, decomposer=None):
        self.decomposer = decomposer
    
    def refine_plan(self, old_plan, corrections):
        """
        根据修正意图更新 plan
        
        Returns:
            dict: {
                "new_plan": {...},
                "changed_tasks": [...],
                "reuse_tasks": [...]
            }
        """
        new_understanding = self._apply_corrections_to_understanding(
            old_plan["understanding"],
            corrections
        )
        
        # 找出受影响的任务
        affected_tasks = self._find_affected_tasks(old_plan, corrections)
        
        # 判断是否需要全部重新规划
        if len(affected_tasks) >= len(old_plan["tasks"]) * 0.7:
            # 影响超过 70%，全部重新规划
            return self._full_replan(new_understanding)
        
        # 部分修正
        return self._partial_replan(old_plan, new_understanding, affected_tasks, corrections)
    
    def _apply_corrections_to_understanding(self, understanding, corrections):
        """应用修正到 understanding"""
        new_understanding = dict(understanding)
        
        for corr in corrections:
            corr_type = corr["correction_type"]
            
            if corr_type == "comparison_type":
                # 修正对比类型
                new_understanding["goal"] = new_understanding["goal"].replace(
                    "环比", "同比"
                ) if corr["to_value"] == "yoy" else new_understanding["goal"].replace(
                    "同比", "环比"
                )
            
            elif corr_type == "time_range":
                # 修正时间范围
                if "assumptions" not in new_understanding:
                    new_understanding["assumptions"] = []
                new_understanding["assumptions"].append(
                    "时间范围：{}".format(corr["to_value"])
                )
            
            elif corr_type == "dimension":
                # 修正维度
                new_understanding["goal"] = new_understanding["goal"].replace(
                    corr.get("from_value", ""),
                    corr["to_value"]
                )
            
            elif corr_type == "intent":
                # 修正意图
                new_understanding["intent_primary"] = corr["to_value"]
        
        return new_understanding
    
    def _find_affected_tasks(self, old_plan, corrections):
        """找出受影响的任务"""
        affected_task_ids = []
        
        for corr in corrections:
            corr_type = corr["correction_type"]
            
            if corr_type == "comparison_type":
                # 对比类型变化，影响所有查询任务
                affected_task_ids.extend([
                    t["task_id"] for t in old_plan["tasks"]
                    if t["type"] == "query" and "compare" in t.get("parameters_template", {})
                ])
            
            elif corr_type == "dimension":
                # 维度变化，影响下钻任务
                affected_task_ids.extend([
                    t["task_id"] for t in old_plan["tasks"]
                    if "dimension" in t.get("title", "").lower()
                ])
            
            elif corr_type == "intent":
                # 意图变化，影响所有任务
                affected_task_ids = [t["task_id"] for t in old_plan["tasks"]]
        
        return list(set(affected_task_ids))
    
    def _partial_replan(self, old_plan, new_understanding, affected_task_ids, corrections):
        """部分重新规划"""
        # 保留未受影响的任务
        reuse_tasks = [
            t for t in old_plan["tasks"]
            if t["task_id"] not in affected_task_ids
        ]
        
        # 重新生成受影响的任务（简化版）
        new_tasks = []
        for task in old_plan["tasks"]:
            if task["task_id"] in affected_task_ids:
                # 应用修正
                new_task = dict(task)
                for corr in corrections:
                    if corr["correction_type"] == "comparison_type":
                        if "parameters_template" in new_task:
                            params = new_task["parameters_template"]
                            if "compare_to" in params:
                                params["compare_to"] = "last_year" if corr["to_value"] == "yoy" else "last_month"
                new_tasks.append(new_task)
        
        # 合并
        all_tasks = reuse_tasks + new_tasks
        
        return {
            "new_plan": {
                "understanding": new_understanding,
                "tasks": all_tasks,
                "version": old_plan.get("version", 1) + 1
            },
            "changed_tasks": new_tasks,
            "reuse_tasks": reuse_tasks
        }
    
    def _full_replan(self, new_understanding):
        """完全重新规划"""
        if self.decomposer:
            result = self.decomposer.decompose(
                user_query=new_understanding["original_query"],
                intent=new_understanding["intent_primary"],
                context={}
            )
            return {
                "new_plan": result,
                "changed_tasks": result["tasks"],
                "reuse_tasks": []
            }
        return None


class CorrectionMemory(object):
    """修正记忆系统"""
    
    def __init__(self, storage=None):
        self.storage = storage or {}  # {user_id: [corrections]}
    
    def store(self, user_id, session_id, correction, original_understanding):
        """存储修正经验"""
        if user_id not in self.storage:
            self.storage[user_id] = []
        
        self.storage[user_id].append({
            "session_id": session_id,
            "timestamp": time.time(),
            "original_understanding": original_understanding,
            "correction": correction,
            "pattern": self._extract_pattern(original_understanding, correction)
        })
    
    def retrieve_similar(self, user_id, current_understanding):
        """检索相似的历史修正"""
        if user_id not in self.storage:
            return []
        
        similar = []
        for record in self.storage[user_id]:
            similarity = self._compute_similarity(
                current_understanding,
                record["original_understanding"]
            )
            if similarity > 0.7:
                similar.append(record)
        
        return similar
    
    def _extract_pattern(self, understanding, correction):
        """提取修正模式"""
        return {
            "intent": understanding.get("intent_primary"),
            "correction_type": correction.get("correction_type"),
            "aspect": correction.get("aspect")
        }
    
    def _compute_similarity(self, u1, u2):
        """计算两个 understanding 的相似度"""
        # 简单实现：比较 intent 和关键词
        score = 0.0
        
        if u1.get("intent_primary") == u2.get("intent_primary"):
            score += 0.5
        
        q1 = set(u1.get("original_query", "").split())
        q2 = set(u2.get("original_query", "").split())
        overlap = len(q1 & q2) / float(max(len(q1), len(q2))) if q1 or q2 else 0
        score += overlap * 0.5
        
        return score


class ClarificationHandler(object):
    """澄清对话处理器（主控制器）"""
    
    def __init__(self, decomposer=None, memory=None):
        self.ambiguity_detector = AmbiguityDetector()
        self.correction_extractor = CorrectionExtractor()
        self.plan_refiner = PlanRefiner(decomposer)
        self.memory = memory or CorrectionMemory()
    
    def handle_initial_query(self, user_id, query):
        """
        处理初始查询
        
        Returns:
            dict: {
                "status": "need_clarification" | "ready",
                "ambiguities": [...],  # if need_clarification
                "understanding": {...}  # 暂存
            }
        """
        # 初步理解（简化版，实际应调用 planner）
        understanding = {
            "original_query": query,
            "intent_primary": self._guess_intent(query),
            "goal": "分析：{}".format(query)
        }
        
        # 检测歧义
        ambiguities = self.ambiguity_detector.detect(query, understanding)
        
        if ambiguities:
            return {
                "status": "need_clarification",
                "ambiguities": ambiguities,
                "understanding": understanding
            }
        
        return {
            "status": "ready",
            "understanding": understanding
        }
    
    def handle_user_correction(self, user_id, session_id, user_feedback, old_plan):
        """
        处理用户修正
        
        Returns:
            dict: {
                "status": "replanned",
                "new_plan": {...},
                "changes_summary": "..."
            }
        """
        # 提取修正意图
        corrections = self.correction_extractor.extract(
            user_feedback,
            old_plan["understanding"]
        )
        
        # 修正 plan
        result = self.plan_refiner.refine_plan(old_plan, corrections)
        
        # 存储修正经验
        for corr in corrections:
            self.memory.store(user_id, session_id, corr, old_plan["understanding"])
        
        # 生成变化摘要
        changes_summary = self._generate_changes_summary(result)
        
        return {
            "status": "replanned",
            "new_plan": result["new_plan"],
            "changes_summary": changes_summary,
            "reused_tasks_count": len(result["reuse_tasks"])
        }
    
    def _guess_intent(self, query):
        """
        意图猜测（增强版）
        
        基于论文：CoCo-IR (arXiv:2608.05149, 2026-08-05)
        核心思想：Transformable embeddings that evolve across turns
        论文数据：单轮方法4轮准确率28.2% → CoCo-IR达到44.1%（+56%）
        """
        # 检测追问词（CoCo-IR方法：识别多轮对话）
        FOLLOWUP_KEYWORDS = ["继续", "展开", "那", "呢", "也", "还有", "另外", "再看"]
        
        is_followup = any(kw in query for kw in FOLLOWUP_KEYWORDS)
        
        if is_followup:
            # 这是追问，意图应该继承上一轮
            # 返回特殊标记，让调用者知道需要继承上下文
            return "followup"
        
        # 常规意图识别
        if "为什么" in query or "原因" in query:
            return "anomaly"
        elif "对比" in query or "哪个" in query:
            return "comparison"
        elif "怎么样" in query or "如何" in query:
            return "breakdown"
        return "unknown"
    
    def _generate_changes_summary(self, replan_result):
        """生成变化摘要"""
        changed_count = len(replan_result["changed_tasks"])
        reused_count = len(replan_result["reuse_tasks"])
        
        if reused_count == 0:
            return "已完全重新规划（{}个新任务）".format(changed_count)
        else:
            return "已修正{}个任务，复用{}个未受影响任务".format(changed_count, reused_count)


# 全局单例
_default_handler = None


def get_default_handler():
    """获取默认处理器"""
    global _default_handler
    if _default_handler is None:
        _default_handler = ClarificationHandler()
    return _default_handler
