# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import json, os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)); sys.path.insert(0, os.path.join(ROOT,'src'))
from offline_evaluation_io import export_blind_review_pack
OUT=os.path.join(ROOT,'harness','review_packs','offline_ops_blind_review_v1.csv')
cases=[
 {'case_id':'review_seed_001','query':'最近7天GMV下降的主因是什么？','answer':'请基于渠道、品类和转化率证据分析主因。'},
 {'case_id':'review_seed_002','query':'ROI变差后应该优先做什么？','answer':'先核验投放成本、转化和归因范围，再给出草案建议。'},
 {'case_id':'review_seed_003','query':'本月华东GMV表现如何？','answer':'请按明确口径和当前权限范围解释结果。'},
]
if __name__=='__main__': print(json.dumps(export_blind_review_pack(cases,OUT),ensure_ascii=False))
