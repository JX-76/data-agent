# -*- coding: utf-8 -*-
"""P4 tool governance and safety control plane.

Server-side capability routing and semantic validation.  This is an offline
contract/test adapter; it does not expose arbitrary shell execution.
"""
from __future__ import unicode_literals
import os
import re

TOOL_MANIFEST_CONTRACT = 'allowed_tool_manifest_v1'
TOOL_EVALUATION_CONTRACT = 'tool_governance_eval_v1'

_DANGEROUS_PROMPT_TERMS = ('ignore policy', 'bypass permission', 'drop table', 'dump secrets', 'exfiltrate', 'disable rls')
_SHELL_FORBIDDEN = re.compile(r"(\||&&|;|`|\$\(|>|<|\n|\r)")
_PATH_TRAVERSAL = re.compile(r"(^|[\\/])\.\.([\\/]|$)")


class ToolSpec(object):
    def __init__(self, tool_id, capability, schema_version='v1', input_schema=None,
                 risk_level='low', allowed_roles=None, requires_approval=False):
        self.tool_id = tool_id; self.capability = capability; self.schema_version = schema_version
        self.input_schema = dict(input_schema or {}); self.risk_level = risk_level or 'low'
        self.allowed_roles = list(allowed_roles or ['analyst', 'admin']); self.requires_approval = bool(requires_approval)
    def to_manifest_item(self):
        return {'tool_id': self.tool_id, 'capability': self.capability, 'schema_version': self.schema_version,
                'input_schema': dict(self.input_schema), 'risk_level': self.risk_level,
                'requires_approval': self.requires_approval}


class CapabilityRouter(object):
    def __init__(self, specs=None):
        self.specs = list(specs or [
            ToolSpec('warehouse.query_sql', 'data.query', input_schema={'required': ['sql'], 'properties': {'sql': {'type': 'string'}, 'limit': {'type': 'integer', 'min': 1, 'max': 1000}}}),
            ToolSpec('semantic.catalog_read', 'metadata.read'),
            ToolSpec('operation.read_file_safe', 'file.read', input_schema={'required': ['path'], 'properties': {'path': {'type': 'string'}}}),
            ToolSpec('operation.shell_dsl', 'ops.dsl', risk_level='high', allowed_roles=['admin'], requires_approval=True,
                     input_schema={'required': ['operation', 'argv'], 'properties': {'operation': {'type': 'string'}, 'argv': {'type': 'array'}}}),
        ])

    def build_manifest(self, requested_capability, context=None):
        ctx = context or {}; role = ctx.get('role', 'analyst'); approved = ctx.get('approval_status') == 'approved'
        items = []
        for spec in self.specs:
            if spec.capability != requested_capability: continue
            if role not in spec.allowed_roles: continue
            if spec.requires_approval and not approved: continue
            items.append(spec.to_manifest_item())
        return {'contract': TOOL_MANIFEST_CONTRACT, 'capability': requested_capability, 'schema_version': 'manifest_v1',
                'trace': {'case_id': ctx.get('case_id'), 'tenant_id': ctx.get('tenant_id'), 'role': role}, 'tools': items}


class SemanticToolValidator(object):
    def validate(self, manifest, tool_id, args, context=None):
        ctx = context or {}; args = args or {}; errors = []
        spec = None
        for item in manifest.get('tools') or []:
            if item.get('tool_id') == tool_id: spec = item
        if spec is None: errors.append('tool_not_in_allowed_manifest')
        else:
            schema = spec.get('input_schema') or {}; props = schema.get('properties') or {}
            for key in schema.get('required') or []:
                if key not in args or args.get(key) in (None, ''): errors.append('missing_required_arg:%s' % key)
            for key, rule in props.items():
                if key not in args: continue
                value = args.get(key); typ = rule.get('type')
                if typ == 'integer' and (not isinstance(value, int) or isinstance(value, bool)): errors.append('arg_type:%s' % key)
                if typ == 'string' and not isinstance(value, basestring if 'basestring' in globals() else str): errors.append('arg_type:%s' % key)
                if typ == 'integer' and isinstance(value, int):
                    if 'max' in rule and value > int(rule.get('max')): errors.append('arg_max:%s' % key)
                    if 'min' in rule and value < int(rule.get('min')): errors.append('arg_min:%s' % key)
        text = (ctx.get('user_prompt') or '').lower()
        if any(term in text for term in _DANGEROUS_PROMPT_TERMS): errors.append('prompt_injection_high_risk')
        if tool_id == 'operation.shell_dsl': errors.extend(self._validate_shell_dsl(args))
        if tool_id == 'operation.read_file_safe': errors.extend(self._validate_path(args.get('path')))
        return {'allowed': not errors, 'errors': errors, 'contract': 'tool_semantic_validation_v1',
                'trace': {'tool_id': tool_id, 'manifest_schema_version': manifest.get('schema_version'), 'tenant_id': ctx.get('tenant_id')}}

    def _validate_shell_dsl(self, args):
        errors = []; op = args.get('operation'); argv = args.get('argv') or []
        if op not in ('list_dir', 'read_text', 'run_harness'): errors.append('operation_not_allowlisted')
        for part in argv:
            if _SHELL_FORBIDDEN.search(str(part)): errors.append('raw_shell_construct_forbidden')
            errors.extend(self._validate_path(str(part)))
        return sorted(set(errors))

    def _validate_path(self, path):
        if not path: return ['path_required']
        text = str(path)
        errors = []
        if _PATH_TRAVERSAL.search(text): errors.append('path_traversal_forbidden')
        if text.startswith('~') or '$' in text or '%' in text: errors.append('env_or_home_expansion_forbidden')
        return errors


class ToolGovernanceEvaluator(object):
    def evaluate(self, cases):
        total = len(cases or []); passed = 0; failures = []
        for case in cases or []:
            got = bool(case.get('actual_allowed')); expected = bool(case.get('expected_allowed'))
            if got == expected: passed += 1
            else: failures.append(case.get('case_id'))
        return {'contract': TOOL_EVALUATION_CONTRACT, 'total': total, 'passed': passed,
                'tool_selection_accuracy': float(passed) / float(total or 1), 'failures': failures}

__all__ = ['ToolSpec', 'CapabilityRouter', 'SemanticToolValidator', 'ToolGovernanceEvaluator', 'TOOL_MANIFEST_CONTRACT']
