# -*- coding: utf-8 -*-
"""Dependency-free MCP-style adapter for governed Data Agent tools.

The adapter deliberately contains no data-source implementation. It translates a
small JSON-RPC-compatible MCP surface to DataSourceGateway, which preserves the
registry, policy, timeout, circuit breaker and trace controls.
"""
from __future__ import unicode_literals

from data_source_gateway import DataSourceGateway
from external_tool_registry import get_external_tool_registry


try:
    basestring
except NameError:  # pragma: no cover
    basestring = str


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _matches_type(value, expected):
    if expected == 'string':
        return isinstance(value, basestring)
    if expected == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == 'boolean':
        return isinstance(value, bool)
    if expected == 'array':
        return isinstance(value, list)
    if expected == 'object':
        return isinstance(value, dict)
    return True


class McpAdapter(object):
    def __init__(self, gateway=None, registry=None):
        self.gateway = gateway or DataSourceGateway()
        self.registry = registry or get_external_tool_registry()

    def _context_allows_tool(self, spec, context=None):
        context = context or {}
        allowed_tools = context.get('allowed_tools')
        if allowed_tools is not None and spec.get('tool_id') not in set(_as_list(allowed_tools)):
            return False
        capability = context.get('capability') or context.get('intent')
        allowed_intents = spec.get('allowed_intents') or []
        if capability and allowed_intents and capability not in allowed_intents:
            return False
        return True

    def _public_schema(self, schema, redact=False):
        schema = dict(schema or {})
        if not redact:
            return schema
        return {'required': list(schema.get('required') or []),
                'properties': dict((k, {'type': (v or {}).get('type')}) for k, v in (schema.get('properties') or {}).items())}

    def list_tools(self, context=None):
        context = context or {}
        tools = []
        for spec in self.registry.list_tools():
            if not self._context_allows_tool(spec, context=context):
                continue
            tools.append({
                'name': spec.get('tool_id'),
                'description': spec.get('description', ''),
                'inputSchema': self._public_schema(spec.get('input_schema') or {}, bool(context.get('redact_schema'))),
                'annotations': {
                    'readOnlyHint': spec.get('side_effect') == 'read_only',
                    'riskLevel': spec.get('risk_level'),
                },
            })
        return tools

    def _validate_arguments(self, spec, arguments):
        schema = spec.get('input_schema') or {}
        arguments = arguments or {}
        missing = [k for k in (schema.get('required') or []) if k not in arguments or arguments.get(k) in (None, '')]
        if missing:
            return 'missing required argument(s): %s' % ','.join(missing)
        properties = schema.get('properties') or {}
        for name, rules in properties.items():
            if name not in arguments or not isinstance(rules, dict):
                continue
            expected = rules.get('type')
            if expected and not _matches_type(arguments.get(name), expected):
                return 'invalid argument type for %s: expected %s' % (name, expected)
            if expected == 'string' and rules.get('min_len') is not None and len(arguments.get(name) or '') < int(rules.get('min_len')):
                return 'invalid argument length for %s' % name
            if expected in ('integer', 'number'):
                value = arguments.get(name)
                if rules.get('min') is not None and value < rules.get('min'):
                    return 'argument %s below min' % name
                if rules.get('max') is not None and value > rules.get('max'):
                    return 'argument %s above max' % name
        return None

    def call_tool(self, tool_name, arguments=None, context=None):
        arguments = arguments or {}
        context = context or {}
        spec = self.registry.get(tool_name)
        if not spec:
            return {'status': 'error', 'tool_id': tool_name, 'data': {},
                    'diagnostics': {'failure_type': 'external_tool_not_found', 'error': 'tool not registered'},
                    'trace_event': {}}
        if not self._context_allows_tool(spec, context=context):
            return {'status': 'blocked', 'tool_id': tool_name, 'data': {},
                    'diagnostics': {'failure_type': 'mcp_tool_not_allowed', 'error': 'tool not allowed by context'},
                    'trace_event': {}}
        validation_error = self._validate_arguments(spec, arguments)
        if validation_error:
            return {'status': 'error', 'tool_id': tool_name, 'data': {},
                    'diagnostics': {'failure_type': 'mcp_invalid_arguments', 'error': validation_error},
                    'trace_event': {}}
        if tool_name == 'semantic.catalog_read':
            return self.gateway.read_semantic_catalog(context=context)
        if tool_name == 'warehouse.schema_introspect':
            return self.gateway.introspect_schema(context=context)
        if tool_name == 'warehouse.query_sql':
            return self.gateway.query_sql(arguments.get('sql'), limit=arguments.get('limit'),
                                          offset=arguments.get('offset', 0), context=context)
        if tool_name == 'harness.run_suite':
            return self.gateway.run_harness_suite(arguments.get('suite'), context=context)
        return {'status': 'error', 'tool_id': tool_name, 'data': {},
                'diagnostics': {'failure_type': 'external_tool_not_found', 'error': 'tool has no adapter binding'},
                'trace_event': {}}

    def handle_request(self, request):
        request = request or {}
        request_id = request.get('id')
        method = request.get('method')
        params = request.get('params') or {}
        if method == 'tools/list':
            return self._success(request_id, {'tools': self.list_tools(params.get('context') or {})})
        if method == 'tools/call':
            name = params.get('name')
            if not name:
                return self._error(request_id, -32602, 'tool name is required')
            result = self.call_tool(name, params.get('arguments') or {}, params.get('context') or {})
            return self._success(request_id, result)
        return self._error(request_id, -32601, 'method not found: %s' % method)

    def _success(self, request_id, result):
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}

    def _error(self, request_id, code, message):
        return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


__all__ = ['McpAdapter']
