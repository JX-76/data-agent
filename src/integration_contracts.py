# -*- coding: utf-8 -*-
"""Environment-safe connector contracts used before any real enterprise integration."""
from __future__ import unicode_literals


class ConnectorSpec(object):
    def __init__(self, connector_id, environment='sandbox', capabilities=None, schema_version=None,
                 read_only=True, approved=False, owner=None):
        self.connector_id = connector_id
        self.environment = environment
        self.capabilities = capabilities or []
        self.schema_version = schema_version or 'v1'
        self.read_only = bool(read_only)
        self.approved = bool(approved)
        self.owner = owner

    def to_dict(self):
        return {'connector_id': self.connector_id, 'environment': self.environment,
                'capabilities': list(self.capabilities), 'schema_version': self.schema_version,
                'read_only': self.read_only, 'approved': self.approved, 'owner': self.owner}


def validate_real_connection(spec, config=None):
    """Fail closed: a real connector requires explicit production approval."""
    config = config or {}
    if spec.environment != 'real':
        return {'allowed': True, 'reason': 'non_real_environment'}
    if not spec.read_only:
        return {'allowed': False, 'reason': 'real_connector_must_be_readonly'}
    if not spec.approved:
        return {'allowed': False, 'reason': 'real_connector_not_approved'}
    if config.get('DATA_AGENT_APPROVE_REAL_CONNECTION') != 'true':
        return {'allowed': False, 'reason': 'missing_explicit_real_connection_approval'}
    return {'allowed': True, 'reason': 'explicitly_approved'}


__all__ = ['ConnectorSpec', 'validate_real_connection']
