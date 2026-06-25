from sqlalchemy import create_engine

from agromech_api.db.models import metadata
from agromech_api.model_aliases import (
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    add_llm_candidate,
    add_manual_alias,
    list_candidates,
    normalize_model,
    promote_candidate,
    resolve_model,
    resolve_models,
)
from agromech_api.query_understanding import parse_query


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def test_normalize_model_collapses_case_space_hyphen_and_underscore() -> None:
    assert normalize_model("M-7040") == "M7040"
    assert normalize_model("m 7040") == "M7040"
    assert normalize_model("M_7040") == "M7040"
    assert normalize_model("  m7040 ") == "M7040"
    assert normalize_model("") == ""


def test_resolve_model_with_empty_table_returns_rule_normalized_form(tmp_path) -> None:
    engine = create_test_engine(tmp_path)

    resolved = resolve_model(engine, "M-7040")

    assert resolved.canonical == "M7040"
    assert resolved.matched_alias is False
    assert resolved.source is None


def test_manual_alias_maps_to_canonical_model(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    add_manual_alias(engine, alias="M7040SU", canonical_model="M7040")

    resolved = resolve_model(engine, "m7040su")

    assert resolved.canonical == "M7040"
    assert resolved.matched_alias is True
    assert resolved.source == "manual"


def test_llm_candidate_is_not_used_for_resolution_until_promoted(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    add_llm_candidate(engine, alias="M7040X", canonical_model="M7040", confidence=0.7)

    # Candidate must not affect resolution.
    resolved = resolve_model(engine, "M7040X")
    assert resolved.matched_alias is False
    assert resolved.canonical == "M7040X"

    candidates = list_candidates(engine)
    assert len(candidates) == 1
    assert candidates[0]["normalized_alias"] == "M7040X"
    assert candidates[0]["source"] == "llm"

    # After promotion the alias resolves and is no longer pending.
    assert promote_candidate(engine, "M7040X") is True
    resolved_after = resolve_model(engine, "M7040X")
    assert resolved_after.matched_alias is True
    assert resolved_after.canonical == "M7040"
    assert list_candidates(engine) == []


def test_promote_candidate_returns_false_when_no_candidate(tmp_path) -> None:
    engine = create_test_engine(tmp_path)

    assert promote_candidate(engine, "unknown") is False


def test_resolve_models_dedupes_and_preserves_order(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    add_manual_alias(engine, alias="M7040SU", canonical_model="M7040")

    resolved = resolve_models(engine, ["M-7040", "M7040SU", "L3901"])

    assert resolved == ["M7040", "L3901"]


def test_similar_models_are_not_auto_merged(tmp_path) -> None:
    engine = create_test_engine(tmp_path)

    # Two distinct models with no alias linking them must stay distinct.
    assert resolve_model(engine, "M7040").canonical == "M7040"
    assert resolve_model(engine, "M7060").canonical == "M7060"


def test_parse_query_applies_manual_alias_when_engine_provided(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    add_manual_alias(engine, alias="M7040SU", canonical_model="M7040")

    parsed = parse_query("Kubota M7040SU 液压无力", engine=engine)

    assert parsed.filters["model"] == "M7040"


def test_parse_query_without_engine_is_unchanged(tmp_path) -> None:
    # Backward-compatible: no engine means rule normalization only.
    parsed = parse_query("Kubota M-7040 液压无力")

    assert parsed.filters["model"] == "M7040"
