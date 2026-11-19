import tempfile
from pathlib import Path
from src.workflow_history import WorkflowHistory
import pytest

def test_crash_restart_replay_and_idempotency():
    with tempfile.TemporaryDirectory() as tmp:
        f=Path(tmp)/'events.db';w=WorkflowHistory(f)
        assert w.append('a',1,'TAKEOVER','take')
        assert w.append('a',1,'SCHEDULE','s')
        assert not w.append('a',1,'SCHEDULE','s')
        # simulate controller restart by dropping the object
        reloaded=WorkflowHistory(f)
        assert reloaded.replay('a').scheduled==1
        assert reloaded.append('a',1,'COMPLETE','c')
        assert reloaded.replay('a').completed==1

def test_generation_fence_and_collision():
    with tempfile.TemporaryDirectory() as tmp:
        w=WorkflowHistory(Path(tmp)/'db');w.append('b',1,'TAKEOVER','t1')
        w.append('b',2,'TAKEOVER','t2')
        with pytest.raises(ValueError):w.append('b',1,'SCHEDULE','stale')
        with pytest.raises(ValueError):w.append('b',2,'COMPLETE','bad')
        with pytest.raises(ValueError):w.append('elsewhere',2,'TAKEOVER','t2')
