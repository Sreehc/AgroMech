from __future__ import annotations


def is_valid_chunk_source_locator(source_locator: dict[str, object] | None) -> bool:
    return bool(source_locator and source_locator.get("type"))


def is_referenceable_chunk(content: str | None, source_locator: dict[str, object] | None) -> bool:
    return bool(content and content.strip() and is_valid_chunk_source_locator(source_locator))
