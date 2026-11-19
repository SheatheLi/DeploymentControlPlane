"""Local 64-claimant atomic lease vs existing unleased admission competition."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from pathlib import Path
import json
from src.effect_outbox import EffectCoordinator
from src.lease_claim import LeasedCoordinator

def run():
    report={}
    with TemporaryDirectory() as temp:
        for label,coordinator in [('legacy_unleased',EffectCoordinator),('fenced_lease',LeasedCoordinator)]:
            c=coordinator(Path(temp)/f'{label}.db')
            c.change_generation('service',1);c.enqueue('service',1,'effect-1',{'release':'v1'})
            def claim(_):
                try:return c.claim('effect-1') if label=='legacy_unleased' else c.claim_leased('effect-1',30)
                except Exception as exc:return ('ERROR',str(exc))
            with ThreadPoolExecutor(max_workers=12) as pool:claims=list(pool.map(claim,range(64)))
            report[label]={'nonempty_leases':sum(isinstance(x,str) and bool(x) for x in claims),
                           'failed_claims':sum(isinstance(x,tuple) for x in claims),
                           'db_attempts':c.state('effect-1')['attempts']}
    report['scope']='Local SQLite one-workflow fixed workload; real external receiver requires idempotency.'
    return report
if __name__=='__main__': print(json.dumps(run(),indent=2))
