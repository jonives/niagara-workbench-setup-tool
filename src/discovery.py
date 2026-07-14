"""
Discovery module - scans for Niagara installs, brands, versions, modules, and user homes.
Reads module.xml from JARs for rich metadata (vendor, version, description, dependencies).
"""

import re
import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# Known brand prefixes in install directory names
# OEMs typically name their install dir: "BrandName-Version" or "Brand Name-Version"
# The generic fallback pattern handles any brand we haven't explicitly mapped.
BRAND_PATTERNS = [
    (re.compile(r'^Niagara-(\d+\.\d+\.\d+.*)$'), 'Tridium'),
    (re.compile(r'^EC-Net Facilities-(\d+\.\d+\.\d+.*)$'), 'Distech'),
    (re.compile(r'^EC-Net4?-(\d+\.\d+\.\d+.*)$'), 'Distech'),
    (re.compile(r'^Vykon[-_ ](\d+\.\d+\.\d+.*)$'), 'Vykon'),
    (re.compile(r'^HawkVision[-_ ](\d+\.\d+\.\d+.*)$'), 'Honeywell'),
    (re.compile(r'^Spyder[-_ ](\d+\.\d+\.\d+.*)$'), 'Honeywell'),
    # Generic fallback: "BrandName-Version" or "Brand Name-Version"
    # Match from the end: find last [-_ ] followed by N?\d+\.\d+\.\d+
    # This handles multi-word names with hyphens like "EC-Net Facilities-4.15"
    (re.compile(r'^(.+?)[-_ ](N?\d+\.\d+\.\d+.*)$'), None),
]

DEFAULT_SCAN_ROOTS = [
    r"C:\Niagara",
    r"C:\Program Files\Niagara",
    r"C:\Program Files (x86)\Niagara",
]

USER_HOME_NIAGARA_PATTERN = re.compile(r'^Niagara(\d+\.\d+(?:[uU]\d+)?)$')


@dataclass
class ModuleMeta:
    """Metadata extracted from META-INF/module.xml inside a JAR."""
    vendor: str = ""
    vendor_version: str = ""
    description: str = ""
    module_name: str = ""          # clean name from module.xml (moduleName attr)
    preferred_symbol: str = ""
    dependencies: list[str] = field(default_factory=list)  # dependency module names
    runtime_profile: str = ""      # "rt", "wb", "ux", etc.


@dataclass
class ModuleInfo:
    """Represents a single module JAR found in an install."""
    filename: str
    module_name: str         # parsed from filename (without -rt.jar etc.)
    jar_type: str            # "rt", "wb", "ux", "se", "other"
    size_bytes: int
    full_path: str
    meta: Optional[ModuleMeta] = None  # from module.xml, may be None if read failed


@dataclass
class InstallInfo:
    """Represents a single Niagara installation."""
    install_path: str
    brand: str
    version: str
    version_major_minor: str
    niagara_version: int
    modules: list[ModuleInfo] = field(default_factory=list)
    new_components_bog: Optional[str] = None
    system_properties: Optional[str] = None
    nre_properties_default: Optional[str] = None
    module_verification_mode: Optional[str] = None
    nre_wb_xmx: Optional[str] = None
    nre_station_xmx: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f"{self.brand} {self.version} ({self.install_path})"

    @property
    def module_names(self) -> set[str]:
        return {m.module_name for m in self.modules}

    def get_module_info(self, module_name: str) -> Optional[ModuleInfo]:
        """Get the first ModuleInfo for a given module name (prefers rt jar)."""
        rt = next((m for m in self.modules if m.module_name == module_name and m.jar_type == 'rt'), None)
        if rt:
            return rt
        return next((m for m in self.modules if m.module_name == module_name), None)


@dataclass
class UserHomeBrand:
    """Represents a brand directory inside a user home Niagara folder."""
    brand_name: str
    base_path: str
    nre_properties: Optional[str] = None
    nav_tree_xml: Optional[str] = None
    recent_ords_xml: Optional[str] = None
    wb_profile_xml: Optional[str] = None
    credentials_xml: Optional[str] = None
    nre_wb_xmx: Optional[str] = None
    nre_station_xmx: Optional[str] = None


@dataclass
class UserHomeInfo:
    """Represents a user home Niagara directory."""
    base_path: str
    version_major_minor: str
    brands: list[UserHomeBrand] = field(default_factory=list)


def detect_brand_and_version(dir_name: str) -> tuple[str, str]:
    """Detect brand and version from a directory name."""
    for pattern, brand in BRAND_PATTERNS:
        match = pattern.match(dir_name)
        if match:
            if brand is not None:
                return brand, match.group(1)
            else:
                detected_brand = match.group(1).strip()
                version = match.group(2)
                # Strip "N" prefix from version (e.g. "N4.11.0.142" -> "4.11.0.142")
                if version.startswith('N'):
                    version = version[1:]
                return detected_brand, version
    return "", ""


def detect_version_from_dirname(dir_name: str) -> str:
    """Extract just the version string from a directory name, ignoring brand."""
    for pattern, brand in BRAND_PATTERNS:
        match = pattern.match(dir_name)
        if match:
            # For all patterns, the version is in the last capture group
            ver = match.group(match.lastindex)
            if ver.startswith('N'):
                ver = ver[1:]
            return ver
    return ""


def read_brand_properties(install_path: str) -> tuple[str, str]:
    """Read brand.properties to get the canonical brand name.
    Checks etc/brand.properties, then overlay/etc/brand.properties.
    Returns (brand_id, workbench_title) -- either may be empty.
    """
    p = Path(install_path)
    for bp_path in [p / 'etc' / 'brand.properties',
                    p / 'overlay' / 'etc' / 'brand.properties']:
        if not bp_path.is_file():
            continue
        try:
            with open(bp_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            brand_id = ""
            title = ""
            for line in content.splitlines():
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                if line.startswith('brand.id='):
                    brand_id = line.split('=', 1)[1].strip()
                elif line.startswith('workbench.title='):
                    title = line.split('=', 1)[1].strip()
            return brand_id, title
        except Exception:
            pass
    return "", ""


def is_niagara_install(install_path: str) -> bool:
    """Check if a directory looks like a Niagara install by looking for
    structural markers: modules/ dir with JARs, or bin/ containing nre.jar."""
    p = Path(install_path)
    modules_dir = p / 'modules'
    if modules_dir.is_dir():
        try:
            next(modules_dir.glob('*.jar'))
            return True
        except StopIteration:
            pass
    # Fallback for AX: bin/ dir with nre.jar or station.exe or wb.exe
    bin_dir = p / 'bin'
    if bin_dir.is_dir():
        for marker in ['nre.jar', 'station.exe', 'wb.exe', 'plat.exe']:
            if (bin_dir / marker).is_file():
                return True
    return False


def extract_jar_type(filename: str) -> tuple[str, str]:
    """Extract module name and jar type from a JAR filename."""
    for suffix in ['-rt.jar', '-wb.jar', '-ux.jar', '-se.jar', '-lib.jar', '-doc.jar']:
        if filename.endswith(suffix):
            module_name = filename[:-len(suffix)]
            jar_type = suffix[1:-4]
            return module_name, jar_type
    if filename.endswith('.jar'):
        return filename[:-4], 'other'
    return filename, 'other'


def read_module_xml(jar_path: str) -> Optional[ModuleMeta]:
    """Read META-INF/module.xml from a JAR file and parse metadata."""
    try:
        with zipfile.ZipFile(jar_path, 'r') as z:
            if 'META-INF/module.xml' not in z.namelist():
                return None
            with z.open('META-INF/module.xml') as f:
                content = f.read().decode('utf-8', errors='replace')
    except Exception:
        return None

    meta = ModuleMeta()
    try:
        root = ET.fromstring(content)
        meta.vendor = root.get('vendor', '')
        meta.vendor_version = root.get('vendorVersion', '')
        meta.description = root.get('description', '')
        meta.module_name = root.get('moduleName', root.get('name', ''))
        meta.preferred_symbol = root.get('preferredSymbol', '')
        meta.runtime_profile = root.get('runtimeProfile', '')

        # Parse dependencies
        deps_elem = root.find('dependencies')
        if deps_elem is not None:
            for dep in deps_elem.findall('dependency'):
                dep_name = dep.get('name', '')
                # Strip -rt/-wb suffix from dependency name
                for suffix in ['-rt', '-wb', '-ux', '-se']:
                    if dep_name.endswith(suffix):
                        dep_name = dep_name[:-len(suffix)]
                        break
                if dep_name:
                    meta.dependencies.append(dep_name)
    except ET.ParseError:
        pass

    return meta


def parse_nre_properties(filepath: str) -> tuple[Optional[str], Optional[str]]:
    """Parse nre.properties to extract wb and station Xmx values."""
    wb_xmx = None
    station_xmx = None
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                if line.startswith('wb.java.options='):
                    m = re.search(r'-Xmx(\d+[GgMm])', line)
                    if m:
                        wb_xmx = m.group(1)
                elif line.startswith('station.java.options='):
                    m = re.search(r'-Xmx(\d+[GgMm])', line)
                    if m:
                        station_xmx = m.group(1)
    except Exception:
        pass
    return wb_xmx, station_xmx


def parse_system_properties(filepath: str) -> Optional[str]:
    """Parse system.properties to extract niagara.moduleVerificationMode."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                if line.startswith('niagara.moduleVerificationMode='):
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return None


def scan_install(install_path: str, read_metadata: bool = True) -> Optional[InstallInfo]:
    """Scan a single Niagara installation directory.
    Uses a combined approach:
    1. Structural check (modules/ with JARs, or bin/+lib/) to confirm it's a Niagara install
    2. brand.properties for canonical brand name (etc/ or overlay/etc/)
    3. Directory name regex for version extraction and brand fallback
    """
    p = Path(install_path)
    if not p.is_dir():
        return None

    # 1. Structural check -- must look like a Niagara install
    if not is_niagara_install(str(p)):
        return None

    # 2. Extract version from directory name
    dir_name = p.name
    version = detect_version_from_dirname(dir_name)
    if not version:
        return None  # Can't determine version, not a valid install

    # 3. Get brand from brand.properties (canonical), fall back to directory name
    brand_id, wb_title = read_brand_properties(str(p))
    dir_brand, _ = detect_brand_and_version(dir_name)

    if brand_id:
        brand = brand_id
    elif wb_title:
        # Use the title as brand if no brand.id (e.g. "Distech Controls EC-Net Facilities")
        brand = wb_title
    elif dir_brand:
        brand = dir_brand
    else:
        brand = "Unknown"

    # Determine Niagara version (3 vs 4)
    if version.startswith('3.'):
        niagara_ver = 3
    elif version.startswith('4.'):
        niagara_ver = 4
    else:
        niagara_ver = 4 if (p / 'defaults').is_dir() else 3

    vm_match = re.match(r'(\d+\.\d+)', version)
    version_major_minor = vm_match.group(1) if vm_match else ""

    info = InstallInfo(
        install_path=str(p),
        brand=brand,
        version=version,
        version_major_minor=version_major_minor,
        niagara_version=niagara_ver,
    )

    # Find modules
    modules_dir = p / 'modules'
    if modules_dir.is_dir():
        for jar_file in sorted(modules_dir.glob('*.jar')):
            module_name, jar_type = extract_jar_type(jar_file.name)
            mod_info = ModuleInfo(
                filename=jar_file.name,
                module_name=module_name,
                jar_type=jar_type,
                size_bytes=jar_file.stat().st_size,
                full_path=str(jar_file),
            )
            # Read module.xml metadata (only for rt jars to avoid duplicate reads)
            if read_metadata and jar_type == 'rt':
                mod_info.meta = read_module_xml(str(jar_file))
            info.modules.append(mod_info)

    # Find newComponents.bog -- check defaults/, then overlay/defaults/
    if niagara_ver == 4:
        for bog_path in [p / 'defaults' / 'workbench' / 'newComponents.bog',
                         p / 'overlay' / 'defaults' / 'workbench' / 'newComponents.bog']:
            if bog_path.is_file():
                info.new_components_bog = str(bog_path)
                break
    else:
        bog_path = p / 'workbench' / 'newComponents.bog'
        if bog_path.is_file():
            info.new_components_bog = str(bog_path)

    # system.properties -- check etc/ first (overrides), then defaults/, then overlay/defaults/
    for sys_props in [p / 'etc' / 'system.properties',
                      p / 'defaults' / 'system.properties',
                      p / 'overlay' / 'etc' / 'system.properties',
                      p / 'overlay' / 'defaults' / 'system.properties']:
        if sys_props.is_file():
            info.system_properties = str(sys_props)
            info.module_verification_mode = parse_system_properties(str(sys_props))
            break

    # nre.properties -- check defaults/, then etc/, then overlay/defaults/
    for nre_props in [p / 'defaults' / 'nre.properties',
                      p / 'etc' / 'nre.properties',
                      p / 'overlay' / 'defaults' / 'nre.properties']:
        if nre_props.is_file():
            info.nre_properties_default = str(nre_props)
            wb_xmx, station_xmx = parse_nre_properties(str(nre_props))
            info.nre_wb_xmx = wb_xmx
            info.nre_station_xmx = station_xmx
            break

    return info


def scan_for_installs(search_roots: list[str] = None, read_metadata: bool = False) -> list[InstallInfo]:
    """Scan for all Niagara installations.
    read_metadata defaults to False for speed on initial scan.
    Call read_module_metadata() later for specific modules when needed.
    """
    if search_roots is None:
        search_roots = DEFAULT_SCAN_ROOTS

    installs: list[InstallInfo] = []
    for root in search_roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for child in sorted(root_path.iterdir()):
            if not child.is_dir():
                continue
            info = scan_install(str(child), read_metadata=read_metadata)
            if info is not None:
                installs.append(info)

    installs.sort(key=lambda i: (i.brand, i.version))
    return installs


def scan_user_homes(username: str = None) -> list[UserHomeInfo]:
    """Scan user home for Niagara profile directories."""
    if username is None:
        user_home = Path(os.environ.get('USERPROFILE', Path.home()))
    else:
        user_home = Path.home() if username.lower() == os.environ.get('USERNAME', '').lower() else Path(f"C:\\Users\\{username}")
    if not user_home.is_dir():
        return []

    homes: list[UserHomeInfo] = []
    for child in sorted(user_home.iterdir()):
        if not child.is_dir():
            continue
        match = USER_HOME_NIAGARA_PATTERN.match(child.name)
        if not match:
            continue

        version_major_minor = match.group(1)
        # Strip update suffix (e.g. "4.11u2" -> "4.11") for matching
        base_version = re.match(r'(\d+\.\d+)', version_major_minor).group(1)
        home = UserHomeInfo(base_path=str(child), version_major_minor=base_version)

        for brand_dir in sorted(child.iterdir()):
            if not brand_dir.is_dir():
                continue
            etc_dir = brand_dir / 'etc'
            if not etc_dir.is_dir():
                continue

            brand_info = UserHomeBrand(brand_name=brand_dir.name, base_path=str(brand_dir))

            nre_path = etc_dir / 'nre.properties'
            if nre_path.is_file():
                brand_info.nre_properties = str(nre_path)
                wb_xmx, station_xmx = parse_nre_properties(str(nre_path))
                brand_info.nre_wb_xmx = wb_xmx
                brand_info.nre_station_xmx = station_xmx

            nav_tree = etc_dir / 'navTree.xml'
            if nav_tree.is_file():
                brand_info.nav_tree_xml = str(nav_tree)

            recent_ords = etc_dir / 'recentOrds.xml'
            if recent_ords.is_file():
                brand_info.recent_ords_xml = str(recent_ords)

            wb_profile = etc_dir / 'wb-WbProfile.xml'
            if wb_profile.is_file():
                brand_info.wb_profile_xml = str(wb_profile)

            creds_dir = brand_dir / 'credentials'
            if creds_dir.is_dir():
                for cred_subdir in creds_dir.iterdir():
                    cred_file = cred_subdir / 'credentials.xml'
                    if cred_file.is_file():
                        brand_info.credentials_xml = str(cred_file)
                        break

            home.brands.append(brand_info)

        if home.brands:
            homes.append(home)

    return homes


def read_module_metadata_for(install: InstallInfo, module_names: list[str]) -> None:
    """Read module.xml metadata for specific modules in an install.
    Modifies install.modules in place by setting .meta on matching rt jars.
    Only reads JARs that haven't had metadata read yet.
    """
    needed = set(module_names)
    for mod in install.modules:
        if mod.module_name in needed and mod.jar_type == 'rt' and mod.meta is None:
            mod.meta = read_module_xml(mod.full_path)


def get_module_meta(install: InstallInfo, module_name: str) -> Optional[ModuleMeta]:
    """Get parsed module.xml metadata for a module from an install."""
    mod = install.get_module_info(module_name)
    if mod and mod.meta:
        return mod.meta
    return None


def get_module_vendor(install: InstallInfo, module_name: str) -> str:
    """Get vendor string for a module, or 'Unknown' if not available."""
    meta = get_module_meta(install, module_name)
    if meta and meta.vendor:
        return meta.vendor
    return "Unknown"


def get_module_version(install: InstallInfo, module_name: str) -> str:
    """Get vendor version string for a module, or '' if not available."""
    meta = get_module_meta(install, module_name)
    if meta and meta.vendor_version:
        return meta.vendor_version
    return ""


def get_module_dependencies(install: InstallInfo, module_name: str) -> list[str]:
    """Get list of dependency module names for a module."""
    meta = get_module_meta(install, module_name)
    if meta:
        return meta.dependencies
    return []


def get_module_description(install: InstallInfo, module_name: str) -> str:
    """Get description for a module."""
    meta = get_module_meta(install, module_name)
    if meta:
        return meta.description
    return ""


def get_module_diff(source: InstallInfo, target: InstallInfo) -> dict:
    """Compare modules between two installs."""
    source_names = source.module_names
    target_names = target.module_names
    return {
        'source_only': sorted(source_names - target_names),
        'target_only': sorted(target_names - source_names),
        'both': sorted(source_names & target_names),
    }


def check_dependency_warnings(
    modules_to_copy: list[str],
    source: InstallInfo,
    target: InstallInfo,
) -> dict[str, list[str]]:
    """Check if selected modules have dependencies missing from target.
    Returns dict mapping module_name -> list of missing dependency names.
    """
    target_names = target.module_names
    warnings = {}
    for mod_name in modules_to_copy:
        deps = get_module_dependencies(source, mod_name)
        missing = [d for d in deps if d not in target_names and d not in modules_to_copy]
        if missing:
            warnings[mod_name] = missing
    return warnings


if __name__ == '__main__':
    print("=== Scanning for installs (fast, no metadata) ===")
    installs = scan_for_installs(read_metadata=False)
    for inst in installs:
        print(f"\n  {inst.brand} {inst.version} (N{inst.niagara_version}) - {len(inst.modules)} modules")
        if inst.new_components_bog:
            print(f"    bog: {inst.new_components_bog}")
        if inst.module_verification_mode:
            print(f"    security: {inst.module_verification_mode}")
        if inst.nre_wb_xmx:
            print(f"    wb RAM: {inst.nre_wb_xmx}")

    # Test metadata reading for a few modules
    if installs:
        test_install = installs[-1]  # last one (Tridium 4.15.2.38)
        test_mods = ['kitControl', 'alarm', 'control']
        print(f"\n=== Reading metadata for {test_mods} from {test_install.version} ===")
        read_module_metadata_for(test_install, test_mods)
        for name in test_mods:
            meta = get_module_meta(test_install, name)
            if meta:
                print(f"  {name}: vendor={meta.vendor}, version={meta.vendor_version}, "
                      f"desc='{meta.description}', deps={meta.dependencies[:3]}")
            else:
                print(f"  {name}: no metadata")

    print("\n=== Scanning user homes ===")
    homes = scan_user_homes()
    for home in homes:
        print(f"  {home.base_path} (v{home.version_major_minor})")
        for brand in home.brands:
            print(f"    {brand.brand_name}:")
            if brand.nav_tree_xml:
                print(f"      navTree.xml: yes")
            if brand.recent_ords_xml:
                print(f"      recentOrds.xml: yes")
            if brand.credentials_xml:
                print(f"      credentials.xml: yes")
            if brand.nre_wb_xmx:
                print(f"      wb RAM: {brand.nre_wb_xmx}")