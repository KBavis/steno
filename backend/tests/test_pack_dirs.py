"""Rule packs from more than one place: the core packs plus an organization's own folders
(STENO_RULE_PACK_DIRS), in either layout; a name found twice is an error, not an override."""

from pathlib import Path

import pytest

from steno.config import Settings, get_settings
from steno.rule_packs.packs import load_all_packs

PACK = (
    "name: {name}\nversion: 0.1.0\nlanguage: any\ndescription: x\nenabled_when: {{always: true}}\n"
)


def _pack(folder: Path, name: str, extra: str = "") -> None:
    folder.mkdir(parents=True)
    (folder / "pack.yaml").write_text(PACK.format(name=name) + extra)
    (folder / "rules").mkdir()


def test_extra_folders_in_either_layout_are_org_packs(tmp_path: Path, monkeypatch) -> None:
    _pack(tmp_path / "flat" / "acme-xld", "acme-xld")  # packs directly in the folder
    _pack(tmp_path / "nested" / "java" / "acme-messaging", "acme-messaging")  # ecosystem → pack
    _pack(tmp_path / "nested" / "catalog" / "acme-manifest", "acme-manifest", "source: core\n")
    monkeypatch.setattr(get_settings(), "rule_pack_dirs", [tmp_path / "flat", tmp_path / "nested"])
    packs = {p.name: p for p in load_all_packs()}
    assert "steno-pack-spring-web" in packs  # the core packs are still there
    assert packs["acme-xld"].source == "org"
    assert packs["acme-messaging"].source == "org"
    assert packs["acme-manifest"].source == "core"  # what the pack says wins


def test_a_pack_name_found_twice_is_an_error(tmp_path: Path, monkeypatch) -> None:
    _pack(tmp_path / "org" / "steno-pack-fastapi", "steno-pack-fastapi")
    monkeypatch.setattr(get_settings(), "rule_pack_dirs", [tmp_path / "org"])
    with pytest.raises(ValueError, match="two packs named steno-pack-fastapi"):
        load_all_packs()


def test_a_missing_folder_is_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rule_pack_dirs", [tmp_path / "nowhere"])
    with pytest.raises(ValueError, match="doesn't exist"):
        load_all_packs()


def test_the_setting_reads_comma_separated_folders(monkeypatch) -> None:
    monkeypatch.setenv("STENO_RULE_PACK_DIRS", "/a/packs, /b/packs")
    assert Settings().rule_pack_dirs == [Path("/a/packs"), Path("/b/packs")]
