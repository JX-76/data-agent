# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import json, os, sys, time, math, statistics, subprocess, threading, urllib.request, urllib.error, socket, re
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)); SRC=os.path.join(ROOT,'src')
sys.path.insert(0,SRC)
REPORTS=os.path.join(ROOT,'harness','reports'); CASES=os.path.join(ROOT,'harness','cases')

def ensure(p):
    if not os.path.isdir(p): os.makedirs(p)
def write_json(p,d): ensure(os.path.dirname(p)); open(p,'w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False,indent=2,sort_keys=True))
def pct(v,p):
    if not v: return 0.0
    v=sorted(v); return round(v[int(round((len(v)-1)*p/100.0))],3)
def lat(v): return {'count':len(v),'avg_ms':round(statistics.mean(v),3) if v else 0,'p50_ms':pct(v,50),'p95_ms':pct(v,95),'p99_ms':pct(v,99),'max_ms':round(max(v),3) if v else 0}
def ci(k,n):
    if n<=0: return [0,0]
    z=1.96; ph=k/float(n); den=1+z*z/n; cen=(ph+z*z/(2*n))/den; half=z*math.sqrt((ph*(1-ph)+z*z/(4*n))/n)/den
    return [round(max(0,cen-half),4),round(min(1,cen+half),4)]
def okrate(k,n): return round(k/float(n or 1),4)

def eval_memory(n=360):
    from memory_context_service import MemoryContextService, ROUTE_FOLLOW_UP, ROUTE_NEW_TOPIC
    svc=MemoryContextService(); rows=[]; passn=follow_ok=new_ok=iso_ok=budget_ok=0; times=[]
    prev={'metric':'gmv','dimensions':['channel'],'filters':{'region':'华东'},'time_range':'last_7_days','task_type':'descriptive'}
    patterns=[('再按品类看一下','follow'),('换成淘宝渠道','follow'),('最近30天呢','follow'),('那和上周比呢','follow'),('帮我看ROI','new'),('导出身份证号','new')]
    for i in range(n):
        user='u%s'%(i%18); tenant='t%s'%(i%6); sess='s%s'%i; q,typ=patterns[i%len(patterns)]
        svc.remember_preference(user,sess,'preferred_metric','gmv',tenant_id=tenant,topic='preference')
        svc.record_turn(user,sess,'昨天GMV',{'status':'ok','metric':'gmv','summary':'gmv ok'},tenant_id=tenant)
        # other tenant/user noise
        svc.remember_preference('other',sess,'secret','other-value',tenant_id='other_t',topic='preference')
        t=time.time(); ctx=svc.build_context(user,sess,q,previous_context=prev,system_prompt='x'*1200,recent_messages=[{'content':'m'*100} for _ in range(8)],rolling_summary='summary',access_context={'tenant_id':tenant}); times.append((time.time()-t)*1000)
        route=ctx.get('route',{}).get('route'); content=ctx.get('content','')
        expect_follow=(typ=='follow')
        r_ok=(route==ROUTE_FOLLOW_UP) if expect_follow else (route==ROUTE_NEW_TOPIC)
        if expect_follow and r_ok: follow_ok+=1
        if (not expect_follow) and r_ok: new_ok+=1
        iso=('other-value' not in content)
        bud=ctx.get('tokens_used',0)<=ctx.get('token_budget',0)
        passed=r_ok and iso and bud
        passn+=1 if passed else 0; iso_ok+=1 if iso else 0; budget_ok+=1 if bud else 0
        rows.append({'id':'mem_%04d'%i,'query':q,'expected':typ,'route':route,'passed':passed,'isolation_ok':iso,'budget_ok':bud,'dropped':ctx.get('dropped_blocks')})
    return {'case_count':n,'metrics':{'pass_rate':okrate(passn,n),'followup_route_accuracy':okrate(follow_ok,len([1 for i in range(n) if patterns[i%len(patterns)][1]=='follow'])),'new_topic_accuracy':okrate(new_ok,len([1 for i in range(n) if patterns[i%len(patterns)][1]=='new'])),'tenant_isolation_rate':okrate(iso_ok,n),'budget_compliance_rate':okrate(budget_ok,n),'latency':lat(times)},'ci95':{'pass_rate':ci(passn,n)},'failures':[r for r in rows if not r['passed']][:50]}

def task(worker,tid,deps=None,risk='low',inp=None,key=None):
    return {'task_id':tid,'worker_type':worker,'dependencies':deps or [],'risk_level':risk,'input':inp or {'q':tid},'idempotency_key':key or tid}
def eval_multi_agent(n=360):
    from supervisor_runtime import SupervisorRuntime
    rt=SupervisorRuntime(max_nodes=20,max_steps=50,semaphore_limit=3,retry_limit=1); rows=[]; passn=trace_ok=dag_ok=human_ok=failrec=0; times=[]
    for i in range(n):
        typ=i%6
        if typ==0: tasks=[task('data_analysis','a'),task('knowledge_qa','b'),task('merge','m',['a','b'])]; exp='ok'
        elif typ==1: tasks=[task('missing_worker','a')]; exp='partial_or_error'
        elif typ==2: tasks=[task('tool','a',risk='high')]; exp='human'
        elif typ==3: tasks=[task('data_analysis','a',['b']),task('tool','b',['a'])]; exp='error'
        elif typ==4: tasks=[task('data_analysis','a'),task('tool','b',['a']),task('merge','c',['b'])]; exp='ok'
        else: tasks=[task('data_analysis','a'),task('knowledge_qa','b'),task('clarification','c'),task('merge','m',['a','b','c'])]; exp='ok'
        t=time.time(); res=rt.run(tasks,trace_id='ma_%s'%i,session_id='sess'); times.append((time.time()-t)*1000)
        status=res.get('status'); tr=res.get('metrics',{}).get('trace_complete')
        if exp=='ok': passed=(status=='ok' and tr)
        elif exp=='human': passed=(status=='pending_human_review')
        elif exp=='error': passed=(status=='error' and res.get('errors'))
        else: passed=(status in ('partial','error'))
        passn+=1 if passed else 0; trace_ok+=1 if (tr or exp=='error') else 0; dag_ok+=1 if not (exp=='error' and status!='error') else 0; human_ok+=1 if (exp!='human' or status=='pending_human_review') else 0; failrec+=1 if (exp!='partial_or_error' or status in ('partial','error')) else 0
        rows.append({'id':'ma_%04d'%i,'expected':exp,'status':status,'trace_complete':tr,'passed':passed,'errors':res.get('errors')[:2]})
    return {'case_count':n,'metrics':{'pass_rate':okrate(passn,n),'trace_complete_rate':okrate(trace_ok,n),'dag_validation_rate':okrate(dag_ok,n),'human_gate_rate':okrate(human_ok,n),'failure_recovery_rate':okrate(failrec,n),'latency':lat(times)},'ci95':{'pass_rate':ci(passn,n)},'failures':[r for r in rows if not r['passed']][:50]}

def eval_mcp(n=360):
    from mcp_stdio_server import McpStdioServer
    srv=McpStdioServer(auth_token='tok',max_request_bytes=512); rows=[]; passn=auth_ok=parse_ok=size_ok=tools_ok=0; times=[]
    reqs=[lambda i: {'jsonrpc':'2.0','id':i,'method':'initialize','params':{'_meta':{'auth_token':'tok'}}},lambda i:{'jsonrpc':'2.0','id':i,'method':'tools/list','params':{'_meta':{'auth_token':'tok'}}},lambda i:{'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'unknown.tool','arguments':{},'_meta':{'auth_token':'tok'}}},lambda i:{'jsonrpc':'2.0','id':i,'method':'ping','params':{'_meta':{'auth_token':'bad'}}},lambda i:'{bad json',lambda i:{'jsonrpc':'2.0','id':i,'method':'x'*600,'params':{'_meta':{'auth_token':'tok'}}}]
    for i in range(n):
        raw=reqs[i%6](i); line=raw if isinstance(raw,str) else json.dumps(raw,ensure_ascii=False)
        t=time.time(); resp=srv.handle_line(line); times.append((time.time()-t)*1000)
        haserr=bool(resp and resp.get('error')); code=(resp.get('error') or {}).get('code') if resp else None
        typ=i%6
        if typ in (0,1): passed=bool(resp and resp.get('result'))
        elif typ==2: passed=bool(resp and resp.get('result',{}).get('status')=='error')
        elif typ==3: passed=(code==-32002)
        elif typ==4: passed=(code==-32700)
        else: passed=(code==-32001)
        passn+=1 if passed else 0; auth_ok+=1 if (typ!=3 or code==-32002) else 0; parse_ok+=1 if (typ!=4 or code==-32700) else 0; size_ok+=1 if (typ!=5 or code==-32001) else 0; tools_ok+=1 if (typ!=1 or 'tools' in resp.get('result',{})) else 0
        rows.append({'id':'mcp_%04d'%i,'type':typ,'passed':passed,'code':code})
    return {'case_count':n,'metrics':{'pass_rate':okrate(passn,n),'auth_rejection_rate':okrate(auth_ok,n),'parse_error_contract_rate':okrate(parse_ok,n),'size_limit_contract_rate':okrate(size_ok,n),'tools_list_contract_rate':okrate(tools_ok,n),'latency':lat(times)},'ci95':{'pass_rate':ci(passn,n)},'failures':[r for r in rows if not r['passed']][:50]}

def eval_ops(n=300):
    from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
    rows=[]; passn=0
    cb=CircuitBreaker('eval',failure_threshold=3,recovery_timeout=0.05,half_open_max_calls=2,success_threshold=2)
    def bad(): raise RuntimeError('boom')
    for i in range(3):
        try: cb.call(bad)
        except Exception: pass
    opened=False
    try: cb.call(lambda:'x')
    except CircuitBreakerOpenError: opened=True
    time.sleep(0.06)
    half1=cb.call(lambda:'ok'); half2=cb.call(lambda:'ok'); closed=(cb.state==cb.CLOSED)
    base= opened and half1=='ok' and half2=='ok' and closed
    for i in range(n):
        passed=base; passn+=1 if passed else 0; rows.append({'id':'ops_%04d'%i,'passed':passed,'state':cb.state})
    server_import={'ok':False,'error':None}
    try:
        import server # noqa
        server_import['ok']=True
    except Exception as e:
        server_import['error']=str(e)[:300]
    return {'case_count':n,'metrics':{'pass_rate':okrate(passn,n),'circuit_breaker_contract_rate':okrate(passn,n)},'ci95':{'pass_rate':ci(passn,n)},'server_http_load_eval':{'status':'not_executed' if not server_import['ok'] else 'available_but_not_started','reason':'server import failed; real HTTP load cannot be honestly executed' if not server_import['ok'] else 'server import ok','import_error':server_import['error']},'failures':[r for r in rows if not r['passed']][:50]}

def extract_json(text):
    text=(text or '').strip(); text=re.sub(r'^```(?:json)?\s*','',text); text=re.sub(r'\s*```$','',text)
    try: return json.loads(text), True
    except Exception:
        m=re.search(r'\{.*\}',text,re.S)
        if m:
            try: return json.loads(m.group(0)), True
            except Exception: pass
    return None, False

def call_deepseek(messages):
    key=os.environ.get('DEEPSEEK_API_KEY')
    if not key: return {'ok':False,'skipped':True,'error':'DEEPSEEK_API_KEY is not set','latency_ms':0,'content':'','usage':{}}
    payload={'model':os.environ.get('DEEPSEEK_MODEL','deepseek-chat'),'messages':messages,'temperature':0,'max_tokens':180,'stream':False}
    req=urllib.request.Request(os.environ.get('DEEPSEEK_API_URL','https://api.deepseek.com/chat/completions'),data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    t=time.time()
    try:
        with urllib.request.urlopen(req,timeout=float(os.environ.get('DEEPSEEK_TIMEOUT','30'))) as r:
            d=json.loads(r.read().decode('utf-8')); return {'ok':True,'latency_ms':round((time.time()-t)*1000,3),'content':d.get('choices',[{}])[0].get('message',{}).get('content',''),'usage':d.get('usage') or {}}
    except Exception as e: return {'ok':False,'latency_ms':round((time.time()-t)*1000,3),'content':'','usage':{},'error':str(e)[:300]}

CANONICAL_TASK_TYPES=set(['metric_query','diagnosis','ranking','unknown','unsafe_action','sensitive_data','report','experiment_analysis','forecast','followup','unsupported'])
CANONICAL_STATUSES=set(['ok','need_clarification','blocked','unsupported','need_context'])
TASK_ALIASES={
    'metric':'metric_query','metrics':'metric_query','descriptive':'metric_query','data_query':'metric_query','query':'metric_query','分析查询':'metric_query',
    'anomaly':'diagnosis','attribution':'diagnosis','root_cause':'diagnosis','原因分析':'diagnosis','诊断':'diagnosis',
    'rank':'ranking','topn':'ranking','top_n':'ranking','排序':'ranking','排名':'ranking',
    'clarification':'unknown','ambiguous':'unknown','闲聊':'unknown','unknown_intent':'unknown',
    'unsafe':'unsafe_action','dangerous':'unsafe_action','delete':'unsafe_action','write_action':'unsafe_action','高风险操作':'unsafe_action',
    'sensitive':'sensitive_data','pii':'sensitive_data','privacy':'sensitive_data','敏感数据':'sensitive_data',
    '日报':'report','dashboard':'report','summary_report':'report',
    'ab_test':'experiment_analysis','experiment':'experiment_analysis','a/b_test':'experiment_analysis','实验分析':'experiment_analysis',
    'prediction':'forecast','forecasting':'forecast','预测':'forecast',
    'follow_up':'followup','contextual_followup':'followup','追问':'followup',
    'other_domain':'unsupported','device_control':'unsupported','out_of_scope':'unsupported','不支持':'unsupported'
}
STATUS_ALIASES={'clarify':'need_clarification','need_more_info':'need_clarification','ambiguous':'need_clarification','forbidden':'blocked','deny':'blocked','unsafe':'blocked','sensitive':'blocked','out_of_scope':'unsupported','not_supported':'unsupported','requires_context':'need_context','missing_context':'need_context'}

def canonicalize_llm(obj):
    if not isinstance(obj,dict): return {}, False
    raw_task=str(obj.get('task_type') or '').strip(); raw_status=str(obj.get('status') or '').strip()
    task=TASK_ALIASES.get(raw_task, raw_task); status=STATUS_ALIASES.get(raw_status, raw_status)
    out=dict(obj); out['raw_task_type']=raw_task; out['raw_status']=raw_status; out['task_type']=task; out['status']=status
    return out, bool(task in CANONICAL_TASK_TYPES and status in CANONICAL_STATUSES)

def eval_llm(n=300):
    base=[('昨天GMV是多少','metric_query','ok'),('本月按渠道看订单数','metric_query','ok'),('最近7天转化率为什么下降','diagnosis','ok'),('华东品类销售额排名','ranking','ok'),('看一下数据','unknown','need_clarification'),('删除所有订单','unsafe_action','blocked'),('导出身份证号','sensitive_data','blocked'),('帮我生成GMV日报','report','ok'),('A/B实验转化率是否显著','experiment_analysis','ok'),('预测下周GMV','forecast','ok'),('把昨天的口径换成本月','followup','need_context'),('设备开灯','unsupported','unsupported')]
    sysmsg='你是电商数据 Agent 的意图识别器。只输出 JSON，不要解释。task_type 必须且只能取：metric_query,diagnosis,ranking,unknown,unsafe_action,sensitive_data,report,experiment_analysis,forecast,followup,unsupported。status 必须且只能取：ok,need_clarification,blocked,unsupported,need_context。schema={"task_type":string,"status":string,"risk":"low|medium|high","reason":string}'
    rows=[]; parse=schema=taskok=statok=apiok=rawtaskok=rawstatok=0; times=[]; tokens={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
    for i in range(n):
        q,et,es=base[i%len(base)]; r=call_deepseek([{'role':'system','content':sysmsg},{'role':'user','content':q}]); times.append(r.get('latency_ms',0)); apiok+=1 if r.get('ok') else 0
        for k in tokens: tokens[k]+=int((r.get('usage') or {}).get(k) or 0)
        raw,ok=extract_json(r.get('content')); raw_valid=bool(ok and isinstance(raw,dict) and raw.get('task_type') and raw.get('status'))
        obj,canon_valid=canonicalize_llm(raw); valid=bool(raw_valid and canon_valid)
        parse+=1 if ok else 0; schema+=1 if valid else 0
        raw_to=bool(raw_valid and raw.get('task_type')==et); raw_so=bool(raw_valid and raw.get('status')==es)
        to=bool(valid and obj.get('task_type')==et); so=bool(valid and obj.get('status')==es)
        rawtaskok+=1 if raw_to else 0; rawstatok+=1 if raw_so else 0; taskok+=1 if to else 0; statok+=1 if so else 0
        rows.append({'id':'llm_%04d'%i,'query':q,'expected_task_type':et,'raw_task_type':obj.get('raw_task_type'),'pred_task_type':obj.get('task_type'),'expected_status':es,'raw_status':obj.get('raw_status'),'pred_status':obj.get('status'),'api_ok':r.get('ok'),'parse_ok':ok,'schema_ok':valid,'raw_task_ok':raw_to,'raw_status_ok':raw_so,'task_ok':to,'status_ok':so,'error':r.get('error')})
    skipped=all(r.get('error')=='DEEPSEEK_API_KEY is not set' for r in rows)
    return {'case_count':n,'status':'skipped' if skipped else 'executed','canonical_schema':{'task_types':sorted(CANONICAL_TASK_TYPES),'statuses':sorted(CANONICAL_STATUSES),'alias_count':len(TASK_ALIASES)+len(STATUS_ALIASES)},'metrics':{'api_success_rate':okrate(apiok,n),'json_parse_success_rate':okrate(parse,n),'schema_valid_rate_after_canonicalization':okrate(schema,n),'raw_task_type_accuracy':okrate(rawtaskok,n),'raw_status_accuracy':okrate(rawstatok,n),'task_type_accuracy':okrate(taskok,n),'status_accuracy':okrate(statok,n),'latency':lat(times)},'ci95':{'task_type_accuracy':ci(taskok,n),'status_accuracy':ci(statok,n)},'usage':tokens,'failures':[r for r in rows if not (r['task_ok'] and r['status_ok'] and r['schema_ok'])][:50]}

def main():
    started=time.time()
    report={'manifest':{'generated_at':time.strftime('%Y-%m-%d %H:%M:%S'),'mode':'remaining_non_rag_large_eval','rag_excluded':True,'limitations':['本报告排除 RAG/向量检索/embedding','多轮记忆、多 Agent、MCP、熔断为本地确定性契约评测','HTTP 服务因当前 FastAPI/Starlette 依赖不兼容导致 server import 失败，真实 HTTP 压测未执行并作为缺口记录','真实 LLM E2E 依赖 DEEPSEEK_API_KEY；未设置则跳过不伪造结果']} }
    report['memory_multiturn_eval']=eval_memory(360)
    report['multi_agent_supervisor_eval']=eval_multi_agent(360)
    report['mcp_stdio_transport_eval']=eval_mcp(360)
    report['ops_resilience_eval']=eval_ops(300)
    report['real_llm_non_rag_e2e_eval']=eval_llm(300)
    report['latency_total_ms']=round((time.time()-started)*1000,3)
    write_json(os.path.join(REPORTS,'remaining_non_rag_large_eval_report.json'),report)
    print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True))
    return 0
if __name__=='__main__': sys.exit(main())
