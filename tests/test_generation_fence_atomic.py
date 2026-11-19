"""Native SQLite regression for generation fencing, including forced conflict."""
from src.effect_outbox import EffectCoordinator
from benchmarks.generation_fence_race import main as race
from concurrent.futures import ThreadPoolExecutor
import pytest


def test_two_interleavings_forced_native_sqlite():
    results=race()
    assert results['legacy']['final']==2  # baseline violated monotonicity
    assert results['fixed']['final']==3
    assert results['fixed']['high'].get('ok')
    assert 'stale' in results['fixed']['low'].get('error','')


def test_concurrent_epochs_never_regress(tmp_path):
    c=EffectCoordinator(tmp_path/'epochs.db')
    c.change_generation('workflow',1)
    epochs=list(range(2,42))
    with ThreadPoolExecutor(max_workers=16) as executor:
        results=list(executor.map(lambda e:try_epoch(c,e), epochs))
    with c.db() as db:
        final=db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',('workflow',)).fetchone()[0]
    assert final==max(epochs)
    assert 41 in results
    with pytest.raises(ValueError):c.change_generation('workflow',3)


def try_epoch(c,e):
    try:c.change_generation('workflow',e);return e
    except ValueError:return None


def test_atomic_fence_preserves_old_effect_inaccessibility(tmp_path):
    c=EffectCoordinator(tmp_path/'fencing.db')
    c.change_generation('recovery',2)
    assert c.enqueue('recovery',2,'effect-old',{'v':2})
    c.change_generation('recovery',3)
    with pytest.raises(ValueError):c.claim('effect-old')
    with pytest.raises(ValueError):c.change_generation('recovery',2)
    assert c.enqueue('recovery',3,'effect-new',{'v':3})
    assert c.claim('effect-new')
