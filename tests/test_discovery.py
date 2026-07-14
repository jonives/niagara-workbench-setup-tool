"""Tests for discovery.py scanning and metadata helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from discovery import (
    detect_brand_and_version,
    detect_version_from_dirname,
    extract_jar_type,
)


def test_detect_brand_and_version_tridium():
    assert detect_brand_and_version("Niagara-4.15.2.38") == ("Tridium", "4.15.2.38")


def test_detect_brand_and_version_distech_ecnet_facilities():
    assert detect_brand_and_version("EC-Net Facilities-4.14.0.162") == (
        "Distech",
        "4.14.0.162",
    )


def test_detect_brand_and_version_distech_ecnet4():
    assert detect_brand_and_version("EC-Net4-4.13.0.120") == ("Distech", "4.13.0.120")


def test_detect_brand_and_version_vykon():
    assert detect_brand_and_version("Vykon-4.12.0.90") == ("Vykon", "4.12.0.90")


def test_detect_brand_and_version_honeywell_webs_n_prefix():
    # N-prefixed version (WEBs-N4.11.0.142) should strip the N
    brand, version = detect_brand_and_version("WEBs-N4.11.0.142")
    assert brand == "WEBs"
    assert version == "4.11.0.142"


def test_detect_brand_and_version_generic_multi_word():
    brand, version = detect_brand_and_version("Honeywell Niagara-4.14.0.162")
    assert brand == "Honeywell Niagara"
    assert version == "4.14.0.162"


def test_detect_brand_and_version_unknown():
    assert detect_brand_and_version("not-a-version") == ("", "")


def test_detect_version_from_dirname_n_prefixed():
    assert detect_version_from_dirname("WEBs-N4.11.0.142") == "4.11.0.142"


def test_extract_jar_type_rt():
    assert extract_jar_type("control-rt.jar") == ("control", "rt")


def test_extract_jar_type_wb():
    assert extract_jar_type("bajaui-wb.jar") == ("bajaui", "wb")


def test_extract_jar_type_other():
    assert extract_jar_type("custom.jar") == ("custom", "other")
