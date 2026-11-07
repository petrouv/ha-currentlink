"""Тесты валидации и 100% паритета файлов локализации strings.json, en.json, ru.json."""

from __future__ import annotations

import json
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "currentlink"


def _load_json(filename: str) -> dict:
    """Загружает JSON-файл локализации."""
    path = COMPONENT_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_keys(d: dict, prefix: str = "") -> set[str]:
    """Рекурсивно извлекает плоское множество ключей."""
    keys = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.update(_flatten_keys(v, full))
        else:
            keys.add(full)
    return keys


def test_strings_matches_english() -> None:
    """Проверяет идентичность strings.json и translations/en.json."""
    strings = _load_json("strings.json")
    en = _load_json("translations/en.json")
    assert strings == en


def test_key_parity_english_and_russian() -> None:
    """Проверяет 100% 1:1 соответствие ключей между en.json и ru.json."""
    en_keys = _flatten_keys(_load_json("translations/en.json"))
    ru_keys = _flatten_keys(_load_json("translations/ru.json"))

    missing_in_ru = en_keys - ru_keys
    extra_in_ru = ru_keys - en_keys

    assert missing_in_ru == set(), f"Keys missing in ru.json: {missing_in_ru}"
    assert extra_in_ru == set(), f"Extra keys in ru.json: {extra_in_ru}"


def test_all_entity_platforms_have_names() -> None:
    """Проверяет, что у каждой сущности в translations определено непустое имя."""
    for filename in ("strings.json", "translations/en.json", "translations/ru.json"):
        data = _load_json(filename)
        entity_section = data.get("entity", {})
        for platform, entities in entity_section.items():
            for entity_key, attrs in entities.items():
                assert "name" in attrs, f"Missing 'name' in {filename} for {platform}.{entity_key}"
                assert attrs["name"].strip() != "", (
                    f"Empty 'name' in {filename} for {platform}.{entity_key}"
                )
