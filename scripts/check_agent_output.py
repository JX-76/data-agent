# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, 'src'))
from agent_facade import AgentFacade

queries = [
    u"最近7天GMV",
    u"delete from orders",
    u"帮我看看销售",
    u"按渠道看GMV",
]
for q in queries:
    f = AgentFacade()
    r = f.ask(q)
    print("query=%s | status=%s intent=%s task_type=%s metric=%s errors=%s" % (
        q, r.get('status'), r.get('intent'), r.get('task_type'), r.get('metric'),
        r.get('errors') or r.get('execution',{}).get('errors')
    ))
