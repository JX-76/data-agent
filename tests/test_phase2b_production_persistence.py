import os, sys, pytest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir,"src"))
if ROOT not in sys.path: sys.path.insert(0,ROOT)

from identity_provider import IdentityContext
from postgres_persistence import PostgresPersistenceAdapter, PostgresPersistenceError
from redis_session_cache import RedisSessionCacheAdapter, RedisSessionCacheError
from clarification_state import ClarificationStateMachine, RepositoryClarificationStore
from human_review_state import HumanReviewStateMachine, RepositoryHumanReviewStore


class Cursor(object):
 def __init__(self, conn): self.conn=conn; self.rowcount=0; self.result=None
 def execute(self, sql, params=None):
  self.conn.calls.append((sql, params))
  if sql.startswith("SELECT set_config"):
   self.conn.current_tenant=params[0]; return
  if sql.startswith("INSERT INTO"):
   tenant,ns,key,payload,expires,created,updated=params
   import json
   self.conn.rows[(tenant,ns,key)]=json.loads(payload); self.rowcount=1; return
  if sql.startswith("SELECT payload"):
   tenant,ns=params[0],params[1]
   if "record_key LIKE" in sql:
    prefix=params[2].replace("%","")
    self.result=[(v,) for (t,n,k),v in self.conn.rows.items() if t==tenant and n==ns and k.startswith(prefix)]
   else:
    key=params[2]
    value=self.conn.rows.get((tenant,ns,key)); self.result=(value,) if value is not None else None
   return
  if sql.startswith("DELETE FROM"):
   tenant,ns=params[0],params[1]
   if "expires_at IS NOT NULL" in sql:
    keys=[key for key in self.conn.rows if key[0]==tenant and key[1]==ns]
    self.rowcount=len(keys)
    for key in keys: self.conn.rows.pop(key,None)
    return
   key=params[2]
   self.rowcount=1 if (tenant,ns,key) in self.conn.rows else 0
   self.conn.rows.pop((tenant,ns,key),None); return
 def fetchone(self): return self.result
 def fetchall(self): return self.result or []
 def close(self): pass
class Conn(object):
 def __init__(self): self.rows={}; self.calls=[]; self.commits=0; self.rollbacks=0
 def cursor(self): return Cursor(self)
 def commit(self): self.commits+=1
 def rollback(self): self.rollbacks+=1
 def close(self): pass

def access(t="t1",verified=True): return IdentityContext("u",t,["analyst"],verified,"jwt")

def test_postgres_fails_closed_without_enablement():
 with pytest.raises(PostgresPersistenceError): PostgresPersistenceAdapter(dsn="postgres://x", enabled=False)

def test_postgres_requires_verified_access_and_sets_rls_tenant():
 c=Conn(); repo=PostgresPersistenceAdapter(enabled=True, connection=c)
 with pytest.raises(PostgresPersistenceError): repo.save_session(access(verified=False),"s",{"v":1})
 repo.save_session(access("t1"),"s",{"v":1})
 assert repo.get_session(access("t1"),"s")["v"]==1
 assert any(call[0].startswith("SELECT set_config") and call[1]==("t1",) for call in c.calls)
 assert repo.get_session(access("t2"),"s") is None


def test_postgres_evidence_repository_is_tenant_and_session_scoped():
 c=Conn(); repo=PostgresPersistenceAdapter(enabled=True, connection=c)
 record={"evidence_id":"ev-pg","authority":"verified_execution","status":"ok"}
 repo.save_evidence(access("t1"),"sess-1","ev-pg",record)
 assert repo.get_evidence(access("t1"),"sess-1","ev-pg")["evidence_id"]=="ev-pg"
 assert repo.get_evidence(access("t2"),"sess-1","ev-pg") is None
 assert repo.get_evidence(access("t1"),"sess-2","ev-pg") is None
 assert repo.list_evidence(access("t1"),"sess-1")[0]["authority"]=="verified_execution"
 assert repo.delete_expired_evidence(access("t1"),now=999999)>=1
 assert repo.get_evidence(access("t1"),"sess-1","ev-pg") is None


def test_repository_backed_clarification_recovers_across_instances():
 c=Conn(); repo=PostgresPersistenceAdapter(enabled=True, connection=c); a=access("t1")
 s1=ClarificationStateMachine(store=RepositoryClarificationStore(repo,a))
 plan={"status":"need_clarification","clarification":{"question":"q","options":[{"id":"metric_query"}]}}
 s1.begin("sess","query",plan)
 s2=ClarificationStateMachine(store=RepositoryClarificationStore(repo,a))
 assert s2.describe("sess")["pending"] is True
 assert s2.resolve("sess","metric_query")["status"]=="ok"

def test_repository_backed_human_review_recovers_across_instances():
 c=Conn(); repo=PostgresPersistenceAdapter(enabled=True, connection=c); a=access("t1")
 h1=HumanReviewStateMachine(store=RepositoryHumanReviewStore(repo,a)); h1.begin("sess","q",{"status":"pending_human_review"})
 h2=HumanReviewStateMachine(store=RepositoryHumanReviewStore(repo,a))
 assert h2.describe("sess")["pending"] is True
 assert h2.decide("sess","approve",reviewer_id="r")["status"]=="ok"

class FakeRedis(object):
 def __init__(self): self.data={}
 def setex(self,k,ttl,v): self.data[k]=v
 def get(self,k): return self.data.get(k)
 def delete(self,k): return 1 if self.data.pop(k,None) is not None else 0
 def keys(self,pattern):
  prefix=pattern.replace("*","")
  return [k for k in self.data if k.startswith(prefix)]

def test_redis_fails_closed_without_enablement():
 with pytest.raises(RedisSessionCacheError): RedisSessionCacheAdapter(url="redis://x", enabled=False)

def test_redis_tenant_namespace_isolation():
 r=FakeRedis(); cache=RedisSessionCacheAdapter(enabled=True, client=r)
 cache.set_value(access("t1"),"k",{"v":1})
 cache.set_value(access("t2"),"k",{"v":2})
 assert cache.get_value(access("t1"),"k")["v"]==1
 assert cache.get_value(access("t2"),"k")["v"]==2
 assert cache.get_value(access("t1"),"missing") is None
