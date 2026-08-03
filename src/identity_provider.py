# -*- coding: utf-8 -*-
"""Trusted identity resolution boundary; request bodies are never an identity source."""
from __future__ import unicode_literals
import os

class IdentityError(Exception): pass
class IdentityContext(object):
 def __init__(self,user_id,tenant_id,roles=None,verified=False,source="unknown",raw_claims=None):
  self.user_id=user_id or "anonymous";self.tenant_id=tenant_id or "default";self.roles=list(roles or []);self.verified=bool(verified);self.source=source;self.raw_claims=dict(raw_claims or {})
 def to_dict(self): return {"user_id":self.user_id,"tenant_id":self.tenant_id,"roles":list(self.roles),"verified":self.verified,"source":self.source}
class IdentityProvider(object):
 def resolve(self,headers=None): raise NotImplementedError
class DevelopmentMockIdentityProvider(IdentityProvider):
 def __init__(self,environment=None): self.environment=environment or os.environ.get("AGENT_ENV","development")
 def resolve(self,headers=None):
  if self.environment not in ("development","test"): raise IdentityError("development mock identity is disabled outside development")
  h=headers or {};return IdentityContext(h.get("x-dev-user-id") or h.get("X-Dev-User-Id") or "dev-user",h.get("x-dev-tenant-id") or h.get("X-Dev-Tenant-Id") or "default",(h.get("x-dev-roles") or "analyst").split(","),True,"mock")
class JWTClaimsIdentityProvider(IdentityProvider):
 """Adapter boundary. Upstream middleware must validate JWT/OIDC signature/audience."""
 def __init__(self,claims_resolver=None): self.claims_resolver=claims_resolver
 def resolve(self,headers=None):
  if not self.claims_resolver: raise IdentityError("JWT/OIDC claims resolver is not configured")
  c=self.claims_resolver(headers or {}) or {}
  if not c.get("sub") or not c.get("tenant_id"): raise IdentityError("verified JWT claims require sub and tenant_id")
  return IdentityContext(c["sub"],c["tenant_id"],c.get("roles") or [],True,"jwt",c)
class AccessContextProvider(object):
 def __init__(self,provider): self.provider=provider
 def resolve(self,headers=None,body=None):
  identity=self.provider.resolve(headers)
  # body is intentionally ignored: clients cannot override verified identity.
  return identity
__all__=["IdentityError","IdentityContext","IdentityProvider","DevelopmentMockIdentityProvider","JWTClaimsIdentityProvider","AccessContextProvider"]
