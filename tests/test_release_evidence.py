import pytest
from src.release_evidence import Phase, classify_release


def base(**changes):
    d=dict(service='demo',generation=3,
           desired={'service':'demo','version':'v3','generation':3},
           job={'service':'demo','version':'v3','generation':3,'status':'SUCCEEDED'},
           active={'version':'v3','generation':3,'active_endpoint':'http://127.0.0.1:1234'},
           effect={'effect_id':'release:demo:3','service':'demo','generation':3,'state':'ACKED'},
           probe={'status':'healthy','version':'v3','launch_nonce':'uuid3','endpoint':'http://127.0.0.1:1234'}, expected_nonce='uuid3', expected_endpoint='http://127.0.0.1:1234', observed_at=100.0, now=102.0)
    d.update(changes)
    return classify_release(**d)


def test_complete_is_only_local_observation():
    v=base()
    assert v.phase==Phase.PROBE_OBSERVED
    assert any('No inference' in x for x in v.limitations)

@pytest.mark.parametrize('key,value,expected',[
    ('desired',None,Phase.UNKNOWN),('job',None,Phase.REQUESTED),
    ('active',None,Phase.CLAIMED),('effect',None,Phase.PROMOTED_LOCAL),
    ('probe',None,Phase.EFFECT_ACKED),
    ('expected_nonce',None,Phase.EFFECT_ACKED),
])
def test_missing_evidence_cannot_elevate(key,value,expected):
    assert base(**{key:value}).phase==expected

@pytest.mark.parametrize('key,value',[
    ('generation',0),('generation',True),('generation',-1),('generation',1.0),
    ('service',''),('service','invalid-service'),
])
def test_invalid_request(key,value):
    with pytest.raises(ValueError):base(**{key:value})

@pytest.mark.parametrize('change',[
    {'service':'other','version':'v3','generation':3},
    {'service':'demo','version':'v3','generation':4},
    {'service':'demo','version':'','generation':3},
])
def test_desired_mismatch(change):
    assert base(desired=change).phase==Phase.CONFLICT

@pytest.mark.parametrize('job',[
    {'service':'else','version':'v3','generation':3,'status':'SUCCEEDED'},
    {'service':'demo','version':'v3','generation':2,'status':'SUCCEEDED'},
    {'service':'demo','version':'v2','generation':3,'status':'SUCCEEDED'},
    {'service':'demo','version':'v3','generation':3,'status':'PENDING'},
])
def test_bad_job_prevents_promote(job):
    assert base(job=job).phase==Phase.REQUESTED

@pytest.mark.parametrize('active',[
    {'generation':3,'version':'v2'},{'generation':2,'version':'v3'},
])
def test_wrong_active(active):
    assert base(active=active).phase==Phase.CLAIMED

@pytest.mark.parametrize('effect',[
    {'effect_id':'release:other:3','service':'demo','generation':3,'state':'ACKED'},
    {'effect_id':'release:demo:3','service':'other','generation':3,'state':'ACKED'},
    {'effect_id':'release:demo:3','service':'demo','generation':2,'state':'ACKED'},
    {'effect_id':'release:demo:3','service':'demo','generation':3,'state':'PENDING'},
])
def test_wrong_effect(effect):
    assert base(effect=effect).phase==Phase.PROMOTED_LOCAL

@pytest.mark.parametrize('probe',[
    {'status':'unhealthy','version':'v3','launch_nonce':'uuid3','endpoint':'http://127.0.0.1:1234'},
    {'status':'healthy','version':'v2','launch_nonce':'uuid3','endpoint':'http://127.0.0.1:1234'},
    {'status':'healthy','version':'v3','launch_nonce':'uuid2','endpoint':'http://127.0.0.1:1234'},
])
def test_wrong_probe(probe):
    assert base(probe=probe).phase==Phase.EFFECT_ACKED

def test_do_not_accept_probe_with_no_job():
    assert base(job=None).phase==Phase.REQUESTED

def test_pg_fields_supported():
    assert base(desired={'name':'demo','desired_version':'v3','generation':3},
                active={'active_version':'v3','active_generation':3,'active_endpoint':'http://127.0.0.1:1234'},
                job={'state':'PROMOTED','version':'v3','service':'demo','generation':3}).phase==Phase.PROBE_OBSERVED

@pytest.mark.parametrize('delta',[-1.0,5.1,40.0])
def test_stale_future_or_expired_probe_fails(delta):
    assert base(now=100+delta).phase==Phase.EFFECT_ACKED

@pytest.mark.parametrize('key,val',[
    ('expected_endpoint',None),('expected_endpoint','http://127.0.0.1:9'),
    ('observed_at',None),('now',None),('max_age_seconds',0),
    ('max_age_seconds',float('nan')),('now',float('inf')),
])
def test_untrustworthy_probe_metadata(key,val):
    assert base(**{key:val}).phase==Phase.EFFECT_ACKED

def test_active_record_endpoint_must_match_probe_endpoint():
    assert base(active={'version':'v3','generation':3,'active_endpoint':'http://127.0.0.1:9'}).phase==Phase.EFFECT_ACKED

def test_missing_active_endpoint_prevents_probe_claim():
    assert base(active={'version':'v3','generation':3}).phase==Phase.EFFECT_ACKED

def test_conflicting_service_aliases_rejected():
    assert base(desired={'service':'bad','name':'demo','version':'v3','generation':3}).phase==Phase.CONFLICT

def test_local_port_can_correlate_with_probe_url():
    assert base(active={'version':'v3','generation':3,'port':1234}).phase==Phase.PROBE_OBSERVED
