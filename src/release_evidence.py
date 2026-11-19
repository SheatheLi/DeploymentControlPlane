"""Release evidence classification, not an orchestrator or a production health attestation.

The classifier is deliberately pure: callers supply independent observations.
It never turns DB bookkeeping into proof of an external effect or physical rollout.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import math
from typing import Mapping, Any


class Phase(str, Enum):
    UNKNOWN = "UNKNOWN"
    REQUESTED = "REQUESTED"
    CLAIMED = "CLAIMED"
    PROMOTED_LOCAL = "PROMOTED_LOCAL"
    EFFECT_ACKED = "EFFECT_ACKED"
    PROBE_OBSERVED = "PROBE_OBSERVED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class EvidenceVerdict:
    phase: Phase
    service: str
    generation: int
    assertions: tuple[str, ...]
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]


def _valid_generation(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def _field(row: Mapping[str, Any] | None, name: str):
    return None if row is None else row.get(name)


def classify_release(
    *, service: str, generation: int,
    desired: Mapping[str, Any] | None,
    active: Mapping[str, Any] | None = None,
    job: Mapping[str, Any] | None = None,
    effect: Mapping[str, Any] | None = None,
    probe: Mapping[str, Any] | None = None,
    expected_nonce: str | None = None,
    expected_endpoint: str | None = None,
    observed_at: float | None = None,
    now: float | None = None,
    max_age_seconds: float = 5,
) -> EvidenceVerdict:
    """Classify the strongest *local* stage demonstrable from the supplied evidence.

    `desired`, `active`, `job`, `effect` are persisted observations (not claims).
    `probe` must be independently obtained from the exact target endpoint.
    A probe with an uncorrelated nonce cannot raise the stage. No transition
    here asserts that a real production deployment succeeded.
    """
    if not service or not service.isidentifier() or not _valid_generation(generation):
        raise ValueError("valid service and positive integer generation required")
    facts: list[str] = []
    blockers: list[str] = []
    limits = ["Only the supplied database and local probe are examined.",
              "No inference of real orchestration, physical actuation, user success or production SLO."]
    if desired is None:
        return EvidenceVerdict(Phase.UNKNOWN, service, generation, (),
                               ("desired generation record missing",), tuple(limits))
    if (('service' in desired and desired['service'] != service) or ('name' in desired and desired['name'] != service)):
        return EvidenceVerdict(Phase.CONFLICT, service, generation, (),
                               ("desired belongs to another service",), tuple(limits))
    actual_gen = desired.get('generation')
    if not _valid_generation(actual_gen):
        return EvidenceVerdict(Phase.CONFLICT, service, generation, (),
                               ("desired generation invalid",), tuple(limits))
    if actual_gen != generation:
        return EvidenceVerdict(Phase.CONFLICT, service, generation, (),
                               ("requested generation is not current desired generation",), tuple(limits))
    version = desired.get('version', desired.get('desired_version'))
    if not isinstance(version, str) or not version:
        return EvidenceVerdict(Phase.CONFLICT, service, generation, (),
                               ("desired version missing",), tuple(limits))
    facts.append("durable desired record matches requested service/generation/version")
    phase = Phase.REQUESTED

    if job is not None:
        if job.get('service') not in (None, service) or job.get('generation') != generation:
            blockers.append("job service/generation mismatch")
        elif job.get('version') not in (None, version):
            blockers.append("job version mismatch")
        elif job.get('state', job.get('status')) in ('CLAIMED', 'PROMOTED', 'SUCCEEDED'):
            facts.append("release job persisted as claimed or later")
            phase = Phase.CLAIMED
        else:
            blockers.append("job not yet claimed")
    else:
        blockers.append("release job not independently inspected")

    if active is not None:
        ag = active.get('generation', active.get('active_generation'))
        av = active.get('version', active.get('active_version'))
        if ag != generation or av != version:
            blockers.append("active record does not match desired generation/version")
        elif phase == Phase.CLAIMED and job is not None and job.get('state', job.get('status')) in ('PROMOTED', 'SUCCEEDED'):
            facts.append("matching active state and promoted job are persisted")
            phase = Phase.PROMOTED_LOCAL
        else:
            blockers.append("matching active state without confirmed job promotion")
    else:
        blockers.append("active record missing")

    if effect is not None:
        eid = effect.get('effect_id')
        if phase != Phase.PROMOTED_LOCAL:
            blockers.append("outbox state cannot raise an unpromoted release")
        elif effect.get('generation') != generation:
            blockers.append("outbox generation mismatch")
        elif effect.get('service') not in (None, service):
            blockers.append("outbox service mismatch")
        elif eid not in (None, f"release:{service}:{generation}"):
            blockers.append("outbox effect identity mismatch")
        elif effect.get('state') == 'ACKED':
            facts.append("outbox acknowledgement persisted; receiver semantics are separate")
            phase = Phase.EFFECT_ACKED
        else:
            blockers.append("outbox not ACKED")
    else:
        blockers.append("outbox record missing")

    if probe is not None:
        recorded_endpoint = None if active is None else active.get('active_endpoint')
        if recorded_endpoint is None and active is not None and 'port' in active:
            recorded_endpoint=f"http://127.0.0.1:{active['port']}"
        if phase != Phase.EFFECT_ACKED:
            blockers.append("probe cannot promote a release lacking persistence chain")
        elif not expected_nonce:
            blockers.append("probe nonce not supplied: response cannot be correlated")
        elif not expected_endpoint or probe.get('endpoint') != expected_endpoint or recorded_endpoint != expected_endpoint:
            blockers.append("probe endpoint is absent or not identity-bound")
        elif (observed_at is None or now is None or not all(isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (observed_at,now,max_age_seconds))
              or max_age_seconds <= 0 or not 0 <= now-observed_at <= max_age_seconds):
            blockers.append("probe observation is missing, stale, or from the future")
        elif probe.get('launch_nonce') != expected_nonce:
            blockers.append("probe nonce mismatch")
        elif probe.get('version') != version or probe.get('status') != 'healthy':
            blockers.append("probe version or health mismatch")
        else:
            facts.append("independently supplied identity-bound loopback probe matched")
            phase = Phase.PROBE_OBSERVED
    return EvidenceVerdict(phase, service, generation,
                           tuple(facts), tuple(blockers), tuple(limits))
