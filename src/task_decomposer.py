# -*- coding: utf-8 -*-
"""Task Decomposer: 长任务分解与执行控制.

解决问题：
1. 复杂任务如何拆解？
2. 如何防止偏差累积？
3. 如何控制 Token 使用？
4. 如何确保子任务对齐？

核心策略：
- 结构化拆解（非 LLM 多次调用）
- 中间结果验证
- 渐进式细化
- 上下文压缩

Python 2.7 compatible.
"""
from __future__ import unicode_literals

import json
import time

try:
    basestring
except NameError:
    basestring = str


class Task(object):
    """任务节点"""
    
    def __init__(self, task_id, title, task_type, status="pending", 
                 parent_id=None, dependencies=None, metadata=None):
        """
        Args:
            task_id: 任务 ID
            title: 任务标题
            task_type: 任务类型（analysis/query/reasoning/synthesis）
            status: 状态（pending/running/completed/failed/blocked）
            parent_id: 父任务 ID
            dependencies: 依赖的任务 ID 列表
            metadata: 任务元数据（input/output/evidence等）
        """
        self.task_id = task_id
        self.title = title
        self.task_type = task_type
        self.status = status
        self.parent_id = parent_id
        self.dependencies = dependencies or []
        self.metadata = metadata or {}
        
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.started_at = None
        self.completed_at = None
    
    def to_dict(self):
        return {
            "task_id": self.task_id,
            "title": self.title,
            "task_type": self.task_type,
            "status": self.status,
            "parent_id": self.parent_id,
            "dependencies": self.dependencies,
            "metadata": self.metadata,
            "result": self.result,
            "error": self.error
        }


class TaskDecomposer(object):
    """
    长任务分解器
    
    核心思路：
    1. 用规则引擎做初步拆解（不用 LLM）
    2. 只在关键决策点用 LLM（1-2 次）
    3. 用结构化模板防止偏差
    4. 用 Evidence 验证中间结果
    """
    
    def __init__(self, max_depth=3, max_breadth=5, observer=None):
        """
        Args:
            max_depth: 最大拆解深度
            max_breadth: 每层最多子任务数
            observer: 观测器
        """
        self.max_depth = max_depth
        self.max_breadth = max_breadth
        self.observer = observer
        
        # 任务树
        self.tasks = {}  # {task_id: Task}
        self.task_counter = 0
    
    def decompose(self, user_query, intent, context=None):
        """
        分解用户查询为任务树
        
        Args:
            user_query: 用户查询
            intent: 意图类型
            context: 上下文
        
        Returns:
            dict: {
                "root_task_id": str,
                "tasks": [Task],
                "execution_plan": [task_id]
            }
        """
        context = context or {}
        
        # 创建根任务
        root_task = self._create_task(
            title="Root: {}".format(user_query),
            task_type="root",
            metadata={"query": user_query, "intent": intent}
        )
        
        # 根据意图选择分解策略
        if intent == "anomaly":
            subtasks = self._decompose_anomaly_task(root_task, user_query, context)
        elif intent == "attribution":
            subtasks = self._decompose_attribution_task(root_task, user_query, context)
        elif intent == "breakdown":
            subtasks = self._decompose_breakdown_task(root_task, user_query, context)
        elif intent == "comparison":
            subtasks = self._decompose_comparison_task(root_task, user_query, context)
        else:
            # 通用拆解
            subtasks = self._decompose_generic_task(root_task, user_query, context)
        
        # 生成执行计划（拓扑排序）
        execution_plan = self._generate_execution_plan(root_task.task_id)
        
        return {
            "root_task_id": root_task.task_id,
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "execution_plan": execution_plan
        }
    
    def _decompose_anomaly_task(self, parent, query, context):
        """
        异常诊断任务拆解（规则引擎）
        
        结构：
        1. 确认异常
        2. 维度下钻
        3. 外部归因
        4. 结论合成
        """
        tasks = []
        
        # 任务 1：确认异常
        t1 = self._create_task(
            title="确认异常存在",
            task_type="query",
            parent_id=parent.task_id,
            metadata={
                "tool": "warehouse.query_sql",
                "description": "查询指标数据，确认异常"
            }
        )
        tasks.append(t1)
        
        # 任务 2：查看整体概览（依赖 t1）
        t2 = self._create_task(
            title="查看整体概览",
            task_type="query",
            parent_id=parent.task_id,
            dependencies=[t1.task_id],
            metadata={
                "tool": "ecommerce.overview",
                "description": "查看店铺级指标异常"
            }
        )
        tasks.append(t2)
        
        # 任务 3：渠道下钻（依赖 t2）
        t3 = self._create_task(
            title="渠道维度分析",
            task_type="query",
            parent_id=parent.task_id,
            dependencies=[t2.task_id],
            metadata={
                "tool": "ecommerce.channel_performance",
                "description": "找出异常渠道"
            }
        )
        tasks.append(t3)
        
        # 任务 4：商品下钻（依赖 t2）
        t4 = self._create_task(
            title="商品维度分析",
            task_type="query",
            parent_id=parent.task_id,
            dependencies=[t2.task_id],
            metadata={
                "tool": "ecommerce.product_performance",
                "description": "找出异常商品"
            }
        )
        tasks.append(t4)
        
        # 任务 5：外部归因（依赖 t3, t4）
        t5 = self._create_task(
            title="外部因素分析",
            task_type="reasoning",
            parent_id=parent.task_id,
            dependencies=[t3.task_id, t4.task_id],
            metadata={
                "description": "判断是否需要外部信息",
                "decision_point": True  # 决策点，可能需要 LLM
            }
        )
        tasks.append(t5)
        
        # 任务 6：结论合成（依赖所有）
        t6 = self._create_task(
            title="合成最终结论",
            task_type="synthesis",
            parent_id=parent.task_id,
            dependencies=[t1.task_id, t2.task_id, t3.task_id, t4.task_id, t5.task_id],
            metadata={
                "description": "基于所有证据合成结论",
                "requires_llm": True,  # 唯一需要 LLM 的地方
                "llm_mode": "synthesis"  # 合成模式，输入压缩过的证据
            }
        )
        tasks.append(t6)
        
        return tasks
    
    def _decompose_attribution_task(self, parent, query, context):
        """归因分析任务拆解"""
        tasks = []
        
        # 结构化拆解：内因 → 外因 → 相关性分析
        
        t1 = self._create_task(
            title="内部因素分析",
            task_type="query",
            parent_id=parent.task_id,
            metadata={"scope": "internal"}
        )
        tasks.append(t1)
        
        t2 = self._create_task(
            title="外部因素分析",
            task_type="query",
            parent_id=parent.task_id,
            dependencies=[t1.task_id],
            metadata={"scope": "external"}
        )
        tasks.append(t2)
        
        t3 = self._create_task(
            title="相关性分析",
            task_type="reasoning",
            parent_id=parent.task_id,
            dependencies=[t1.task_id, t2.task_id],
            metadata={
                "requires_llm": True,
                "llm_mode": "correlation"
            }
        )
        tasks.append(t3)
        
        return tasks
    
    def _decompose_breakdown_task(self, parent, query, context):
        """下钻分析任务拆解"""
        tasks = []
        
        # 结构：总体 → 各维度 → 交叉分析
        
        t1 = self._create_task(
            title="总体指标查询",
            task_type="query",
            parent_id=parent.task_id
        )
        tasks.append(t1)
        
        # 根据查询提取维度（简单规则）
        dimensions = self._extract_dimensions(query)
        
        for i, dim in enumerate(dimensions[:self.max_breadth]):
            t = self._create_task(
                title="{}维度分析".format(dim),
                task_type="query",
                parent_id=parent.task_id,
                dependencies=[t1.task_id],
                metadata={"dimension": dim}
            )
            tasks.append(t)
        
        # 合成
        deps = [t1.task_id] + [t.task_id for t in tasks[1:]]
        t_final = self._create_task(
            title="综合分析",
            task_type="synthesis",
            parent_id=parent.task_id,
            dependencies=deps,
            metadata={"requires_llm": True}
        )
        tasks.append(t_final)
        
        return tasks
    
    def _decompose_comparison_task(self, parent, query, context):
        """对比分析任务拆解"""
        tasks = []
        
        # 结构：A 数据 → B 数据 → 差异计算 → 原因分析
        
        entities = self._extract_comparison_entities(query)
        
        for i, entity in enumerate(entities[:2]):  # 最多比较 2 个
            t = self._create_task(
                title="查询{}数据".format(entity),
                task_type="query",
                parent_id=parent.task_id,
                metadata={"entity": entity}
            )
            tasks.append(t)
        
        if len(tasks) >= 2:
            t_diff = self._create_task(
                title="计算差异",
                task_type="reasoning",
                parent_id=parent.task_id,
                dependencies=[t.task_id for t in tasks],
                metadata={"operation": "diff"}
            )
            tasks.append(t_diff)
            
            t_why = self._create_task(
                title="分析原因",
                task_type="reasoning",
                parent_id=parent.task_id,
                dependencies=[t_diff.task_id],
                metadata={"requires_llm": True}
            )
            tasks.append(t_why)
        
        return tasks
    
    def _decompose_generic_task(self, parent, query, context):
        """通用任务拆解"""
        # 简单拆分为：数据收集 → 分析 → 结论
        tasks = []
        
        t1 = self._create_task(
            title="数据收集",
            task_type="query",
            parent_id=parent.task_id
        )
        tasks.append(t1)
        
        t2 = self._create_task(
            title="分析",
            task_type="reasoning",
            parent_id=parent.task_id,
            dependencies=[t1.task_id],
            metadata={"requires_llm": True}
        )
        tasks.append(t2)
        
        return tasks
    
    def _create_task(self, title, task_type, parent_id=None, dependencies=None, metadata=None):
        """创建任务节点"""
        self.task_counter += 1
        task_id = "task_{:04d}".format(self.task_counter)
        
        task = Task(
            task_id=task_id,
            title=title,
            task_type=task_type,
            parent_id=parent_id,
            dependencies=dependencies,
            metadata=metadata
        )
        
        self.tasks[task_id] = task
        return task
    
    def _extract_dimensions(self, query):
        """
        从查询中提取维度（扩展规则引擎）
        
        基于论文：Reasoning Core (arXiv:2608.05148, 2026-08-05)
        核心思想：50+ generators with semantic scorers and difficulty controls
        参考论文发现：semantic validity + compact targets + calibrated difficulty
        """
        # 扩展维度模式（从4个扩展到20+个）
        # 参考 Reasoning Core 论文的生成器设计
        DIMENSION_PATTERNS = {
            # 原有核心维度
            "channel": ["渠道", "平台", "来源", "入口", "途径"],
            "product": ["商品", "SKU", "品类", "类目", "商品类别", "产品"],
            "time": ["时间", "日期", "月份", "季度", "年度", "周"],
            "region": ["地区", "省份", "城市", "区域", "地域"],
            
            # 新增常见电商维度（论文建议：calibrated difficulty）
            "price": ["价格", "单价", "客单价", "价格段", "价格区间", "价位"],
            "customer": ["客户", "用户", "新老客", "会员", "非会员", "客户类型", "顾客"],
            "activity": ["活动", "促销", "优惠", "折扣", "营销", "大促", "campaign"],
            "traffic_source": ["流量", "访问来源", "流量来源", "入口来源"],
            "device": ["设备", "手机", "PC", "移动端", "桌面端", "终端"],
            "age": ["年龄", "年龄段", "年龄层", "年龄组"],
            "gender": ["性别", "男", "女", "男性", "女性"],
            "payment": ["支付", "支付方式", "付款方式", "付款", "结算"],
            "logistics": ["物流", "配送", "快递", "物流方式", "配送方式"],
            "brand": ["品牌", "牌子", "品牌商"],
            "shop": ["店铺", "商家", "卖家", "店家"],
            "coupon": ["优惠券", "券", "红包", "满减"],
            "member_level": ["会员等级", "会员级别", "等级"],
            "purchase_frequency": ["购买频次", "复购", "购买次数"],
            "order_status": ["订单状态", "状态", "已付款", "已发货"],
            "return_rate": ["退货率", "退款率", "退货"]
        }
        
        dimensions = []
        matched_keywords = set()  # 避免重复匹配
        
        # 遍历所有维度模式
        for dim_name, keywords in DIMENSION_PATTERNS.items():
            for keyword in keywords:
                if keyword in query and keyword not in matched_keywords:
                    dimensions.append(dim_name)
                    matched_keywords.add(keyword)
                    break  # 该维度已匹配，检查下一个维度
        
        # 论文建议：返回 compact targets（不返回冗余维度）
        return dimensions if dimensions else ["默认维度"]
    
    def _extract_comparison_entities(self, query):
        """提取对比实体（简单规则）"""
        # 这里可以用更复杂的 NER，但为了避免 LLM 调用，用规则
        entities = []
        
        # 简单示例
        if "淘宝" in query:
            entities.append("淘宝")
        if "京东" in query:
            entities.append("京东")
        if "抖音" in query:
            entities.append("抖音")
        
        if "上月" in query or "上个月" in query:
            entities.append("上月")
        if "本月" in query or "这个月" in query:
            entities.append("本月")
        
        return entities if entities else ["实体A", "实体B"]
    
    def _generate_execution_plan(self, root_task_id):
        """
        生成执行计划（拓扑排序）
        
        Returns:
            list: 按执行顺序排列的 task_id
        """
        # 简单的拓扑排序
        plan = []
        visited = set()
        
        def visit(task_id):
            if task_id in visited:
                return
            
            task = self.tasks.get(task_id)
            if not task:
                return
            
            # 先访问依赖
            for dep_id in task.dependencies:
                visit(dep_id)
            
            visited.add(task_id)
            plan.append(task_id)
        
        visit(root_task_id)
        return plan
    
    def get_task(self, task_id):
        """获取任务"""
        return self.tasks.get(task_id)
    
    def update_task_status(self, task_id, status, result=None, error=None):
        """更新任务状态"""
        task = self.tasks.get(task_id)
        if not task:
            return False
        
        task.status = status
        task.result = result
        task.error = error
        
        if status == "running" and not task.started_at:
            task.started_at = time.time()
        elif status in ["completed", "failed"]:
            task.completed_at = time.time()
        
        return True


# 全局单例
_default_decomposer = None


def get_default_decomposer():
    """获取默认分解器"""
    global _default_decomposer
    if _default_decomposer is None:
        _default_decomposer = TaskDecomposer()
    return _default_decomposer
