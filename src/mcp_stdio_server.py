# -*- coding: utf-8 -*-
"""Line-delimited JSON-RPC stdio transport for the governed MCP adapter.

The transport owns protocol framing, request-size limits and optional shared-token
checking only. Every business request is handed to McpAdapter, preserving the
DataSourceGateway -> executor -> policy -> trace path.
"""
from __future__ import unicode_literals

import json
import os
import sys
import time

from mcp_adapter import McpAdapter


DEFAULT_PROTOCOL_VERSION = '2024-11-05'
DEFAULT_MAX_REQUEST_BYTES = 65536


def _text(value):
    try:
        if isinstance(value, bytes):
            return value.decode('utf-8')
    except Exception:
        pass
    return value


def _json_error(request_id, code, message, data=None):
    error = {'code': code, 'message': message}
    if data is not None:
        error['data'] = data
    return {'jsonrpc': '2.0', 'id': request_id, 'error': error}


class McpStdioServer(object):
    """Small synchronous MCP stdio transport.

    One complete JSON-RPC object is accepted per input line and exactly one
    object is emitted per handled request. Notifications are intentionally
    ignored because this dependency-free transport has no async lifecycle.
    """

    def __init__(self, adapter=None, max_request_bytes=None, auth_token=None,
                 protocol_version=None, server_name='data-agent-mcp', server_version='0.1'):
        self.adapter = adapter or McpAdapter()
        self.max_request_bytes = int(max_request_bytes or os.environ.get(
            'DATA_AGENT_MCP_MAX_REQUEST_BYTES', DEFAULT_MAX_REQUEST_BYTES))
        self.auth_token = auth_token
        if self.auth_token is None:
            self.auth_token = os.environ.get('DATA_AGENT_MCP_AUTH_TOKEN') or None
        self.protocol_version = protocol_version or DEFAULT_PROTOCOL_VERSION
        self.server_name = server_name
        self.server_version = server_version
        self.events = []

    def handle_line(self, raw_line):
        """Decode and process a single JSON line without raising to the caller."""
        if raw_line is None:
            return None
        raw_line = _text(raw_line)
        if not raw_line.strip():
            return None
        size = len(raw_line.encode('utf-8'))
        if size > self.max_request_bytes:
            response = _json_error(None, -32001, 'request exceeds maximum size', {
                'max_request_bytes': self.max_request_bytes,
            })
            self._record(None, None, 'rejected', size, response)
            return response
        try:
            request = json.loads(raw_line)
        except Exception:
            response = _json_error(None, -32700, 'parse error')
            self._record(None, None, 'error', size, response)
            return response
        return self.handle_request(request, request_size=size)

    def handle_request(self, request, request_size=None):
        request = request or {}
        if not isinstance(request, dict):
            response = _json_error(None, -32600, 'invalid request')
            self._record(None, None, 'error', request_size, response)
            return response
        request_id = request.get('id')
        method = request.get('method')
        if request.get('jsonrpc') not in (None, '2.0'):
            response = _json_error(request_id, -32600, 'jsonrpc must be 2.0')
            self._record(request_id, method, 'error', request_size, response)
            return response
        if not method:
            response = _json_error(request_id, -32600, 'method is required')
            self._record(request_id, method, 'error', request_size, response)
            return response
        if not self._authorized(request):
            response = _json_error(request_id, -32002, 'unauthorized')
            self._record(request_id, method, 'rejected', request_size, response)
            return response

        if method == 'initialize':
            response = self._initialize_response(request_id, request.get('params') or {})
        elif method == 'ping':
            response = {'jsonrpc': '2.0', 'id': request_id, 'result': {}}
        elif method == 'notifications/initialized':
            response = None
        else:
            response = self.adapter.handle_request(request)
        self._record(request_id, method, 'ok' if response and not response.get('error') else 'error',
                     request_size, response)
        return response

    def serve(self, input_stream=None, output_stream=None):
        """Serve newline-delimited requests until EOF; return handled request count."""
        input_stream = input_stream or sys.stdin
        output_stream = output_stream or sys.stdout
        count = 0
        for raw_line in input_stream:
            response = self.handle_line(raw_line)
            if response is None:
                continue
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(',', ':')) + '\n')
            output_stream.flush()
            count += 1
        return count

    def _initialize_response(self, request_id, params):
        requested = params.get('protocolVersion')
        # A mismatched client version is accepted with the server's advertised
        # version, allowing clients to negotiate without a dependency on an SDK.
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': {
                'protocolVersion': self.protocol_version,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': self.server_name, 'version': self.server_version},
                'instructions': 'All tools execute through DataSourceGateway governance.',
                'clientProtocolVersion': requested,
            },
        }

    def _authorized(self, request):
        if not self.auth_token:
            return True
        params = request.get('params') or {}
        context = params.get('context') or {}
        meta = params.get('_meta') or {}
        candidate = context.get('auth_token') or meta.get('auth_token')
        return candidate == self.auth_token

    def _record(self, request_id, method, status, request_size, response):
        self.events.append({
            'name': 'mcp_transport_request',
            'trace_id': request_id,
            'method': method,
            'status': status,
            'request_size': request_size,
            'timestamp_ms': int(time.time() * 1000),
            'has_error': bool(response and response.get('error')),
        })


__all__ = ['McpStdioServer', 'DEFAULT_PROTOCOL_VERSION', 'DEFAULT_MAX_REQUEST_BYTES']
