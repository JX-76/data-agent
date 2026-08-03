import os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'src'))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from experiment_analysis import analyze_experiment
from experiment_registry import ExperimentDefinition

def definition(): return ExperimentDefinition('e','conversion','binary_conversion','uid',tenant_id='t',minimum_group_size=2)
def base(): return [{'uid':'1','variant':'A','conversion':0},{'uid':'2','variant':'A','conversion':0},{'uid':'3','variant':'B','conversion':1},{'uid':'4','variant':'B','conversion':1}]
def test_tenant_blocked(): assert analyze_experiment(definition(),base(),'other')['status']=='blocked'
def test_contamination_needs_clarification(): assert analyze_experiment(definition(),base()+[{'uid':'1','variant':'B','conversion':1}],'t')['status']=='need_clarification'
def test_missing_metric_needs_clarification(): assert analyze_experiment(definition(),[{'uid':'1','variant':'A'}],'t')['status']=='need_clarification'
def test_no_raw_user_data_in_output():
 r=analyze_experiment(definition(),base(),'t'); assert 'uid' not in repr(r['results'])
def test_guardrail_is_aggregate_output():
 x=ExperimentDefinition('e','conversion','binary_conversion','uid',minimum_group_size=2,guardrails=['refund']); assert analyze_experiment(x,base())['results'][0]['guardrail']==['refund']
def test_duplicate_is_caveated(): assert analyze_experiment(definition(),base()+[{'uid':'2','variant':'A','conversion':0}],'t')['caveats']

