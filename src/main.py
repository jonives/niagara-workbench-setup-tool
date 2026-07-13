"""
Niagara Workbench Setup Tool - by jives
Main GUI Application

Features:
- Single source/target selector drives all operations
- Module migration with vendor + version sub-grouping, metadata from JAR module.xml
- Modules unchecked by default; Select All = all source-only modules
- Additive navTree.xml and recentOrds.xml merge (never replaces existing entries)
- File copy (bog, login XML) driven by main source/target
- Properties tuning (security level, RAM) applied to target
- Deep purple/blue dark theme
"""

import sys
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QTextEdit, QGroupBox, QLineEdit, QSpinBox,
    QHeaderView, QSplitter, QMessageBox, QFileDialog
)
from PySide6.QtGui import QFont, QColor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from discovery import (
    scan_for_installs, scan_user_homes, scan_install,
    InstallInfo, UserHomeInfo, UserHomeBrand,
    get_module_diff,
    read_module_metadata_for, get_module_meta, get_module_vendor,
    get_module_version, get_module_dependencies, get_module_description,
    check_dependency_warnings,
)
from operations import (
    copy_new_components_bog, copy_station_login_xml,
    copy_modules, set_module_verification_mode, set_nre_ram,
    get_brand_for_install, OperationResult
)


class WorkerThread(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished_ops = Signal(list)

    def __init__(self, operations_list):
        super().__init__()
        self.operations_list = operations_list

    def run(self):
        results = []
        for op in self.operations_list:
            self.log.emit(f"\n--- {op['name']} ---")
            try:
                if op['type'] == 'copy_bog':
                    result = copy_new_components_bog(op['source'], op['target'])
                elif op['type'] == 'copy_xml':
                    result = copy_station_login_xml(
                        op['source_brand'], op['target_brand'],
                        op.get('copy_nav_tree', True),
                        op.get('copy_recent_ords', True),
                        op.get('copy_wb_profile', True),
                    )
                elif op['type'] == 'copy_modules':
                    result = copy_modules(
                        op['source_install'], op['target_install'],
                        op['module_names'],
                        lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
                    )
                elif op['type'] == 'set_security':
                    result = set_module_verification_mode(op['install'], op['mode'])
                elif op['type'] == 'set_ram':
                    result = set_nre_ram(
                        op['base_path'],
                        op.get('wb_xmx'),
                        op.get('station_xmx'),
                        op.get('nre_props_path')
                    )
                else:
                    result = OperationResult(False, f"Unknown operation type: {op['type']}")

                results.append({'name': op['name'], 'result': result})
                self.log.emit(result.message)
                for d in result.details:
                    self.log.emit(f"  {d}")
                if result.backup_path:
                    self.log.emit(f"  Backup: {result.backup_path}")
            except Exception as e:
                result = OperationResult(False, f"Exception: {e}")
                results.append({'name': op['name'], 'result': result})
                self.log.emit(f"ERROR: {e}")

        self.finished_ops.emit(results)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Niagara Workbench Setup Tool - by jives")

        # Launch near-fullscreen
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(900, 600)
        self.resize(int(screen.width() * 0.95), int(screen.height() * 0.92))
        self.move(int(screen.width() * 0.025), int(screen.height() * 0.04))

        self.installs: list[InstallInfo] = []
        self.user_homes: list[UserHomeInfo] = []

        self._init_ui()
        self._do_scan()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # === Header ===
        header = QHBoxLayout()
        title = QLabel("Niagara Workbench Setup Tool")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        header.addWidget(title)

        by_label = QLabel("by jives")
        by_label.setFont(QFont("Segoe UI", 9, italic=True))
        by_label.setStyleSheet("color: #7b68ee;")
        header.addWidget(by_label)
        header.addStretch()

        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.clicked.connect(self._do_scan)
        header.addWidget(self.btn_rescan)

        self.btn_add_path = QPushButton("Add Install Path...")
        self.btn_add_path.clicked.connect(self._add_custom_path)
        header.addWidget(self.btn_add_path)
        main_layout.addLayout(header)

        # === Global Source / Target ===
        st_group = QGroupBox("Source & Target")
        st_layout = QGridLayout(st_group)
        st_layout.setContentsMargins(10, 18, 10, 10)

        st_layout.addWidget(QLabel("Source install:"), 0, 0)
        self.cmb_source = QComboBox()
        self.cmb_source.setMinimumWidth(400)
        st_layout.addWidget(self.cmb_source, 0, 1)

        st_layout.addWidget(QLabel("Target install:"), 1, 0)
        self.cmb_target = QComboBox()
        self.cmb_target.setMinimumWidth(400)
        st_layout.addWidget(self.cmb_target, 1, 1)

        self.btn_compare = QPushButton("Compare Modules")
        self.btn_compare.clicked.connect(self._compare_modules)
        st_layout.addWidget(self.btn_compare, 0, 2, 2, 1)
        main_layout.addWidget(st_group)

        # === Top Row: Discovery Tree | Module Migration ===
        top_splitter = QSplitter(Qt.Horizontal)

        # --- Discovery Tree (left) ---
        disc_group = QGroupBox("Discovered Installations")
        disc_layout = QVBoxLayout(disc_group)
        disc_layout.setContentsMargins(8, 18, 8, 8)
        self.install_tree = QTreeWidget()
        self.install_tree.setHeaderLabels(["Name", "Brand", "Version", "Modules", "Security", "WB RAM"])
        self.install_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.install_tree.header().setStretchLastSection(False)
        disc_layout.addWidget(self.install_tree)
        top_splitter.addWidget(disc_group)

        # --- Module Migration (right) ---
        mod_group = QGroupBox("Module Migration")
        mod_layout = QVBoxLayout(mod_group)
        mod_layout.setContentsMargins(8, 18, 8, 8)

        # Search + grouping options
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Filter:"))
        self.txt_mod_filter = QLineEdit()
        self.txt_mod_filter.setPlaceholderText("Type to filter modules...")
        self.txt_mod_filter.textChanged.connect(self._filter_modules)
        search_layout.addWidget(self.txt_mod_filter)

        self.chk_group_by_vendor = QCheckBox("Group by vendor")
        self.chk_group_by_vendor.setChecked(True)
        self.chk_group_by_vendor.stateChanged.connect(self._compare_modules)
        search_layout.addWidget(self.chk_group_by_vendor)

        self.chk_group_by_version = QCheckBox("Sub-group by version")
        self.chk_group_by_version.setChecked(True)
        self.chk_group_by_version.stateChanged.connect(self._compare_modules)
        search_layout.addWidget(self.chk_group_by_version)
        mod_layout.addLayout(search_layout)

        # Module tree
        self.mod_tree = QTreeWidget()
        self.mod_tree.setHeaderLabels(["Module", "Vendor", "Version", "Description", "Deps", "Action"])
        self.mod_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.mod_tree.header().setStretchLastSection(False)
        # Tri-state checkbox propagation
        self.mod_tree.itemChanged.connect(self._on_mod_tree_item_changed)
        self._mod_tree_updating = False
        mod_layout.addWidget(self.mod_tree)

        # Select buttons
        sel_layout = QHBoxLayout()
        btn_sel_all = QPushButton("Select All Source-Only")
        btn_sel_all.clicked.connect(self._select_source_only)
        btn_sel_none = QPushButton("Clear Selection")
        btn_sel_none.clicked.connect(lambda: self._select_all_modules(False))
        sel_layout.addWidget(btn_sel_all)
        sel_layout.addWidget(btn_sel_none)
        sel_layout.addStretch()
        self.lbl_mod_count = QLabel("0 selected")
        sel_layout.addWidget(self.lbl_mod_count)
        mod_layout.addLayout(sel_layout)

        top_splitter.addWidget(mod_group)
        top_splitter.setSizes([380, 620])
        main_layout.addWidget(top_splitter, 2)

        # === Bottom Row: File Copy | Properties Tuning (compact, no stretch) ===
        bottom_widget = QWidget()
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        # --- File Copy (left) ---
        file_group = QGroupBox("File Copy (source -> target)")
        file_layout = QVBoxLayout(file_group)
        file_layout.setContentsMargins(8, 18, 8, 8)
        file_layout.setSpacing(4)

        self.chk_copy_bog = QCheckBox("Copy newComponents.bog")
        self.chk_copy_bog.setToolTip("Copy the palette/bog file that defines New Component templates")
        file_layout.addWidget(self.chk_copy_bog)

        self.chk_copy_xml = QCheckBox("Copy Station Login History (additive merge)")
        self.chk_copy_xml.setToolTip(
            "Merges navTree.xml and recentOrds.xml additively -- existing entries preserved.\n"
            "wb-WbProfile.xml is replaced (window state is not additive).\n"
            "Uses the brand from the main source/target selectors."
        )
        file_layout.addWidget(self.chk_copy_xml)

        xml_files_layout = QHBoxLayout()
        xml_files_layout.setContentsMargins(20, 0, 0, 0)
        self.chk_nav_tree = QCheckBox("navTree.xml")
        self.chk_nav_tree.setChecked(True)
        self.chk_recent_ords = QCheckBox("recentOrds.xml")
        self.chk_recent_ords.setChecked(True)
        self.chk_wb_profile = QCheckBox("wb-WbProfile.xml")
        self.chk_wb_profile.setChecked(True)
        xml_files_layout.addWidget(self.chk_nav_tree)
        xml_files_layout.addWidget(self.chk_recent_ords)
        xml_files_layout.addWidget(self.chk_wb_profile)
        file_layout.addLayout(xml_files_layout)
        bottom_layout.addWidget(file_group, 1)

        # --- Properties Tuning (right) ---
        props_group = QGroupBox("Properties Tuning (applies to target)")
        props_layout = QVBoxLayout(props_group)
        props_layout.setContentsMargins(8, 18, 8, 8)
        props_layout.setSpacing(4)

        # Security + RAM in a grid for compactness
        props_grid = QGridLayout()
        props_grid.setSpacing(4)

        # Row 0: Security
        self.chk_security = QCheckBox("Security level:")
        self.chk_security.setToolTip("Set niagara.moduleVerificationMode in system.properties")
        props_grid.addWidget(self.chk_security, 0, 0)
        self.cmb_security = QComboBox()
        self.cmb_security.addItems(["low", "medium", "high"])
        self.cmb_security.setCurrentText("low")
        props_grid.addWidget(self.cmb_security, 0, 1)

        # Row 1: WB RAM
        self.chk_wb_ram = QCheckBox("Workbench RAM (-Xmx):")
        props_grid.addWidget(self.chk_wb_ram, 1, 0)
        self.spn_wb_ram = QSpinBox()
        self.spn_wb_ram.setRange(1, 128)
        self.spn_wb_ram.setValue(8)
        self.spn_wb_ram.setSuffix(" GB")
        self.spn_wb_ram.setMinimumWidth(90)
        props_grid.addWidget(self.spn_wb_ram, 1, 1)

        # Row 2: Station RAM
        self.chk_station_ram = QCheckBox("Station RAM (-Xmx):")
        props_grid.addWidget(self.chk_station_ram, 2, 0)
        self.spn_station_ram = QSpinBox()
        self.spn_station_ram.setRange(1, 128)
        self.spn_station_ram.setValue(4)
        self.spn_station_ram.setSuffix(" GB")
        self.spn_station_ram.setMinimumWidth(90)
        props_grid.addWidget(self.spn_station_ram, 2, 1)

        props_layout.addLayout(props_grid)

        ram_note = QLabel("RAM applies to install + matching user home brands.")
        ram_note.setStyleSheet("color: #a0a0b8; font-size: 9pt;")
        props_layout.addWidget(ram_note)
        bottom_layout.addWidget(props_group, 1)

        main_layout.addWidget(bottom_widget)

        # === Execute Bar ===
        exec_layout = QHBoxLayout()
        self.btn_execute = QPushButton("Review & Execute")
        self.btn_execute.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.btn_execute.setMinimumHeight(32)
        self.btn_execute.setStyleSheet(
            "QPushButton { background-color: #7b68ee; color: white; border-radius: 4px; }"
            "QPushButton:hover { background-color: #6a5acd; }"
            "QPushButton:disabled { background-color: #2a2a3a; color: #555570; }"
        )
        self.btn_execute.clicked.connect(self._execute)
        exec_layout.addWidget(self.btn_execute)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        exec_layout.addWidget(self.progress)
        main_layout.addLayout(exec_layout)

        # === Log ===
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 18, 8, 8)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setMaximumHeight(80)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group)

    def _log(self, msg: str):
        self.log_text.append(msg)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _do_scan(self):
        self._log("Scanning for Niagara installations...")
        self.installs = scan_for_installs(read_metadata=False)
        self.user_homes = scan_user_homes()
        self._log(f"Found {len(self.installs)} installs, {len(self.user_homes)} user homes")
        self._populate_install_tree()
        self._populate_combos()

    def _populate_install_tree(self):
        self.install_tree.clear()
        brands: dict[str, list[InstallInfo]] = {}
        for inst in self.installs:
            brands.setdefault(inst.brand, []).append(inst)

        for brand_name in sorted(brands.keys()):
            brand_item = QTreeWidgetItem([brand_name, "", "", "", "", ""])
            brand_item.setFont(0, QFont("Segoe UI", 10, QFont.Bold))
            brand_item.setFlags(brand_item.flags() & ~Qt.ItemIsSelectable)

            for inst in brands[brand_name]:
                mod_count = len(inst.module_names)
                sec = inst.module_verification_mode or "default"
                ram = inst.nre_wb_xmx or "default"
                item = QTreeWidgetItem([
                    Path(inst.install_path).name, inst.brand, inst.version,
                    str(mod_count), sec, ram
                ])
                item.setToolTip(0, inst.install_path)

                matching_homes = [h for h in self.user_homes if h.version_major_minor == inst.version_major_minor]
                for home in matching_homes:
                    for brand in home.brands:
                        home_label = f"User Home: {brand.brand_name} ({home.version_major_minor})"
                        home_item = QTreeWidgetItem([
                            home_label, brand.brand_name, home.version_major_minor,
                            "", "", brand.nre_wb_xmx or "default"
                        ])
                        home_item.setToolTip(0, brand.base_path)
                        item.addChild(home_item)

                brand_item.addChild(item)
            self.install_tree.addTopLevelItem(brand_item)
            brand_item.setExpanded(True)

    def _populate_combos(self):
        install_items = [inst.display_name for inst in self.installs]
        for cmb in [self.cmb_source, self.cmb_target]:
            cmb.clear()
            cmb.addItems(install_items)

    def _add_custom_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select Niagara Install Directory")
        if not path:
            return
        info = scan_install(path)
        if info is None:
            QMessageBox.warning(self, "Not a Niagara Install",
                                f"Could not detect a Niagara installation at:\n{path}")
            return
        if any(i.install_path == info.install_path for i in self.installs):
            QMessageBox.information(self, "Already Listed", "This install is already in the list.")
            return
        self.installs.append(info)
        self.installs.sort(key=lambda i: (i.brand, i.version))
        self._populate_install_tree()
        self._populate_combos()
        self._log(f"Added custom install: {info.display_name}")

    def _get_source(self) -> Optional[InstallInfo]:
        idx = self.cmb_source.currentIndex()
        if idx < 0 or idx >= len(self.installs):
            return None
        return self.installs[idx]

    def _get_target(self) -> Optional[InstallInfo]:
        idx = self.cmb_target.currentIndex()
        if idx < 0 or idx >= len(self.installs):
            return None
        return self.installs[idx]

    def _compare_modules(self):
        source = self._get_source()
        target = self._get_target()
        if not source or not target:
            return
        if source.install_path == target.install_path:
            QMessageBox.warning(self, "Same Install", "Source and target are the same install.")
            return

        diff = get_module_diff(source, target)

        self._log(f"Reading metadata for {len(diff['source_only'])} source-only modules...")
        read_module_metadata_for(source, diff['source_only'])
        if diff['both']:
            read_module_metadata_for(target, diff['both'][:50])
        self._log("Metadata read complete.")

        self.mod_tree.clear()
        group_by_vendor = self.chk_group_by_vendor.isChecked()
        group_by_version = self.chk_group_by_version.isChecked()

        # Build source-only items (unchecked by default)
        source_only_data: list[dict] = []
        for name in diff['source_only']:
            vendor = get_module_vendor(source, name)
            version = get_module_version(source, name) or "?"
            desc = get_module_description(source, name)
            deps = get_module_dependencies(source, name)
            deps_str = ", ".join(deps[:4]) if deps else ""
            if len(deps) > 4:
                deps_str += f" (+{len(deps)-4})"
            source_only_data.append({
                'name': name, 'vendor': vendor, 'version': version,
                'desc': desc, 'deps': deps, 'deps_str': deps_str,
                'action': 'Copy',
            })

        # Shared items
        shared_data: list[dict] = []
        for name in diff['both']:
            s_ver = get_module_version(source, name) or ""
            t_ver = get_module_version(target, name) or ""
            vendor = get_module_vendor(source, name) or get_module_vendor(target, name)
            desc = get_module_description(source, name) or get_module_description(target, name)
            version_display = s_ver if s_ver == t_ver else f"{s_ver} -> {t_ver}"
            action = "Same" if s_ver == t_ver else "Diff ver"
            shared_data.append({
                'name': name, 'vendor': vendor, 'version': version_display,
                'desc': desc, 'deps': [], 'deps_str': '',
                'action': action,
            })

        # Target-only items
        target_only_data: list[dict] = []
        for name in diff['target_only']:
            vendor = get_module_vendor(target, name)
            version = get_module_version(target, name) or "?"
            desc = get_module_description(target, name)
            target_only_data.append({
                'name': name, 'vendor': vendor, 'version': version,
                'desc': desc, 'deps': [], 'deps_str': '',
                'action': 'Target only',
            })

        if group_by_vendor:
            self._add_grouped(source_only_data, "Source Only (Copy Candidates)", group_by_version)
            self._add_grouped(shared_data, "In Both", group_by_version)
            self._add_grouped(target_only_data, "Target Only", group_by_version)
        else:
            self._add_flat(source_only_data, "Source Only (Copy Candidates)")
            self._add_flat(shared_data, "In Both")
            self._add_flat(target_only_data, "Target Only")

        self._update_mod_count()
        self._log(f"Compared {source.version} -> {target.version}: "
                   f"{len(diff['source_only'])} source-only, {len(diff['both'])} shared, "
                   f"{len(diff['target_only'])} target-only")

    def _make_leaf_item(self, data: dict) -> QTreeWidgetItem:
        """Create a leaf module item from data dict."""
        item = QTreeWidgetItem([
            data['name'], data['vendor'], data['version'],
            data['desc'], data['deps_str'], data['action']
        ])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        # Unchecked by default for all items
        item.setCheckState(0, Qt.Unchecked)

        if data['action'] == 'Copy':
            item.setForeground(5, QColor("#7b68ee"))
        elif data['action'] == 'Diff ver':
            item.setForeground(5, QColor("#e0a030"))
        elif data['action'] == 'Same':
            item.setForeground(5, QColor("#a0a0b8"))
        else:
            item.setForeground(5, QColor("#606078"))

        if data['deps']:
            item.setToolTip(4, "Dependencies: " + ", ".join(data['deps']))
        return item

    def _add_grouped(self, items_data: list[dict], section_label: str, sub_group_by_version: bool):
        """Add items grouped by vendor, optionally sub-grouped by version.
        Section/vendor/version nodes have tri-state checkboxes that propagate to children."""
        if not items_data:
            return

        # Section header (checkable, tri-state)
        section = QTreeWidgetItem([f"{section_label} ({len(items_data)})", "", "", "", "", ""])
        section.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
        section.setForeground(0, QColor("#7b68ee"))
        section.setFlags(section.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        section.setCheckState(0, Qt.Unchecked)
        self.mod_tree.addTopLevelItem(section)

        # Group by vendor
        vendor_groups: dict[str, list[dict]] = {}
        for data in items_data:
            vendor = data['vendor'] or "Unknown"
            vendor_groups.setdefault(vendor, []).append(data)

        for vendor in sorted(vendor_groups.keys()):
            vendor_items = vendor_groups[vendor]

            if sub_group_by_version:
                # Vendor node (tri-state checkbox)
                vendor_node = QTreeWidgetItem([
                    f"{vendor} ({len(vendor_items)})", "", "", "", "", ""
                ])
                vendor_node.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
                vendor_node.setForeground(0, QColor("#b8b8d0"))
                vendor_node.setFlags(vendor_node.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                vendor_node.setCheckState(0, Qt.Unchecked)
                section.addChild(vendor_node)

                # Sub-group by version
                version_groups: dict[str, list[dict]] = {}
                for data in vendor_items:
                    ver = data['version'] or "?"
                    version_groups.setdefault(ver, []).append(data)

                for ver in sorted(version_groups.keys()):
                    ver_items = version_groups[ver]
                    ver_node = QTreeWidgetItem([
                        f"v{ver} ({len(ver_items)})", "", "", "", "", ""
                    ])
                    ver_node.setFont(0, QFont("Segoe UI", 8, QFont.Normal))
                    ver_node.setForeground(0, QColor("#9090b0"))
                    ver_node.setFlags(ver_node.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                    ver_node.setCheckState(0, Qt.Unchecked)
                    vendor_node.addChild(ver_node)

                    for data in ver_items:
                        leaf = self._make_leaf_item(data)
                        ver_node.addChild(leaf)
            else:
                # Vendor node (tri-state checkbox)
                vendor_node = QTreeWidgetItem([
                    f"{vendor} ({len(vendor_items)})", "", "", "", "", ""
                ])
                vendor_node.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
                vendor_node.setForeground(0, QColor("#b8b8d0"))
                vendor_node.setFlags(vendor_node.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                vendor_node.setCheckState(0, Qt.Unchecked)
                section.addChild(vendor_node)

                for data in vendor_items:
                    leaf = self._make_leaf_item(data)
                    vendor_node.addChild(leaf)

    def _add_flat(self, items_data: list[dict], section_label: str):
        """Add items in a flat list under a tri-state section header."""
        if not items_data:
            return

        header = QTreeWidgetItem([f"{section_label} ({len(items_data)})", "", "", "", "", ""])
        header.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
        header.setForeground(0, QColor("#7b68ee"))
        header.setFlags(header.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        header.setCheckState(0, Qt.Unchecked)
        self.mod_tree.addTopLevelItem(header)

        for data in items_data:
            leaf = self._make_leaf_item(data)
            header.addChild(leaf)

    def _on_mod_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle checkbox state changes. With ItemIsAutoTristate, Qt propagates
        parent->child automatically, but we need to update the count label."""
        if self._mod_tree_updating:
            return
        self._update_mod_count()

    def _filter_modules(self):
        """Filter module tree by search text. Matches across all columns (OR logic).
        When a leaf matches, its parent groups are kept visible too."""
        filter_text = self.txt_mod_filter.text().lower().strip()
        root = self.mod_tree.invisibleRootItem()

        if not filter_text:
            for i in range(root.childCount()):
                self._set_subtree_hidden(root.child(i), False)
            return

        for i in range(root.childCount()):
            self._filter_subtree(root.child(i), filter_text)

    def _filter_subtree(self, item: QTreeWidgetItem, filter_text: str) -> bool:
        """Recursively filter tree. Returns True if this item or any descendant matches.
        Searches ALL columns of leaf items. Group/section nodes are kept visible
        if any descendant matches."""
        # Leaf = no children
        if item.childCount() == 0:
            # Search all columns
            matched = False
            for col in range(item.columnCount()):
                if filter_text in item.text(col).lower():
                    matched = True
                    break
            item.setHidden(not matched)
            return matched
        else:
            # Non-leaf: visible if any child is visible
            any_visible = False
            for i in range(item.childCount()):
                if self._filter_subtree(item.child(i), filter_text):
                    any_visible = True
            item.setHidden(not any_visible)
            return any_visible

    def _set_subtree_hidden(self, item: QTreeWidgetItem, hidden: bool):
        item.setHidden(hidden)
        for i in range(item.childCount()):
            self._set_subtree_hidden(item.child(i), hidden)

    def _select_all_modules(self, checked: bool):
        """Select/deselect all items."""
        self._mod_tree_updating = True
        root = self.mod_tree.invisibleRootItem()
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(root.childCount()):
            root.child(i).setCheckState(0, state)
        self._mod_tree_updating = False
        self._update_mod_count()

    def _select_source_only(self):
        """Select all modules in the 'Source Only' section, deselect everything else."""
        self._mod_tree_updating = True
        root = self.mod_tree.invisibleRootItem()
        for i in range(root.childCount()):
            section = root.child(i)
            is_source_only = "Source Only" in section.text(0)
            section.setCheckState(0, Qt.Checked if is_source_only else Qt.Unchecked)
        self._mod_tree_updating = False
        self._update_mod_count()

    def _update_mod_count(self):
        selected = self._get_selected_modules()
        self.lbl_mod_count.setText(f"{len(selected)} selected")

    def _get_selected_modules(self) -> list[str]:
        """Get list of module names that are checked and marked as 'Copy'.
        Only collects leaf items (no children = leaf)."""
        result = []
        root = self.mod_tree.invisibleRootItem()
        self._collect_checked(root, result)
        return result

    def _collect_checked(self, item: QTreeWidgetItem, result: list):
        # Only leaf items (childCount == 0) with "Copy" action
        if item.childCount() == 0 and (item.flags() & Qt.ItemIsUserCheckable):
            if item.checkState(0) == Qt.Checked and item.text(5) == "Copy":
                result.append(item.text(0))
        for i in range(item.childCount()):
            self._collect_checked(item.child(i), result)

    def _validate_inputs(self) -> list[str]:
        errors = []
        source = self._get_source()
        target = self._get_target()

        if not source or not target:
            errors.append("No source or target install selected")
            return errors

        if source.install_path == target.install_path:
            errors.append("Source and target are the same install")

        return errors

    def _build_operations_list(self) -> list[dict]:
        ops = []
        source = self._get_source()
        target = self._get_target()
        if not source or not target:
            return ops

        # Module migration
        selected = self._get_selected_modules()
        if selected:
            ops.append({
                'name': f"Copy {len(selected)} modules: {source.version} -> {target.version}",
                'type': 'copy_modules',
                'source_install': source,
                'target_install': target,
                'module_names': selected,
            })

        # newComponents.bog
        if self.chk_copy_bog.isChecked():
            if source.new_components_bog:
                target_bog = target.new_components_bog
                if not target_bog:
                    if target.niagara_version == 4:
                        target_bog = str(Path(target.install_path) / "defaults" / "workbench" / "newComponents.bog")
                    else:
                        target_bog = str(Path(target.install_path) / "workbench" / "newComponents.bog")
                ops.append({
                    'name': f"Copy newComponents.bog: {source.version} -> {target.version}",
                    'type': 'copy_bog',
                    'source': source.new_components_bog,
                    'target': target_bog,
                })

        # Station login XML -- derive brands from main source/target
        if self.chk_copy_xml.isChecked():
            src_brand = get_brand_for_install(source, self.user_homes)
            tgt_brand = get_brand_for_install(target, self.user_homes)

            if not src_brand:
                self._log(f"WARNING: No user home brand found for source {source.brand} {source.version}")
            if not tgt_brand:
                self._log(f"WARNING: No user home brand found for target {target.brand} {target.version}")

            if src_brand and tgt_brand:
                ops.append({
                    'name': f"Merge station login XML: {src_brand.brand_name} -> {tgt_brand.brand_name}",
                    'type': 'copy_xml',
                    'source_brand': src_brand,
                    'target_brand': tgt_brand,
                    'copy_nav_tree': self.chk_nav_tree.isChecked(),
                    'copy_recent_ords': self.chk_recent_ords.isChecked(),
                    'copy_wb_profile': self.chk_wb_profile.isChecked(),
                })

        # Security
        if self.chk_security.isChecked():
            mode = self.cmb_security.currentText()
            ops.append({
                'name': f"Set security to '{mode}': {target.version}",
                'type': 'set_security',
                'install': target,
                'mode': mode,
            })

        # RAM -- always applies to install + matching user home brands
        if self.chk_wb_ram.isChecked() or self.chk_station_ram.isChecked():
            wb_xmx = f"{self.spn_wb_ram.value()}G" if self.chk_wb_ram.isChecked() else None
            station_xmx = f"{self.spn_station_ram.value()}G" if self.chk_station_ram.isChecked() else None
            nre_path = target.nre_properties_default
            ops.append({
                'name': f"Set RAM (install): {target.version}",
                'type': 'set_ram',
                'base_path': target.install_path,
                'wb_xmx': wb_xmx,
                'station_xmx': station_xmx,
                'nre_props_path': nre_path,
            })
            # Automatically apply to matching user home brands too
            matching_homes = [h for h in self.user_homes
                              if h.version_major_minor == target.version_major_minor]
            for home in matching_homes:
                for brand in home.brands:
                    if brand.nre_properties:
                        ops.append({
            'name': f"Set RAM (user home {brand.brand_name}): {home.version_major_minor}",
                            'type': 'set_ram',
                            'base_path': brand.base_path,
                            'wb_xmx': wb_xmx,
                            'station_xmx': station_xmx,
                            'nre_props_path': brand.nre_properties,
                        })

        return ops

    def _execute(self):
        errors = self._validate_inputs()
        if errors:
            QMessageBox.critical(self, "Validation Errors", "\n".join(f"  - {e}" for e in errors))
            return

        ops = self._build_operations_list()
        if not ops:
            QMessageBox.information(self, "Nothing to do", "No operations selected.")
            return

        # Dependency warnings
        source = self._get_source()
        target = self._get_target()
        dep_warnings = {}
        selected = self._get_selected_modules()
        if selected and source and target:
            dep_warnings = check_dependency_warnings(selected, source, target)

        # Summary
        summary_lines = [f"  {i+1}. {op['name']}" for i, op in enumerate(ops)]

        dep_text = ""
        if dep_warnings:
            dep_text = "\n\nDependency warnings (missing in target):\n"
            for mod, missing in dep_warnings.items():
                dep_text += f"  {mod} needs: {', '.join(missing)}\n"
            dep_text += "\nThese dependencies will not be copied. The module may not load without them."

        reply = QMessageBox.question(
            self, "Confirm Operations",
            f"The following {len(ops)} operations will be executed:\n\n" +
            "\n".join(summary_lines) +
            f"\n\nAll existing files will be backed up before modification." +
            dep_text +
            f"\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_execute.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._log(f"\n{'='*60}")
        self._log(f"Executing {len(ops)} operations...")

        if dep_warnings:
            self._log("WARNING: Dependency issues detected:")
            for mod, missing in dep_warnings.items():
                self._log(f"  {mod} -> missing: {', '.join(missing)}")

        self.worker = WorkerThread(ops)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(
            lambda cur, tot, msg: self.progress.setValue(int(cur / tot * 100) if tot > 0 else 0)
        )
        self.worker.finished_ops.connect(self._on_ops_finished)
        self.worker.start()

    def _on_ops_finished(self, results: list):
        self.progress.setVisible(False)
        self.btn_execute.setEnabled(True)

        success_count = sum(1 for r in results if r['result'].success)
        fail_count = len(results) - success_count

        self._log(f"\n{'='*60}")
        self._log(f"Done: {success_count} succeeded, {fail_count} failed")

        if fail_count > 0:
            QMessageBox.warning(self, "Operations Complete (with errors)",
                                f"{success_count} succeeded, {fail_count} failed.\nCheck the log for details.")
        else:
            QMessageBox.information(self, "Operations Complete",
                                    f"All {success_count} operations completed successfully.")

        self._do_scan()


def _build_dark_palette() -> "QPalette":
    from PySide6.QtGui import QPalette
    p = QPalette()

    bg          = QColor("#1a1a2e")
    bg_alt      = QColor("#16213e")
    bg_hover    = QColor("#0f3460")
    text        = QColor("#e0e0e0")
    text_dim    = QColor("#a0a0b8")
    accent      = QColor("#7b68ee")
    accent_hover= QColor("#6a5acd")
    highlight   = QColor("#4a3f7a")

    p.setColor(QPalette.Window,          bg)
    p.setColor(QPalette.WindowText,      text)
    p.setColor(QPalette.Base,            bg_alt)
    p.setColor(QPalette.AlternateBase,   bg)
    p.setColor(QPalette.ToolTipBase,     bg_hover)
    p.setColor(QPalette.ToolTipText,     text)
    p.setColor(QPalette.Text,            text)
    p.setColor(QPalette.Button,          bg_hover)
    p.setColor(QPalette.ButtonText,      text)
    p.setColor(QPalette.Highlight,       highlight)
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, text_dim)
    p.setColor(QPalette.Disabled, QPalette.Text,       text_dim)
    p.setColor(QPalette.Disabled, QPalette.ButtonText, text_dim)
    p.setColor(QPalette.Link,            accent)
    p.setColor(QPalette.LinkVisited,     accent_hover)
    p.setColor(QPalette.PlaceholderText, text_dim)
    return p


DARK_STYLESHEET = """
QWidget {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-family: "Segoe UI", sans-serif;
    font-size: 9pt;
}
QGroupBox {
    border: 1px solid #2a2a4a;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
    background-color: #16213e;
    color: #b8b8d0;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #7b68ee;
}
QLabel { color: #e0e0e0; background: transparent; }
QLineEdit, QComboBox, QSpinBox {
    background-color: #0f1a2e;
    color: #e0e0e0;
    border: 1px solid #2a2a4a;
    border-radius: 4px;
    padding: 4px 8px;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #7b68ee; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: #16213e;
    color: #e0e0e0;
    selection-background-color: #4a3f7a;
    selection-color: #ffffff;
    border: 1px solid #2a2a4a;
}
QCheckBox { color: #e0e0e0; spacing: 6px; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #3a3a5a;
    border-radius: 3px;
    background-color: #0f1a2e;
}
QCheckBox::indicator:checked { background-color: #7b68ee; border-color: #7b68ee; }
QPushButton {
    background-color: #0f3460;
    color: #e0e0e0;
    border: 1px solid #2a3a5a;
    border-radius: 4px;
    padding: 6px 16px;
    min-height: 20px;
}
QPushButton:hover { background-color: #1a4a80; border-color: #7b68ee; }
QPushButton:pressed { background-color: #4a3f7a; }
QPushButton:disabled { background-color: #1a1a2e; color: #555570; border-color: #1a1a2e; }
QTreeWidget {
    background-color: #0f1a2e;
    color: #e0e0e0;
    border: 1px solid #2a2a4a;
    border-radius: 4px;
    alternate-background-color: #16213e;
}
QTreeWidget::item { padding: 3px 0; border: 0px; }
QTreeWidget::item:selected { background-color: #4a3f7a; color: #ffffff; }
QTreeWidget::item:hover { background-color: #1f2a4e; }
QHeaderView::section {
    background-color: #16213e;
    color: #b8b8d0;
    border: none;
    border-bottom: 1px solid #2a2a4a;
    padding: 4px 8px;
    font-weight: bold;
}
QTextEdit {
    background-color: #0a0a1a;
    color: #b8d0b8;
    border: 1px solid #2a2a4a;
    border-radius: 4px;
    font-family: "Consolas", "Cascadia Mono", monospace;
}
QProgressBar {
    background-color: #0f1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 4px;
    text-align: center;
    color: #e0e0e0;
}
QProgressBar::chunk { background-color: #7b68ee; border-radius: 3px; }
QScrollBar:vertical { background: #1a1a2e; width: 10px; border: none; }
QScrollBar::handle:vertical { background: #2a2a4a; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #4a3f7a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: #1a1a2e; height: 10px; border: none; }
QScrollBar::handle:horizontal { background: #2a2a4a; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background: #4a3f7a; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QToolTip {
    background-color: #0f3460;
    color: #e0e0e0;
    border: 1px solid #7b68ee;
    border-radius: 4px;
    padding: 4px;
}
QMessageBox { background-color: #1a1a2e; }
QMessageBox QLabel { color: #e0e0e0; }
"""


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_build_dark_palette())
    app.setStyleSheet(DARK_STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()