"""
Operations module - handles all file copy and properties editing operations.
Every operation creates a backup first and logs its actions.
navTree.xml merge is additive (merges hosts/sessions, never replaces).
"""

import shutil
import re
import os
import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable
from dataclasses import dataclass, field

from discovery import InstallInfo, UserHomeBrand


@dataclass
class OperationResult:
    """Result of a single operation."""
    success: bool
    message: str
    backup_path: Optional[str] = None
    details: list[str] = field(default_factory=list)


def get_backup_dir(base_path: str) -> Path:
    """Create and return a backup directory next to the given path."""
    p = Path(base_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = p.parent / f"{p.name}.workbenchSetupTool.backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_file(filepath: str, backup_dir: Path) -> Optional[str]:
    """Backup a file to the backup directory."""
    src = Path(filepath)
    if not src.is_file():
        return None
    dest = backup_dir / src.name
    shutil.copy2(str(src), str(dest))
    return str(dest)


def copy_new_components_bog(source_path: str, target_path: str) -> OperationResult:
    """Copy newComponents.bog from source to target."""
    details = []
    src = Path(source_path)
    tgt = Path(target_path)

    if not src.is_file():
        return OperationResult(False, f"Source file not found: {source_path}")

    backup_path = None
    if tgt.is_file():
        backup_dir = get_backup_dir(str(tgt.parent))
        backup_path = backup_file(str(tgt), backup_dir)
        details.append(f"Backed up existing target to: {backup_path}")

    try:
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(tgt))
        details.append(f"Copied {src.name} -> {tgt}")
        return OperationResult(True, "newComponents.bog copied successfully", backup_path, details)
    except Exception as e:
        return OperationResult(False, f"Failed to copy: {e}", backup_path, details)


# ---------------------------------------------------------------------------
# navTree.xml additive merge
# ---------------------------------------------------------------------------

def _session_key(session_elem: ET.Element) -> str:
    """Build a unique key for a <session> element to detect duplicates."""
    agent = session_elem.get('agent', '')
    port = session_elem.get('port', '')
    return f"{agent}:{port}"


def _host_key(host_elem: ET.Element) -> str:
    """Build a unique key for a <host> element."""
    return host_elem.get('ord', '')


def _merge_hosts(target_host: ET.Element, source_host: ET.Element) -> tuple[int, int]:
    """Merge sessions from source_host into target_host.
    Returns (sessions_added, sessions_skipped).
    """
    existing_sessions = {_session_key(s) for s in target_host.findall('session')}
    added = 0
    skipped = 0
    for src_session in source_host.findall('session'):
        key = _session_key(src_session)
        if key in existing_sessions:
            skipped += 1
        else:
            target_host.append(copy.deepcopy(src_session))
            added += 1
    return added, skipped


def _merge_nav_tree(source_xml: str, target_xml: str) -> tuple[str, int, int]:
    """Merge source navTree.xml into target navTree.xml additively.
    Merges hosts (matched by ord) and sessions (matched by agent+port).
    Preserves folders, adding any from source that don't exist in target.
    Returns (merged_xml_string, hosts_added, sessions_added).
    """
    src_tree = ET.parse(source_xml)
    tgt_tree = ET.parse(target_xml)
    src_root = src_tree.getroot()
    tgt_root = tgt_tree.getroot()

    # Index existing target hosts by ord (top-level only)
    existing_hosts: dict[str, ET.Element] = {}
    for child in list(tgt_root):
        if child.tag == 'host':
            existing_hosts[_host_key(child)] = child

    # Index existing target folders by name
    existing_folders: dict[str, ET.Element] = {}
    for child in list(tgt_root):
        if child.tag == 'folder':
            existing_folders[child.get('name', '')] = child

    hosts_added = 0
    sessions_added = 0

    for src_child in list(src_root):
        if src_child.tag == 'host':
            key = _host_key(src_child)
            if key in existing_hosts:
                # Merge sessions into existing host
                added, _ = _merge_hosts(existing_hosts[key], src_child)
                sessions_added += added
            else:
                # Add new host (deepcopy to avoid stealing from source tree)
                new_host = copy.deepcopy(src_child)
                tgt_root.append(new_host)
                existing_hosts[key] = new_host
                hosts_added += 1
                sessions_added += len(src_child.findall('session'))

        elif src_child.tag == 'folder':
            folder_name = src_child.get('name', '')
            if folder_name in existing_folders:
                # Merge hosts inside the folder
                tgt_folder = existing_folders[folder_name]
                tgt_folder_hosts: dict[str, ET.Element] = {}
                for fc in tgt_folder:
                    if fc.tag == 'host':
                        tgt_folder_hosts[_host_key(fc)] = fc
                for src_fc in src_child:
                    if src_fc.tag == 'host':
                        fkey = _host_key(src_fc)
                        if fkey in tgt_folder_hosts:
                            added, _ = _merge_hosts(tgt_folder_hosts[fkey], src_fc)
                            sessions_added += added
                        else:
                            tgt_folder.append(copy.deepcopy(src_fc))
                            hosts_added += 1
            else:
                new_folder = copy.deepcopy(src_child)
                tgt_root.append(new_folder)
                existing_folders[folder_name] = new_folder

    # Write merged XML
    # Note: _indent_xml reformats the entire file. This is acceptable because
    # Niagara rewrites these files on launch anyway, so formatting is not preserved.
    _indent_xml(tgt_root)
    xml_str = ET.tostring(tgt_root, encoding='unicode', xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    return xml_str, hosts_added, sessions_added


def _indent_xml(elem: ET.Element, level: int = 0):
    """Add whitespace indentation to XML for readability."""
    indent = " "
    i = "\n" + level * indent
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = f"{i}{indent}"
        for child in elem:
            _indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


# ---------------------------------------------------------------------------
# recentOrds.xml additive merge
# ---------------------------------------------------------------------------

def _merge_recent_ords(source_xml: str, target_xml: str) -> tuple[str, int]:
    """Merge source recentOrds.xml into target additively.
    Adds entries that don't exist in target (matched by ord).
    Returns (merged_xml_string, entries_added).
    """
    src_tree = ET.parse(source_xml)
    tgt_tree = ET.parse(target_xml)
    src_root = src_tree.getroot()
    tgt_root = tgt_tree.getroot()

    existing_ords = {e.get('ord', '') for e in tgt_root.findall('entry')}
    added = 0

    for src_entry in src_root.findall('entry'):
        ord_val = src_entry.get('ord', '')
        if ord_val and ord_val not in existing_ords:
            tgt_root.append(copy.deepcopy(src_entry))
            existing_ords.add(ord_val)
            added += 1

    _indent_xml(tgt_root)
    xml_str = ET.tostring(tgt_root, encoding='unicode', xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    return xml_str, added


# ---------------------------------------------------------------------------
# File copy operations
# ---------------------------------------------------------------------------

def copy_station_login_xml(
    source_brand: UserHomeBrand,
    target_brand: UserHomeBrand,
    copy_nav_tree: bool = True,
    copy_recent_ords: bool = True,
    copy_wb_profile: bool = True,
) -> OperationResult:
    """Copy station login XML files from source to target user home brand.
    navTree.xml and recentOrds.xml are merged additively.
    wb-WbProfile.xml is replaced (window state, not additive).
    """
    details = []
    backup_paths = []

    target_etc = Path(target_brand.base_path) / "etc"
    if not target_etc.is_dir():
        return OperationResult(False, f"Target etc directory not found: {target_etc}")

    backup_dir = get_backup_dir(str(target_etc))
    merged_count = 0
    copied_count = 0

    # navTree.xml -- additive merge
    if copy_nav_tree and source_brand.nav_tree_xml:
        target_nav = target_etc / "navTree.xml"
        if target_nav.is_file():
            try:
                merged_xml, hosts_added, sessions_added = _merge_nav_tree(
                    source_brand.nav_tree_xml, str(target_nav)
                )
                bp = backup_file(str(target_nav), backup_dir)
                backup_paths.append(bp)
                details.append(f"Backed up navTree.xml -> {bp}")

                with open(target_nav, 'w', encoding='utf-8') as f:
                    f.write(merged_xml)
                details.append(f"Merged navTree.xml: +{hosts_added} hosts, +{sessions_added} sessions")
                merged_count += 1
            except Exception as e:
                details.append(f"ERROR merging navTree.xml: {e}")
        elif Path(source_brand.nav_tree_xml).is_file():
            # No target file, just copy
            try:
                shutil.copy2(source_brand.nav_tree_xml, str(target_nav))
                details.append(f"Copied navTree.xml (new file)")
                copied_count += 1
            except Exception as e:
                details.append(f"ERROR copying navTree.xml: {e}")

    # recentOrds.xml -- additive merge
    if copy_recent_ords and source_brand.recent_ords_xml:
        target_ro = target_etc / "recentOrds.xml"
        if target_ro.is_file():
            try:
                merged_xml, entries_added = _merge_recent_ords(
                    source_brand.recent_ords_xml, str(target_ro)
                )
                bp = backup_file(str(target_ro), backup_dir)
                backup_paths.append(bp)
                details.append(f"Backed up recentOrds.xml -> {bp}")

                with open(target_ro, 'w', encoding='utf-8') as f:
                    f.write(merged_xml)
                details.append(f"Merged recentOrds.xml: +{entries_added} entries")
                merged_count += 1
            except Exception as e:
                details.append(f"ERROR merging recentOrds.xml: {e}")
        elif Path(source_brand.recent_ords_xml).is_file():
            try:
                shutil.copy2(source_brand.recent_ords_xml, str(target_ro))
                details.append(f"Copied recentOrds.xml (new file)")
                copied_count += 1
            except Exception as e:
                details.append(f"ERROR copying recentOrds.xml: {e}")

    # wb-WbProfile.xml -- replace (window state is not additive)
    if copy_wb_profile and source_brand.wb_profile_xml:
        target_wp = target_etc / "wb-WbProfile.xml"
        if target_wp.is_file():
            bp = backup_file(str(target_wp), backup_dir)
            backup_paths.append(bp)
            details.append(f"Backed up wb-WbProfile.xml -> {bp}")
        try:
            shutil.copy2(source_brand.wb_profile_xml, str(target_wp))
            details.append(f"Copied wb-WbProfile.xml -> {target_wp}")
            copied_count += 1
        except Exception as e:
            details.append(f"ERROR copying wb-WbProfile.xml: {e}")

    total_ops = merged_count + copied_count
    if total_ops == 0:
        return OperationResult(False, "No XML files found in source to copy", str(backup_dir) if backup_paths else None, details)

    msg = f"{merged_count} merged, {copied_count} copied"
    return OperationResult(total_ops > 0, msg, str(backup_dir) if backup_paths else None, details)


# ---------------------------------------------------------------------------
# Brand mapping
# ---------------------------------------------------------------------------

BRAND_ALIASES = {
    'Webs': ['honeywell', 'webs'],
    'Honeywell': ['honeywell', 'webs'],
    'Tridium': ['tridium'],
    'vykon': ['vykon'],
    'distech': ['distech'],
    'TridiumEMEA': ['tridiumemea', 'tridium'],
    'Alerton': ['alerton', 'alki'],
}


def _brand_names_for(install_brand: str) -> list[str]:
    """Return acceptable user-home brand names for a given install brand."""
    return [n.lower() for n in BRAND_ALIASES.get(install_brand, [install_brand])]


def brand_matches(install_brand: str, home_brand_name: str) -> bool:
    """Check if a user home brand name matches an install brand (with aliases)."""
    return home_brand_name.lower() in _brand_names_for(install_brand)


def get_brand_for_install(install: InstallInfo, user_homes: list) -> Optional[UserHomeBrand]:
    """Find the user home brand that matches an install's brand and version.
    Uses case-insensitive matching of install.brand against user home brand dir names.
    Falls back to a known-aliases table for common mismatches.
    """
    target_names = _brand_names_for(install.brand)

    for home in user_homes:
        if home.version_major_minor == install.version_major_minor:
            for brand in home.brands:
                if brand.brand_name.lower() in target_names:
                    return brand
    return None


# ---------------------------------------------------------------------------
# Module copy
# ---------------------------------------------------------------------------

def copy_modules(
    source_install: InstallInfo,
    target_install: InstallInfo,
    module_names: list[str],
    progress_callback: Callable[[int, int, str], None] = None,
) -> OperationResult:
    """Copy selected module JARs from source to target install."""
    details = []
    target_modules_dir = Path(target_install.install_path) / "modules"
    if not target_modules_dir.is_dir():
        return OperationResult(False, f"Target modules directory not found: {target_modules_dir}")

    jars_to_copy = []
    for module_name in module_names:
        for mod in source_install.modules:
            if mod.module_name == module_name:
                jars_to_copy.append(mod)

    if not jars_to_copy:
        return OperationResult(False, "No matching JAR files found in source")

    total = len(jars_to_copy)
    backup_dir = get_backup_dir(str(target_modules_dir))
    copied = 0
    backed_up = 0

    for i, mod in enumerate(jars_to_copy):
        target_jar = target_modules_dir / mod.filename
        if target_jar.is_file():
            backup_file(str(target_jar), backup_dir)
            backed_up += 1
        try:
            shutil.copy2(mod.full_path, str(target_jar))
            details.append(f"Copied {mod.filename}")
            copied += 1
        except Exception as e:
            details.append(f"ERROR copying {mod.filename}: {e}")
        if progress_callback:
            progress_callback(i + 1, total, mod.filename)

    msg = f"Copied {copied}/{total} JAR files"
    if backed_up > 0:
        msg += f" ({backed_up} existing files backed up)"
    return OperationResult(copied > 0, msg, str(backup_dir) if backed_up > 0 else None, details)


# ---------------------------------------------------------------------------
# Properties editing
# ---------------------------------------------------------------------------

def set_module_verification_mode(install: InstallInfo, mode: str) -> OperationResult:
    """Set niagara.moduleVerificationMode in system.properties."""
    details = []
    sys_props_path = Path(install.system_properties) if install.system_properties else None
    if not sys_props_path or not sys_props_path.is_file():
        for candidate in [Path(install.install_path) / "etc" / "system.properties",
                          Path(install.install_path) / "defaults" / "system.properties",
                          Path(install.install_path) / "overlay" / "etc" / "system.properties",
                          Path(install.install_path) / "overlay" / "defaults" / "system.properties"]:
            if candidate.is_file():
                sys_props_path = candidate
                break
    if not sys_props_path or not sys_props_path.is_file():
        return OperationResult(False, f"system.properties not found in {install.install_path}")

    try:
        with open(sys_props_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return OperationResult(False, f"Failed to read system.properties: {e}")

    backup_dir = get_backup_dir(str(sys_props_path.parent))
    backup_path = backup_file(str(sys_props_path), backup_dir)
    details.append(f"Backed up to: {backup_path}")

    pattern = re.compile(r'^(#?\s*)niagara\.moduleVerificationMode\s*=\s*\S+', re.MULTILINE)
    replacement = f"niagara.moduleVerificationMode={mode}"

    if pattern.search(content):
        new_content = pattern.sub(replacement, content)
    else:
        new_content = content.rstrip() + f"\n\n# Added by Workbench Setup Tool\nniagara.moduleVerificationMode={mode}\n"

    try:
        with open(sys_props_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        details.append(f"Set niagara.moduleVerificationMode={mode}")
        return OperationResult(True, f"Security mode set to '{mode}'", backup_path, details)
    except Exception as e:
        return OperationResult(False, f"Failed to write system.properties: {e}", backup_path, details)


def set_nre_ram(
    install_or_brand_base_path: str,
    wb_xmx: Optional[str] = None,
    station_xmx: Optional[str] = None,
    nre_props_path: Optional[str] = None,
) -> OperationResult:
    """Set wb/station -Xmx values in nre.properties."""
    details = []
    if nre_props_path:
        props_path = Path(nre_props_path)
    else:
        props_path = Path(install_or_brand_base_path) / "etc" / "nre.properties"
        if not props_path.is_file():
            props_path = Path(install_or_brand_base_path) / "defaults" / "nre.properties"

    if not props_path.is_file():
        return OperationResult(False, f"nre.properties not found at {props_path}")

    try:
        with open(props_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return OperationResult(False, f"Failed to read nre.properties: {e}")

    backup_dir = get_backup_dir(str(props_path.parent))
    backup_path = backup_file(str(props_path), backup_dir)
    details.append(f"Backed up to: {backup_path}")

    changes = []

    def _update_xmx_line(content: str, key: str, value: str) -> str:
        """Replace or append -Xmx on a java.options line. Removes any existing
        -Xmx values to avoid duplicates, then appends the new one at the end.
        """
        line_re = re.compile(rf'^({re.escape(key)}\.java\.options=)(.*)$', re.MULTILINE)

        def _rewrite_line(m: re.Match) -> str:
            prefix = m.group(1)
            opts = m.group(2)
            # Strip all existing -Xmx<N><g|G|m|M> tokens and their preceding whitespace
            opts = re.sub(r'\s*-Xmx\d+[GgMm]\b', '', opts).strip()
            return f"{prefix}{opts} -Xmx{value}"

        if line_re.search(content):
            return line_re.sub(_rewrite_line, content)
        return content.rstrip() + f"\n{key}.java.options=-Dfile.encoding=UTF-8 -Xmx{value}\n"

    if wb_xmx:
        content = _update_xmx_line(content, 'wb', wb_xmx)
        changes.append(f"wb RAM -> {wb_xmx}")

    if station_xmx:
        content = _update_xmx_line(content, 'station', station_xmx)
        changes.append(f"station RAM -> {station_xmx}")

    if not changes:
        return OperationResult(True, "No changes requested", backup_path, details)

    try:
        with open(props_path, 'w', encoding='utf-8') as f:
            f.write(content)
        details.extend(changes)
        return OperationResult(True, "; ".join(changes), backup_path, details)
    except Exception as e:
        return OperationResult(False, f"Failed to write nre.properties: {e}", backup_path, details)