"""Daily lineup optimisation.

Each day, every rostered player with a game has an expected value
(projected FP x probability of playing) and a set of lineup slots he may fill.
Because a player's value does not depend on *which* slot he fills, the sets of
players that can start together form a transversal matroid, so adding players
greedily by value (keeping a set only if it still has a perfect matching into
the slots, checked with Kuhn's augmenting paths) gives the exact maximum-value
lineup (Edmonds 1971; Kuhn 1955). It is equivalent to solving the assignment
problem with the Hungarian method but much cheaper for 13 x 10 rosters.
"""

from __future__ import annotations

from .constants import NON_STARTING_SLOTS, SLOT_ELIGIBILITY


class SlotModel:
    def __init__(self, roster_slots: dict[str, int]):
        self.slots: list[str] = []
        for code, n in roster_slots.items():
            if code in NON_STARTING_SLOTS:
                continue
            self.slots.extend([code] * int(n))
        # Most restrictive slots first, so tie-breaking keeps flexible slots open.
        self.slots.sort(key=lambda c: len(SLOT_ELIGIBILITY.get(c, ())))
        self.n = len(self.slots)

    def mask_for(self, eligible_slots: list[str], positions: list[str]) -> int:
        """Bitmask of slot instances a player may fill."""
        codes = set(eligible_slots)
        if not codes:
            codes = {c for c, pos in SLOT_ELIGIBILITY.items() if set(positions) & pos}
        mask = 0
        for j, code in enumerate(self.slots):
            if code in codes:
                mask |= 1 << j
        return mask


def solve_day(cands: list[tuple[int, float, int]], n_slots: int) -> dict[int, int]:
    """Maximum-value assignment of candidates to slots.

    cands: (player_index, value, slot_mask). Returns player_index -> slot index.
    """
    slot_owner = [-1] * n_slots
    assigned: dict[int, int] = {}
    order = sorted(cands, key=lambda c: -c[1])
    masks = {c[0]: c[2] for c in order}

    def augment(pi: int, seen: list[bool]) -> bool:
        m = masks[pi]
        j = 0
        while m:
            if m & 1 and not seen[j]:
                seen[j] = True
                owner = slot_owner[j]
                if owner == -1 or augment(owner, seen):
                    slot_owner[j] = pi
                    assigned[pi] = j
                    return True
            m >>= 1
            j += 1
        return False

    filled = 0
    for pi, value, mask in order:
        if value <= 0 or filled >= n_slots:
            break
        if augment(pi, [False] * n_slots):
            filled += 1
    return assigned
