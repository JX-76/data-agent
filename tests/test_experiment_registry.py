import os, sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'src'))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from experiment_registry import ExperimentDefinition, ExperimentRegistry

def test_definition_serializes(): assert ExperimentDefinition('e','conversion','binary_conversion','uid').to_dict()['experiment_id']=='e'
def test_registry_resolves():
 r=ExperimentRegistry(); r.register(ExperimentDefinition('e','conversion','binary_conversion','uid')); assert r.resolve('e')['ok']
def test_unknown_is_not_ok(): assert not ExperimentRegistry().resolve('x')['ok']
def test_tenant_scope():
 r=ExperimentRegistry(); r.register(ExperimentDefinition('e','conversion','binary_conversion','uid',tenant_id='t1')); assert 'experiment_tenant_mismatch' in r.resolve('e','t2')['errors']
def test_missing_randomization_is_invalid():
 r=ExperimentRegistry(); r.register(ExperimentDefinition('e','conversion','binary_conversion','')); assert not r.resolve('e')['ok']
def test_unsupported_kind_is_invalid():
 r=ExperimentRegistry(); r.register(ExperimentDefinition('e','x','bad','uid')); assert not r.resolve('e')['ok']

