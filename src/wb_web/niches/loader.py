"""Load and validate niche communication packs (YAML).

A pack is data, not code: tone, lexicon, forbidden claims, universal fallback
for unknown facts, few-shot examples and escalation keywords. The base pack
is always available and used when a shop selects no niches.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PACKS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class FewShot:
    review: str
    reply: str


@dataclass(frozen=True)
class NichePack:
    id: str
    name: str
    version: int
    tone: str
    lexicon: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    unknown_fact: str = ""
    few_shots: tuple[FewShot, ...] = ()
    escalation_keywords: tuple[str, ...] = ()


def _as_str_list(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"pack field '{key}' must be a list of strings")
    return tuple(value)


def load_pack(path: Path) -> NichePack:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"pack {path.name}: root must be a mapping")
    for key in ("id", "name", "version", "tone"):
        if key not in raw:
            raise ValueError(f"pack {path.name}: missing required key '{key}'")
    few_shots_raw = raw.get("few_shots") or []
    if not isinstance(few_shots_raw, list):
        raise ValueError(f"pack {path.name}: 'few_shots' must be a list")
    few_shots = tuple(
        FewShot(review=str(fs.get("review", "")).strip(), reply=str(fs.get("reply", "")).strip())
        for fs in few_shots_raw
        if isinstance(fs, dict) and fs.get("review") and fs.get("reply")
    )
    return NichePack(
        id=str(raw["id"]),
        name=str(raw["name"]),
        version=int(raw["version"]),
        tone=str(raw["tone"]),
        lexicon=_as_str_list(raw.get("lexicon"), "lexicon"),
        forbidden_claims=_as_str_list(raw.get("forbidden_claims"), "forbidden_claims"),
        unknown_fact=str(raw.get("unknown_fact", "")).strip(),
        few_shots=few_shots,
        escalation_keywords=_as_str_list(raw.get("escalation_keywords"), "escalation_keywords"),
    )


def load_packs(directory: Path = PACKS_DIR) -> dict[str, NichePack]:
    packs: dict[str, NichePack] = {}
    for path in sorted(directory.glob("*.yaml")):
        pack = load_pack(path)
        packs[pack.id] = pack
    if "base" not in packs:
        raise ValueError("niche packs directory must contain base.yaml")
    return packs


def get_packs_for_shop(niche_ids: list[str] | None) -> list[NichePack]:
    """Resolve shop niche selection; empty/unknown selection -> base pack only."""
    packs = load_packs()
    if not niche_ids:
        return [packs["base"]]
    resolved = [packs[nid] for nid in niche_ids if nid in packs]
    return resolved or [packs["base"]]


def needs_manual_review(text: str, packs: list[NichePack]) -> bool:
    lowered = text.lower()
    return any(any(kw in lowered for kw in pack.escalation_keywords) for pack in packs)
