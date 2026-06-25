from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update

from agromech_api.db.models import model_aliases


# Alias lifecycle status. Only ACTIVE aliases participate in resolution; LLM
# proposals land as CANDIDATE and must be promoted by a human before use, so the
# system never auto-merges similar-but-distinct models (spec 3.7).
STATUS_ACTIVE = "active"
STATUS_CANDIDATE = "candidate"

# Where an alias came from. Manual entries are authoritative; rule entries are
# deterministic normalizations; llm entries are unconfirmed candidates.
SOURCE_MANUAL = "manual"
SOURCE_RULE = "rule"
SOURCE_LLM = "llm"

_SEPARATORS_RE = re.compile(r"[\s_\-]+")


@dataclass(frozen=True)
class ResolvedModel:
    canonical: str
    matched_alias: bool
    source: str | None


def normalize_model(value: str) -> str:
    """Rule-normalize a model string.

    Handles case, surrounding whitespace, internal spaces, hyphens and
    underscores so that ``M-7040``, ``m 7040`` and ``M_7040`` all collapse to the
    same canonical key ``M7040``. Returns an empty string for blank input.
    """
    if not value:
        return ""
    return _SEPARATORS_RE.sub("", value.strip()).upper()


def resolve_model(engine: Engine, value: str) -> ResolvedModel:
    """Resolve a raw model string to its canonical form.

    Rule normalization runs first; the active alias table is then consulted. An
    empty alias table is the common case and yields the rule-normalized form, so
    callers behave identically until manual aliases are added.
    """
    key = normalize_model(value)
    if not key:
        return ResolvedModel(canonical="", matched_alias=False, source=None)

    with engine.connect() as connection:
        row = connection.execute(
            select(model_aliases)
            .where(model_aliases.c.normalized_alias == key)
            .where(model_aliases.c.status == STATUS_ACTIVE)
        ).mappings().one_or_none()

    if row is None:
        return ResolvedModel(canonical=key, matched_alias=False, source=None)
    return ResolvedModel(
        canonical=row["normalized_canonical"],
        matched_alias=True,
        source=row["source"],
    )


def resolve_models(engine: Engine, values: list[str]) -> list[str]:
    """Resolve a list of model strings, de-duplicating while preserving order."""
    resolved: list[str] = []
    for value in values:
        canonical = resolve_model(engine, value).canonical
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    return resolved


def add_manual_alias(
    engine: Engine,
    *,
    alias: str,
    canonical_model: str,
    notes: str | None = None,
) -> str:
    """Register an authoritative manual alias (immediately active)."""
    return _upsert_alias(
        engine,
        alias=alias,
        canonical_model=canonical_model,
        status=STATUS_ACTIVE,
        source=SOURCE_MANUAL,
        confidence=None,
        notes=notes,
    )


def add_llm_candidate(
    engine: Engine,
    *,
    alias: str,
    canonical_model: str,
    confidence: float,
    notes: str | None = None,
) -> str:
    """Store an LLM-proposed alias as a candidate.

    Candidates are never used for resolution until explicitly promoted, so an LLM
    suggestion can never silently rewrite a user's model into a different one.
    """
    return _upsert_alias(
        engine,
        alias=alias,
        canonical_model=canonical_model,
        status=STATUS_CANDIDATE,
        source=SOURCE_LLM,
        confidence=confidence,
        notes=notes,
    )


def promote_candidate(engine: Engine, alias: str) -> bool:
    """Promote a stored candidate to an active alias. Returns False if none found."""
    key = normalize_model(alias)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        candidate = connection.execute(
            select(model_aliases)
            .where(model_aliases.c.normalized_alias == key)
            .where(model_aliases.c.status == STATUS_CANDIDATE)
        ).mappings().one_or_none()
        if candidate is None:
            return False
        # Remove any existing active alias for the same key before promoting so
        # the (normalized_alias, status) uniqueness invariant is preserved.
        connection.execute(
            model_aliases.delete()
            .where(model_aliases.c.normalized_alias == key)
            .where(model_aliases.c.status == STATUS_ACTIVE)
        )
        connection.execute(
            update(model_aliases)
            .where(model_aliases.c.id == candidate["id"])
            .values(status=STATUS_ACTIVE, source=SOURCE_MANUAL, updated_at=now)
        )
    return True


def list_candidates(engine: Engine) -> list[dict[str, object]]:
    """Return pending candidate aliases for human review."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(model_aliases)
            .where(model_aliases.c.status == STATUS_CANDIDATE)
            .order_by(model_aliases.c.created_at)
        ).mappings().all()
    return [
        {
            "alias": row["alias"],
            "normalized_alias": row["normalized_alias"],
            "canonical_model": row["canonical_model"],
            "confidence": row["confidence"],
            "source": row["source"],
            "notes": row["notes"],
        }
        for row in rows
    ]


def _upsert_alias(
    engine: Engine,
    *,
    alias: str,
    canonical_model: str,
    status: str,
    source: str,
    confidence: float | None,
    notes: str | None,
) -> str:
    normalized_alias = normalize_model(alias)
    if not normalized_alias:
        raise ValueError("alias must not be empty")
    normalized_canonical = normalize_model(canonical_model)
    if not normalized_canonical:
        raise ValueError("canonical_model must not be empty")

    now = datetime.now(UTC)
    with engine.begin() as connection:
        existing = connection.execute(
            select(model_aliases.c.id)
            .where(model_aliases.c.normalized_alias == normalized_alias)
            .where(model_aliases.c.status == status)
        ).scalar_one_or_none()
        if existing is not None:
            connection.execute(
                update(model_aliases)
                .where(model_aliases.c.id == existing)
                .values(
                    alias=alias,
                    canonical_model=canonical_model,
                    normalized_canonical=normalized_canonical,
                    source=source,
                    confidence=confidence,
                    notes=notes,
                    updated_at=now,
                )
            )
            return existing
        alias_id = str(uuid4())
        connection.execute(
            insert(model_aliases).values(
                id=alias_id,
                alias=alias,
                normalized_alias=normalized_alias,
                canonical_model=canonical_model,
                normalized_canonical=normalized_canonical,
                status=status,
                source=source,
                confidence=confidence,
                notes=notes,
            )
        )
        return alias_id
