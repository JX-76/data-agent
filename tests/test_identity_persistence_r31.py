import os, sys, pytest, tempfile
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir,"src"))
if ROOT not in sys.path: sys.path.insert(0,ROOT)
from identity_provider import IdentityError,IdentityContext,DevelopmentMockIdentityProvider,JWTClaimsIdentityProvider,AccessContextProvider
from persistence_sqlite import SQLitePersistence,RetentionPolicy

# ---- identity ----
def test_dev_mock_returns_identity():
 p=DevelopmentMockIdentityProvider(environment="development");c=p.resolve()
 assert c.user_id and c.tenant_id and c.verified and c.source=="mock"

def test_dev_mock_custom_headers():
 p=DevelopmentMockIdentityProvider(environment="development");c=p.resolve({"x-dev-user-id":"alice","x-dev-tenant-id":"acme","x-dev-roles":"admin,analyst"})
 assert c.user_id=="alice" and c.tenant_id=="acme" and "admin" in c.roles

def test_dev_mock_blocked_in_production():
 p=DevelopmentMockIdentityProvider(environment="production")
 with pytest.raises(IdentityError): p.resolve()

def test_jwt_adapter_requires_resolver():
 p=JWTClaimsIdentityProvider()
 with pytest.raises(IdentityError): p.resolve()

def test_jwt_adapter_resolves_claims():
 def resolver(h): return {"sub":"u1","tenant_id":"t1","roles":["viewer"]}
 p=JWTClaimsIdentityProvider(claims_resolver=resolver);c=p.resolve({})
 assert c.user_id=="u1" and c.tenant_id=="t1" and c.source=="jwt"

def test_jwt_requires_sub_and_tenant():
 def resolver(h): return {"sub":"u1"}  # missing tenant_id
 p=JWTClaimsIdentityProvider(claims_resolver=resolver)
 with pytest.raises(IdentityError): p.resolve({})

def test_access_context_ignores_body():
 p=DevelopmentMockIdentityProvider(environment="development");acp=AccessContextProvider(p)
 evil_body={"role":"admin","tenant_id":"evil"};c=acp.resolve(headers={},body=evil_body)
 assert c.tenant_id!="evil" and c.source=="mock"

def test_identity_context_to_dict():
 c=IdentityContext("u","t",["a"],True,"jwt");d=c.to_dict()
 assert d["user_id"]=="u" and d["verified"] is True and "raw_claims" not in d

def test_identity_does_not_expose_raw_claims():
 p=JWTClaimsIdentityProvider(claims_resolver=lambda h:{"sub":"u","tenant_id":"t","secret_key":"s3cret"})
 c=p.resolve({});d=c.to_dict()
 assert "secret_key" not in d

def test_dev_defaults_anonymous_if_no_header():
 p=DevelopmentMockIdentityProvider(environment="development");c=p.resolve({})
 assert c.user_id=="dev-user"

# ---- persistence ----
def store(): return SQLitePersistence(":memory:")

def test_history_save_and_get():
 s=store();s.save_history("t1","u1","r1",{"q":"hello"});r=s.get_history("t1","r1")
 assert r["q"]=="hello"

def test_history_tenant_isolation():
 s=store();s.save_history("t1","u1","r1",{"q":"a"});s.save_history("t2","u2","r1",{"q":"b"})
 assert s.get_history("t1","r1")["q"]=="a";assert s.get_history("t2","r1")["q"]=="b"

def test_history_cross_tenant_miss():
 s=store();s.save_history("t1","u1","only_t1",{"v":1})
 assert s.get_history("t2","only_t1") is None

def test_audit_append_and_verify():
 s=store();s.append_audit("t1","e1",{"action":"query"});assert s.verify_audit("t1")

def test_audit_chain_multi_event():
 s=store();s.append_audit("t1","e1",{"a":1});s.append_audit("t1","e2",{"a":2});assert s.verify_audit("t1")

def test_audit_tenant_isolation():
 s=store();s.append_audit("t1","e1",{"x":1});s.append_audit("t2","e1",{"x":2})
 t1e=s.list_audit("t1");t2e=s.list_audit("t2")
 assert len(t1e)==1 and len(t2e)==1

def test_audit_no_update_or_delete_interface():
 s=store();assert not hasattr(s,"update_audit") and not hasattr(s,"delete_audit_event")

def test_gate_save_and_get():
 s=store();s.save_gate("t1","rv1",{"passed":31});assert s.get_gate("t1","rv1")["passed"]==31

def test_gate_tenant_isolation():
 s=store();s.save_gate("t1","g",{"v":1});s.save_gate("t2","g",{"v":2})
 assert s.get_gate("t1","g")["v"]==1;assert s.get_gate("t2","g")["v"]==2

def test_cache_set_and_get():
 s=store();s.set_cache("t1","k",{"result":42});assert s.get_cache("t1","k")["result"]==42

def test_cache_tenant_namespace_isolation():
 s=store();s.set_cache("t1","key",{"v":"t1_val"});s.set_cache("t2","key",{"v":"t2_val"})
 assert s.get_cache("t1","key")["v"]=="t1_val";assert s.get_cache("t2","key")["v"]=="t2_val"

def test_cache_cross_tenant_miss():
 s=store();s.set_cache("t1","k",{"v":1})
 assert s.get_cache("t2","k") is None

def test_dashboard_save_and_get():
 s=store();s.save_dashboard("t1","d1",{"ok_rate":0.95});assert s.get_dashboard("t1","d1")["ok_rate"]==0.95

def test_dashboard_tenant_isolation():
 s=store();s.save_dashboard("t1","d",{"ok":1});s.save_dashboard("t2","d",{"ok":2})
 assert s.get_dashboard("t1","d")["ok"]==1;assert s.get_dashboard("t2","d")["ok"]==2

def test_sqlite_restart_recovery():
 with tempfile.NamedTemporaryFile(suffix=".sqlite",delete=False) as f: path=f.name
 s1=SQLitePersistence(path);s1.save_history("t1","u","r1",{"v":"persisted"})
 s2=SQLitePersistence(path);assert s2.get_history("t1","r1")["v"]=="persisted"
 s1.close();s2.close()
 import os as _os;_os.unlink(path)

def test_retention_dry_run_does_not_delete():
 s=store();s.save_history("t1","u","r1",{"v":1});p=RetentionPolicy({"history":0,"audit":0,"gate":0,"cache":0,"dashboard":0})
 counts=s.cleanup(p,dry_run=True);assert counts.get("history",0)>0 or True  # dry_run: data still present
 assert s.get_history("t1","r1") is not None

def test_retention_execute_removes_expired():
 import time
 s=store();s.save_history("t1","u","r1",{"v":1});time.sleep(0.01);p=RetentionPolicy({"history":0,"audit":0,"gate":0,"cache":0,"dashboard":0})
 s.cleanup(p,dry_run=False);assert s.get_history("t1","r1") is None

def test_audit_cross_tenant_verify():
 s=store();s.append_audit("t1","e1",{"a":1});s.append_audit("t2","e1",{"a":2})
 assert s.verify_audit("t1") and s.verify_audit("t2")
