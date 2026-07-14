"""Tests for operations.py XML merging and property editing helpers."""

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from discovery import InstallInfo
from operations import (
    _merge_nav_tree,
    _merge_recent_ords,
    set_module_verification_mode,
    set_nre_ram,
)


def write_xml(path: Path, xml: str) -> None:
    path.write_text(xml, encoding="utf-8")


def test_merge_nav_tree_adds_host_and_session():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src_navTree.xml"
        tgt = Path(tmp) / "tgt_navTree.xml"
        write_xml(
            src,
            """<?xml version="1.0" encoding="UTF-8"?>
<NavTree>
  <host ord="station:src">
    <session agent="192.168.1.10" port="3011"/>
  </host>
</NavTree>""",
        )
        write_xml(
            tgt,
            """<?xml version="1.0" encoding="UTF-8"?>
<NavTree>
  <host ord="station:existing">
    <session agent="10.0.0.1" port="3011"/>
  </host>
</NavTree>""",
        )

        merged, hosts_added, sessions_added = _merge_nav_tree(str(src), str(tgt))
        assert hosts_added == 1
        assert sessions_added == 1
        assert 'ord="station:src"' in merged
        assert 'ord="station:existing"' in merged
        assert 'agent="192.168.1.10"' in merged


def test_merge_nav_tree_skips_duplicate_session():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src_navTree.xml"
        tgt = Path(tmp) / "tgt_navTree.xml"
        write_xml(
            src,
            """<?xml version="1.0" encoding="UTF-8"?>
<NavTree>
  <host ord="station:shared">
    <session agent="192.168.1.10" port="3011"/>
  </host>
</NavTree>""",
        )
        write_xml(
            tgt,
            """<?xml version="1.0" encoding="UTF-8"?>
<NavTree>
  <host ord="station:shared">
    <session agent="192.168.1.10" port="3011"/>
  </host>
</NavTree>""",
        )

        merged, hosts_added, sessions_added = _merge_nav_tree(str(src), str(tgt))
        assert hosts_added == 0
        assert sessions_added == 0


def test_merge_recent_ords_adds_entries():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src_recentOrds.xml"
        tgt = Path(tmp) / "tgt_recentOrds.xml"
        write_xml(
            src,
            """<?xml version="1.0" encoding="UTF-8"?>
<RecentOrds>
  <entry ord="slot:/newOne"/>
  <entry ord="slot:/existing"/>
</RecentOrds>""",
        )
        write_xml(
            tgt,
            """<?xml version="1.0" encoding="UTF-8"?>
<RecentOrds>
  <entry ord="slot:/existing"/>
</RecentOrds>""",
        )

        merged, entries_added = _merge_recent_ords(str(src), str(tgt))
        assert entries_added == 1
        assert 'ord="slot:/newOne"' in merged
        assert merged.count('ord="slot:/existing"') == 1


def test_set_nre_ram_replaces_existing_xmx():
    with tempfile.TemporaryDirectory() as tmp:
        etc = Path(tmp) / "etc"
        etc.mkdir()
        props = etc / "nre.properties"
        props.write_text(
            "wb.java.options=-Dfile.encoding=UTF-8 -Xmx2G\n"
            "station.java.options=-Dfile.encoding=UTF-8 -Xmx1G\n"
        )
        result = set_nre_ram(str(tmp), wb_xmx="8G", station_xmx="4G")
        assert result.success
        text = props.read_text()
        assert "-Xmx8G" in text
        assert "-Xmx4G" in text
        assert "-Xmx2G" not in text
        assert "-Xmx1G" not in text


def test_set_nre_ram_handles_multiple_xmx_values():
    with tempfile.TemporaryDirectory() as tmp:
        etc = Path(tmp) / "etc"
        etc.mkdir()
        props = etc / "nre.properties"
        props.write_text(
            "wb.java.options=-Dfile.encoding=UTF-8 -Xmx2G -Xmx4G\n"
        )
        result = set_nre_ram(str(tmp), wb_xmx="8G")
        assert result.success
        text = props.read_text()
        assert text.count("-Xmx") == 1
        assert "-Xmx8G" in text
        assert "-Xmx2G" not in text
        assert "-Xmx4G" not in text


def test_set_nre_ram_appends_when_line_missing():
    with tempfile.TemporaryDirectory() as tmp:
        etc = Path(tmp) / "etc"
        etc.mkdir()
        props = etc / "nre.properties"
        props.write_text("station.java.options=-Dfile.encoding=UTF-8 -Xmx1G\n")
        result = set_nre_ram(str(tmp), wb_xmx="8G")
        assert result.success
        text = props.read_text()
        assert "wb.java.options=-Dfile.encoding=UTF-8 -Xmx8G" in text


def test_set_module_verification_mode_replaces_existing():
    with tempfile.TemporaryDirectory() as tmp:
        sys_props = Path(tmp) / "system.properties"
        sys_props.write_text(
            "niagara.moduleVerificationMode=high\n"
            "some.other.prop=value\n"
        )
        info = InstallInfo(
            install_path=str(tmp),
            brand="Tridium",
            version="4.15.2.38",
            version_major_minor="4.15",
            niagara_version=4,
            system_properties=str(sys_props),
        )
        result = set_module_verification_mode(info, "low")
        assert result.success
        text = sys_props.read_text()
        assert "niagara.moduleVerificationMode=low" in text
        assert "niagara.moduleVerificationMode=high" not in text
        assert text.count("niagara.moduleVerificationMode") == 1


def test_set_module_verification_mode_tolerates_comment_and_whitespace():
    with tempfile.TemporaryDirectory() as tmp:
        sys_props = Path(tmp) / "system.properties"
        sys_props.write_text(
            "# niagara.moduleVerificationMode=high   # old setting\n"
            "some.other.prop=value\n"
        )
        info = InstallInfo(
            install_path=str(tmp),
            brand="Tridium",
            version="4.15.2.38",
            version_major_minor="4.15",
            niagara_version=4,
            system_properties=str(sys_props),
        )
        result = set_module_verification_mode(info, "low")
        assert result.success
        text = sys_props.read_text()
        assert "niagara.moduleVerificationMode=low" in text
        assert text.count("niagara.moduleVerificationMode") == 1


def test_set_module_verification_mode_appends_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        sys_props = Path(tmp) / "system.properties"
        sys_props.write_text("some.other.prop=value\n")
        info = InstallInfo(
            install_path=str(tmp),
            brand="Tridium",
            version="4.15.2.38",
            version_major_minor="4.15",
            niagara_version=4,
            system_properties=str(sys_props),
        )
        result = set_module_verification_mode(info, "medium")
        assert result.success
        text = sys_props.read_text()
        assert "niagara.moduleVerificationMode=medium" in text
