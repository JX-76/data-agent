# -*- coding: utf-8 -*-
"""Prompt registry with per-chain system prompts.

This module keeps prompt selection deterministic and traceable.
Python 2.7 compatible.
"""

from __future__ import unicode_literals


class PromptEntry(object):
    def __init__(self, prompt_id, version='v1', system_prompt=''):
        self.prompt_id = prompt_id
        self.version = version
        self.system_prompt = system_prompt

    def to_dict(self):
        return {
            'prompt_id': self.prompt_id,
            'prompt_version': self.version,
            'system_prompt': self.system_prompt,
        }


class PromptRegistry(object):
    def __init__(self):
        self._entries = {}
        self._register_defaults()

    def _register_defaults(self):
        # Prompts here are role contracts. Runtime context must be compiled by
        # PromptContextCompiler so RAG, memory and tool evidence stay typed.
        prefix = u'你是受治理的数据分析助手。数值、趋势、排名、比较和归因只能由当前匹配的工具/SQL证据支持；SOP、领域知识和历史记忆不能充当事实；证据不足时必须澄清或拒绝猜测。'
        self.register('router', 'v2', prefix + u' 你是路由器，只输出意图、指标、维度、任务类型及澄清需求，不输出业务结论。')
        self.register('planner', 'v2', prefix + u' 你是计划器，只输出结构化 AnalysisPlan；把 SOP 转为待验证步骤，不执行 SQL。')
        self.register('sql_generator', 'v2', prefix + u' 你是 SQL 生成器，只能基于已批准 plan 和受治理语义层生成候选只读 SQL。')
        self.register('sql_reviewer', 'v2', prefix + u' 你是 SQL 审核器，只检查只读、时间范围、敏感字段、白名单和 limit。')
        self.register('analyst', 'v2', prefix + u' 你是分析师，仅解释当前执行证据，明确区分事实、假设和待验证项。')
        self.register('report', 'v2', prefix + u' 你是报告生成器，只组织可追溯的已验证内容，不新增事实。')
        self.register('human_review', 'v2', prefix + u' 你是人工审核辅助器，请生成清晰的风险说明和审核清单。')

    def register(self, prompt_id, version, system_prompt):
        self._entries[prompt_id] = PromptEntry(prompt_id, version, system_prompt)

    def get(self, prompt_id):
        return self._entries.get(prompt_id)

    def system_prompt(self, prompt_id):
        entry = self.get(prompt_id)
        return entry.system_prompt if entry else ''

    def chain(self, chain_name='default'):
        if chain_name == 'safety':
            return ['router', 'planner', 'sql_generator', 'sql_reviewer', 'analyst', 'report', 'human_review']
        return ['router', 'planner', 'analyst', 'report']

    def chain_spec(self, chain_name='default'):
        spec = []
        for prompt_id in self.chain(chain_name):
            entry = self.get(prompt_id)
            if entry:
                spec.append(entry.to_dict())
        return spec


def get_prompt_registry():
    return PromptRegistry()


__all__ = ['PromptRegistry', 'PromptEntry', 'get_prompt_registry']
