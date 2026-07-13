# 🔧 Niagara Workbench Setup Tool

**by jives**

A portable Windows GUI tool for migrating modules, copying configuration files, and tuning properties across multiple Niagara N4 installations.

> ⚠️ **THIS TOOL IS UNTESTED. USE AT YOUR OWN RISK.**
>
> This tool has not been tested on any system other than the developer's machine.
> It modifies files in your Niagara installation directories. While all modifications
> are backed up before changes are made, there is no guarantee that this tool will not
> break your installation. **BACK UP YOUR NIAGARA INSTALLATIONS BEFORE USING THIS TOOL.**

---

## ⚠️⚠️⚠️ DISCLAIMERS ⚠️⚠️⚠️

### This is pre-alpha software

- ❌ It has **not** been tested end-to-end on a clean system
- ❌ It has **not** been tested with OEM installations other than Tridium and Distech
- ❌ It has **not** been tested with Niagara versions other than those on the developer's machine (4.11–4.15)
- ❌ It has **not** been tested with legacy Niagara AX installations — the tool may detect them but file paths and structures differ
- ❌ It has **not** been reviewed or endorsed by Tridium, Honeywell, Distech, or any Niagara OEM
- ❌ It is **not** an official Tridium product
- ❌ It is **not** affiliated with, endorsed by, or supported by Tridium or any Niagara OEM
- ⚠️ It modifies files in your Niagara installation directories (`modules/`, `etc/system.properties`, `defaults/nre.properties`, user home `etc/` directories)
- ⚠️ All modifications create timestamped backups, but **you should still make your own full backups**
- ⚠️ The module migration feature copies JAR files between installs — copying modules between major versions (e.g., 4.13 → 4.15) may cause issues if the modules have ABI incompatibilities
- ⚠️ The security level change feature modifies `niagara.moduleVerificationMode` in `system.properties` — lowering security on production systems is dangerous
- ⚠️ The RAM adjustment feature modifies `-Xmx` values in `nre.properties` — setting values too high can cause Java to fail to start
- ⚠️ The navTree.xml merge is additive but has not been tested with all possible XML structures
- ⚠️ This tool does **not** and **cannot** copy credentials between brand profiles — each brand profile has its own unique keystore, and credentials encrypted with one keystore cannot be decrypted by another

### If something breaks

1. All modified files are backed up to a `.workbenchSetupTool.backups/` directory next to the modified file, organized by timestamp
2. To restore: copy the backup file back to its original location
3. If modules were copied and cause issues: delete the copied JARs from the target install's `modules/` directory
4. Open an issue on this repo with details of what happened

---

## What It Does

### Module Migration
- Scans all Niagara installations found in `C:\Niagara\`, `C:\Program Files\Niagara\`, and `C:\Program Files (x86)\Niagara\`
- Reads module metadata (vendor, version, description, dependencies) from `META-INF/module.xml` inside each JAR
- Compares modules between a source and target install
- Groups modules by vendor and sub-groups by version (toggleable)
- Shows which modules are source-only (copy candidates), in both (shared), or target-only
- Detects version differences when a module exists in both installs but at different versions
- Checks for missing dependencies and warns before execution
- Search/filter across all columns

### File Copy
- **newComponents.bog**: Copy the component palette file between installs
- **Station Login History**: Additively merge `navTree.xml` and `recentOrds.xml` between user home brand profiles (existing entries are preserved, duplicates are skipped)
- **wb-WbProfile.xml**: Replace workbench window state profile
- Brand profiles are automatically derived from the source/target install selections

### Properties Tuning
- **Security level**: Set `niagara.moduleVerificationMode` to `low`, `medium`, or `high` in the target install's `system.properties`
- **Workbench RAM**: Set `-Xmx` for the Workbench process via spinbox selector (1–128 GB)
- **Station RAM**: Set `-Xmx` for the Station process via spinbox selector (1–128 GB)
- RAM changes automatically apply to both the install's `defaults/nre.properties` AND matching user home brand `etc/nre.properties` files

## Supported Brands

The tool auto-detects brands from install directory names. Known patterns:

| Brand | Directory Pattern | User Home Dir |
|-------|------------------|---------------|
| Tridium | `Niagara-4.x.x.x` | `tridium` |
| Distech | `EC-Net Facilities-4.x.x.x`, `EC-Net4-4.x.x.x` | `distech` |
| Vykon | `Vykon-4.x.x.x` | `vykon` |
| Honeywell | `HawkVision-4.x.x.x`, `Spyder-4.x.x.x` | `honeywell` |
| Any OEM | `BrandName-4.x.x.x` | `brandname` (case-insensitive) |

The generic fallback pattern handles any OEM that follows the standard `BrandName-Version` directory naming convention, including multi-word names like `Honeywell Niagara-4.14.0.162`.

## Building

Requires Python 3.11+ and PySide6.

```bash
pip install PySide6 pyinstaller
pyinstaller build.spec --clean --noconfirm
```

The portable EXE will be in `dist/NiagaraWorkbenchSetupTool.exe` (~45 MB, single file, no install needed).

## Running from Source

```bash
pip install PySide6
python src/main.py
```

## Tech Stack

- **Python 3.11+**
- **PySide6 (Qt6)** — GUI framework
- **PyInstaller** — single-file EXE packaging
- **stdlib only** for file operations (shutil, xml.etree, zipfile, pathlib)

No external API calls, no network access, no telemetry. Everything runs locally.

## License

MIT — see [LICENSE](LICENSE)

## Acknowledgments

- Niagara is a registered trademark of Tridium, Inc. (a Honeywell company)
- This tool is an independent project and is not affiliated with or endorsed by Tridium or any Niagara OEM
- Built with PySide6 by The Qt Company

## Contributing

This tool is in early development. If you have a Niagara installation from an OEM not listed above and encounter issues, please open an issue with:
- Your install directory name(s)
- The brand/version detected (or not detected)
- Any error messages from the log panel

Bug reports and pull requests are welcome.