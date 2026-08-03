import os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'src'))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from experiment_analysis import analyze_experiment
from experiment_registry import ExperimentDefinition

def rows(a,b,metric='conversion'):
 return [{'uid':'a%s'%i,'variant':'A',metric:v} for i,v in enumerate(a)]+[{'uid':'b%s'%i,'variant':'B',metric:v} for i,v in enumerate(b)]
def d(kind='binary_conversion',metric='conversion',randomized=True): return ExperimentDefinition('e',metric,kind,'uid',minimum_group_size=5,randomized=randomized)
def test_conversion_significant(): assert analyze_experiment(d(),rows([0]*20,[1]*20))['analysis']['summary_facts']['significance']=='significant'
def test_conversion_not_significant(): assert analyze_experiment(d(),rows([0,1]*10,[0,1]*10))['analysis']['summary_facts']['significance']=='not_significant'
def test_conversion_has_ci_and_lifts():
 f=analyze_experiment(d(),rows([0]*10,[1]*10))['analysis']['summary_facts']; assert f['confidence_interval'] and f['absolute_lift']==1

def test_continuous_significant():
 f=analyze_experiment(d('continuous','gmv'),rows([1]*20,[10]*20,'gmv'))['analysis']['summary_facts']; assert f['method'].startswith('welch') and f['p_value']<.05

def test_continuous_not_significant(): assert analyze_experiment(d('continuous','gmv'),rows([10]*10,[10]*10,'gmv'))['analysis']['summary_facts']['significance']=='not_significant'
def test_small_sample_needs_clarification(): assert analyze_experiment(d(),rows([0]*2,[1]*2))['status']=='need_more_data'
def test_observational_suppresses_significance():
 f=analyze_experiment(d(randomized=False),rows([0]*10,[1]*10))['analysis']['summary_facts']; assert f['p_value'] is None and f['significance'] is None

