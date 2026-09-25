"""
api_Controls/delegation_ledger.py
STEP 3: ACP-5 - Delegation Ledger (Fig. 3 "Delegation Ledger / Expiry &
revocation"). Implements delegated authority as an explicit, queryable,
revocable grant (Section 3.2), in place of the framework's own static
`allowed_permissions: Set[str]` passed once to CExecutionEnvironment for
the whole run.

Each DelegationGrant records: (i) a grantor and grantee; (ii) a
permitted scope; (iii) an expiry condition (elapsed time via
`ttl_seconds`, or invocation count via `max_invocations`); and (iv) a
parent grant id, enabling transitive (cascade-on-parent-revoke)
revocation - all four properties named in Section 4.2's description of
the ledger.

Honesty-critical note (already carried in Paper 3 drafts): the
delegation primitive has no real counterpart in the current prototype.
`bootstrap_from_allowed_permissions()` below performs exactly the
reinterpretation Section 4.2 proposes - the existing
`allowed_permissions` set becomes a single, unscoped, non-expiring
operator-to-orchestrator grant - and nothing more; it does not retrofit
real expiry or scoping onto the framework's own permission model.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set


@dataclass
class DelegationGrant:
    grantor: str
    grantee: str
    scope: Set[str]                              # tools / permissions / operations covered
    grant_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: Optional[str] = None
    issued_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None        # elapsed-time expiry
    max_invocations: Optional[int] = None        # invocation-count expiry
    invocation_count: int = 0
    revoked: bool = False
    revoked_reason: Optional[str] = None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        if self.expires_at is not None and now >= self.expires_at:
            return True
        if self.max_invocations is not None and self.invocation_count >= self.max_invocations:
            return True
        return False


class DelegationLedger:
    """Issue / revoke / is_valid over DelegationGrant records, with
    cascade-on-parent-revoke transitive revocation (Section 3.2(2))."""

    def __init__(self, bus=None):
        self._grants: Dict[str, DelegationGrant] = {}
        self._children: Dict[str, List[str]] = {}
        self.bus = bus

    def issue(self, grantor: str, grantee: str, scope: Set[str],
              parent_id: Optional[str] = None,
              ttl_seconds: Optional[float] = None,
              max_invocations: Optional[int] = None) -> DelegationGrant:
        expires_at = (datetime.now() + timedelta(seconds=ttl_seconds)) if ttl_seconds else None
        grant = DelegationGrant(grantor=grantor, grantee=grantee, scope=set(scope),
                                 parent_id=parent_id, expires_at=expires_at,
                                 max_invocations=max_invocations)
        self._grants[grant.grant_id] = grant
        if parent_id:
            self._children.setdefault(parent_id, []).append(grant.grant_id)
        if self.bus:
            self.bus.publish("delegation_issued", acp="ACP-5",
                              grant_id=grant.grant_id, grantor=grantor, grantee=grantee,
                              scope=sorted(scope), parent_id=parent_id,
                              depth=self._depth(grant.grant_id))
        return grant

    def _depth(self, grant_id: str) -> int:
        depth, current = 0, self._grants.get(grant_id)
        while current and current.parent_id:
            depth += 1
            current = self._grants.get(current.parent_id)
        return depth

    def revoke(self, grant_id: str, reason: str = "") -> List[str]:
        """Revokes grant_id and cascades to every transitive child. Returns
        the list of grant_ids actually revoked (empty if grant_id is
        unknown or already revoked)."""
        if grant_id not in self._grants:
            return []
        revoked_ids: List[str] = []
        stack = [grant_id]
        while stack:
            gid = stack.pop()
            grant = self._grants.get(gid)
            if grant is None or grant.revoked:
                continue
            grant.revoked = True
            grant.revoked_reason = reason
            revoked_ids.append(gid)
            if self.bus:
                self.bus.publish("delegation_revoked", acp="ACP-5", grant_id=gid, reason=reason,
                                  cascaded=(gid != grant_id))
            stack.extend(self._children.get(gid, []))
        return revoked_ids

    def is_valid(self, grant_id: str, required_scope: Optional[str] = None,
                 now: Optional[datetime] = None) -> bool:
        grant = self._grants.get(grant_id)
        if grant is None or grant.revoked:
            return False
        if grant.is_expired(now):
            return False
        if required_scope is not None and required_scope not in grant.scope:
            return False
        return True

    def record_invocation(self, grant_id: str) -> None:
        grant = self._grants.get(grant_id)
        if grant:
            grant.invocation_count += 1

    def get(self, grant_id: str) -> Optional[DelegationGrant]:
        return self._grants.get(grant_id)

    def active_grants(self) -> List[DelegationGrant]:
        return [g for g in self._grants.values() if not g.revoked and not g.is_expired()]

    # -- reinterpretation of the framework's own permission model -----------
    def bootstrap_from_allowed_permissions(self, allowed_permissions: Set[str],
                                            grantor: str = "operator",
                                            grantee: str = "orchestrator") -> DelegationGrant:
        """
        Reinterprets CAgenticOrchestrator's constructor-time
        `allowed_permissions: Set[str]` as a degenerate, unscoped,
        non-expiring delegation grant. Does not change how
        CExecutionEnvironment enforces permissions on its own -
        controlled_execution.py checks BOTH the original
        allowed_permissions set (P(a)) AND this grant's validity (G(a,t)).
        """
        return self.issue(grantor=grantor, grantee=grantee, scope=set(allowed_permissions))
