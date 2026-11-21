"""Ticket-állapotgép (SPECIFICATION 8.0). A táblában nem szereplő átmenet tilos."""

STATUSES = ("NEW", "READY", "IN_PROGRESS", "WAITING_FOR_INPUT", "PENDING_APPROVAL", "REVIEW", "COMPLETED",
            "FAILED", "CANCELLED", "NEEDS_TRIAGE")
FINAL = {"COMPLETED", "CANCELLED"}

# (honnan, hová) -> engedélyezett szereplők. Szereplők: human, coordinator, worker, system.
TRANSITIONS: dict[tuple[str, str], set[str]] = {
    ("NEW", "READY"): {"coordinator", "human"},
    ("NEW", "NEEDS_TRIAGE"): {"coordinator"},
    ("NEEDS_TRIAGE", "READY"): {"human"},
    ("READY", "IN_PROGRESS"): {"worker"},
    ("IN_PROGRESS", "IN_PROGRESS"): {"worker"},
    ("IN_PROGRESS", "WAITING_FOR_INPUT"): {"worker"},
    ("IN_PROGRESS", "PENDING_APPROVAL"): {"worker"},
    ("IN_PROGRESS", "REVIEW"): {"worker"},
    ("IN_PROGRESS", "FAILED"): {"worker", "system"},
    ("WAITING_FOR_INPUT", "READY"): {"system"},
    ("PENDING_APPROVAL", "READY"): {"system"},
    ("REVIEW", "COMPLETED"): {"human"},
    ("REVIEW", "READY"): {"human"},
    ("FAILED", "READY"): {"human", "system"},
}
for _s in STATUSES:
    if _s not in FINAL:
        TRANSITIONS.setdefault((_s, "CANCELLED"), set()).add("human")
# szemantikus duplikátum: a Coordinator a még be nem sorolt ticketet összevonja egy meglévővel (SPECIFICATION 5)
TRANSITIONS[("NEW", "CANCELLED")].add("coordinator")


class TransitionError(ValueError):
    pass


def check(old: str, new: str, actor: str) -> None:
    allowed = TRANSITIONS.get((old, new))
    if not allowed:
        raise TransitionError(f"tiltott átmenet: {old} → {new}")
    if actor not in allowed:
        raise TransitionError(f"{old} → {new} átmenetet {actor} nem végezhet (csak: {', '.join(sorted(allowed))})")
