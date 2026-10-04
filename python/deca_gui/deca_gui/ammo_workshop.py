"""
Ammo Workshop: add new ammunition to theHunter: Call of the Wild without overwriting anything.

The window edits a mod file (.cotwmod.json, see cotw/construire_mod.py): a list of new ammunition, each copied
from an existing one, plus changes to existing objects (witness price...). "Build" rebuilds the mod's dropzone
from the game's original files in one pass, with the same functions as the command-line tools, so the result is
identical to `python cotw\\construire_mod.py PROJECT MOD --sortie OUTPUT`.

Every setting is the one validated in game up to test14 (research log, 2026-10-03):
  own entity (item_id, tuning_file, _object_id and .ammotunec instance renamed), stats row inserted before the
  empty rows of the ammo sheet, display name and description in string_ids.stringlookup, witness price.

Threads: the catalogue and the build run in background threads (string_ids takes about 40 s on the game file),
each with its own database connection; the GUI thread never shares one with them.
"""
import copy
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from . import cotw_tools
from .project_task import CallTask, start_task

LANGUAGES = ['en', 'fr', 'de', 'es', 'ru', 'pl', 'ja', 'pt', 'cs', 'zh']
LANGUAGE_NAMES = {'en': 'English', 'fr': 'French', 'de': 'German', 'es': 'Spanish', 'ru': 'Russian',
                  'pl': 'Polish', 'ja': 'Japanese', 'pt': 'Portuguese', 'cs': 'Czech', 'zh': 'Chinese'}
ENTITY_CHOICES = [
    ('propre', 'Own entity (recommended)'),
    ('source', 'Source entity: the weapon loads the source ammo'),
    ('copie', 'Byte copy of the source entity (diagnostic)'),
]
ALL = 'all'
INSTANCE_NAME_MAX = 31   # .ammotunec instance name: source file name cut to 31 characters (cotw/entite.py)
NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')
WINDOW_TITLE = 'Ammo Workshop'


# ---------------------------------------------------------------------------
# Logic without Qt (tested directly)
# ---------------------------------------------------------------------------

def suggest_name(source: str, taken) -> str:
    """equipment_ammo_243_sp_01 -> equipment_ammo_243_sp_mod_01 (first free number)."""
    stem = re.sub(r'_\d+$', '', source)
    n = 1
    while '{}_mod_{:02d}'.format(stem, n) in taken:
        n += 1
    return '{}_mod_{:02d}'.format(stem, n)


def new_entry(cm, source_item, taken):
    """Settings validated in game (test14): own entity, stats row copied from the source when it has one."""
    return cm.Munition(source=source_item.name, nom=suggest_name(source_item.name, taken), entite=cm.ENTITE_PROPRE,
                       stats_de=source_item.name if source_item.stats is not None else None)


def _instance_name(name: str) -> str:
    return (name + '.ammotune')[:INSTANCE_NAME_MAX]


def entry_problems(mod, index: int, catalogue, cm, sl) -> List[str]:
    """Problems that would make the build fail, found before building (empty list: nothing found)."""
    m = mod.munitions[index]
    problems = []
    earlier = {e.nom: e for e in mod.munitions[:index]}
    game = {it.name: it for it in catalogue.ammo} if catalogue is not None else {}
    if not m.nom or not NAME_RE.match(m.nom):
        problems.append('Name: ASCII letters, digits and _ only.')
    elif catalogue is not None and m.nom in catalogue.names():
        problems.append('Name: "{}" already exists in the game.'.format(m.nom))
    if [e.nom for e in mod.munitions].count(m.nom) > 1:
        problems.append('Name: used by several entries of this mod.')
    src_game, src_mod = game.get(m.source), earlier.get(m.source)
    if catalogue is not None and src_game is None and src_mod is None:
        problems.append('Source: "{}" is neither a game ammo nor an entry above this one.'.format(m.source))
    if m.entite in (cm.ENTITE_PROPRE, cm.ENTITE_COPIE):
        if src_game is not None and not src_game.entity:
            problems.append('Entity: the source has no entity in spawn_ammunition_defs.')
        if m.entite == cm.ENTITE_PROPRE and _instance_name(m.nom) == _instance_name(m.source):
            problems.append('Entity: "{}" and the source give the same .ammotunec instance name (first {} characters '
                            'of "<name>.ammotune"); change the name earlier.'.format(m.nom, INSTANCE_NAME_MAX))
    if m.stats_de:
        tpl_game, tpl_mod = game.get(m.stats_de), earlier.get(m.stats_de)
        if tpl_game is not None and tpl_game.stats is None:
            problems.append('Stats: "{}" has no row in equipment_stats_ui.'.format(m.stats_de))
        elif catalogue is not None and tpl_game is None and (tpl_mod is None or not tpl_mod.stats_de):
            problems.append('Stats: no row for "{}" (game ammo with a row, or an entry above with stats).'.format(
                m.stats_de))
    for what, specs in (('Display name', m.nom_affiche), ('Description', m.description)):
        if specs:
            try:
                sl.textes_par_langue(specs)
            except sl.EStringLookup as e:
                problems.append('{}: {}'.format(what, e))
    for spec in m.champs:
        if '=' not in spec or not spec.split('=', 1)[0].strip():
            problems.append('Fields: "{}" is not FIELD=VALUE.'.format(spec))
    return problems


def mod_problems(mod, catalogue, cm, sl) -> Dict[int, List[str]]:
    return {i: p for i in range(len(mod.munitions)) for p in [entry_problems(mod, i, catalogue, cm, sl)] if p}


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class AmmoTableModel(QAbstractTableModel):
    COLUMNS = ['Name', 'Display name', 'Price', 'Availability', 'Stats row', 'Weapons']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []

    def set_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return it.name
            if col == 1:
                return it.display_name or ''
            if col == 2:
                return '' if it.price is None else it.price
            if col == 3:
                return it.availability
            if col == 4:
                if it.stats is None:
                    return 'none'
                return 'yes' if it.stats_read_by_game else 'after empty row'
            if col == 5:
                return ', '.join(it.weapons)
        if role == Qt.ToolTipRole:
            return '{}\n{}'.format(it.display_name or it.name, it.description or '')
        if role == Qt.UserRole:
            return it
        if role == Qt.TextAlignmentRole and col == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class TextsEditor(QWidget):
    """Text for all languages, plus optional per-language overrides (10 languages of string_ids)."""
    changed = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.all_edit = QLineEdit()
        self.all_edit.setPlaceholderText('Empty: keep the source\'s text')
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet('color: gray')
        self.table = QTableWidget(len(LANGUAGES), 2)
        self.table.setHorizontalHeaderLabels(['Language', 'Override (empty: text for all languages)'])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, code in enumerate(LANGUAGES):
            item = QTableWidgetItem('{} ({})'.format(LANGUAGE_NAMES[code], code))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(''))
        box = QGroupBox(title)
        form = QFormLayout()
        form.addRow('All languages:', self.all_edit)
        form.addRow(self.hint)
        form.addRow(self.table)
        box.setLayout(form)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        self.all_edit.textChanged.connect(self.changed)
        self.table.itemChanged.connect(lambda _item: self.changed.emit())

    def set_texts(self, texts: Dict[str, str]):
        self.blockSignals(True)
        self.table.blockSignals(True)
        self.all_edit.setText(texts.get(ALL, ''))
        for row, code in enumerate(LANGUAGES):
            self.table.item(row, 1).setText(texts.get(code, ''))
        self.table.blockSignals(False)
        self.blockSignals(False)

    def texts(self) -> Dict[str, str]:
        out = {}
        if self.all_edit.text():
            out[ALL] = self.all_edit.text()
        for row, code in enumerate(LANGUAGES):
            t = self.table.item(row, 1).text()
            if t:
                out[code] = t
        return out


class AmmoWorkshop(QMainWindow):
    """Top-level window, opened from Tools > Ammo Workshop of the main window, bound to one DECA project."""

    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.project_dir = str(Path(project_dir))
        (self.cmod, self.cm, self.kat, self._projet, self.sl, self.xls) = cotw_tools.modules()
        self.mf = self.cmod.ModFile(self.cm.Mod([]), name='new_mod')
        self.mod_path: Optional[str] = None
        self.dirty = False
        self.catalogue = None
        self.language = 'en'
        self._loading = False
        self._task = None
        self._thread = None
        self._output_is_default = True
        self._stats_keys: Dict[int, str] = {}       # column -> key used in the mod file for that column
        self._stats_unresolved: Dict[str, str] = {}  # keys of the mod file that match no column (kept as they are)
        self.last_report: List[str] = []

        self._build_ui()
        self._set_output(os.path.join(cotw_tools.default_mods_dir(), self.mf.name))
        self._refresh_mod_panel()
        self._update_title()
        self.load_catalogue()

    # ----- dialogs (replaced by the tests) -----

    def error_dialog(self, text: str):
        QMessageBox.critical(self, WINDOW_TITLE, text)

    def info_dialog(self, text: str):
        QMessageBox.information(self, WINDOW_TITLE, text)

    def ask_save_changes(self) -> str:
        """'save', 'discard' or 'cancel'."""
        answer = QMessageBox.question(
            self, WINDOW_TITLE, 'The mod "{}" has unsaved changes. Save them?'.format(self.mf.name or 'new mod'),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return {QMessageBox.Save: 'save', QMessageBox.Discard: 'discard'}.get(answer, 'cancel')

    def ask_open_path(self) -> Optional[str]:
        path, _ = QFileDialog.getOpenFileName(self, 'Open mod', self._mods_dir(), 'COTWed mod (*.cotwmod.json *.json)')
        return path or None

    def ask_save_path(self) -> Optional[str]:
        start = os.path.join(self._mods_dir(), (self.mf.name or 'mod') + self.cmod.SUFFIX)
        path, _ = QFileDialog.getSaveFileName(self, 'Save mod', start, 'COTWed mod (*.cotwmod.json)')
        if not path:
            return None
        return path if path.endswith(self.cmod.SUFFIX) else path + (self.cmod.SUFFIX if not path.endswith('.json') else '')

    def _mods_dir(self) -> str:
        if self.mod_path:
            return os.path.dirname(self.mod_path)
        return cotw_tools.default_mods_dir()

    # ----- layout -----

    def _build_ui(self):
        self.resize(1500, 900)
        tb = QToolBar('Mod')
        tb.setMovable(False)
        self.addToolBar(tb)
        self.action_new = QAction('&New mod', self, shortcut=QKeySequence.New, triggered=self.mod_new)
        self.action_open = QAction('&Open mod...', self, shortcut=QKeySequence.Open, triggered=self.mod_open)
        self.action_save = QAction('&Save', self, shortcut=QKeySequence.Save, triggered=self.mod_save)
        self.action_save_as = QAction('Save &as...', self, shortcut=QKeySequence.SaveAs, triggered=self.mod_save_as)
        for a in (self.action_new, self.action_open, self.action_save, self.action_save_as):
            tb.addAction(a)
        tb.addSeparator()
        tb.addWidget(QLabel(' Game texts: '))
        self.language_combo = QComboBox()
        for code in LANGUAGES:
            self.language_combo.addItem('{} ({})'.format(LANGUAGE_NAMES[code], code), code)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        tb.addWidget(self.language_combo)

        # left: game ammunition
        self.catalogue_model = AmmoTableModel(self)
        self.catalogue_proxy = QSortFilterProxyModel(self)
        self.catalogue_proxy.setSourceModel(self.catalogue_model)
        self.catalogue_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.catalogue_proxy.setFilterKeyColumn(-1)
        self.catalogue_view = QTableView()
        self.catalogue_view.setModel(self.catalogue_proxy)
        self.catalogue_view.setSortingEnabled(True)
        self.catalogue_view.sortByColumn(0, Qt.AscendingOrder)
        self.catalogue_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.catalogue_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.catalogue_view.verticalHeader().setVisible(False)
        self.catalogue_view.horizontalHeader().setStretchLastSection(True)
        self.catalogue_view.doubleClicked.connect(lambda _i: self.add_from_selected())
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText('Filter (name, display name, weapons...)')
        self.filter_edit.textChanged.connect(self.catalogue_proxy.setFilterFixedString)
        self.add_button = QPushButton('Copy into the mod  →')
        self.add_button.setToolTip('New ammo copied from the selected one (double-click does the same)')
        self.add_button.clicked.connect(self.add_from_selected)
        self.catalogue_status = QLabel('Loading the game ammunition...')
        self.catalogue_status.setWordWrap(True)
        left = QGroupBox('Game ammunition (original files)')
        lv = QVBoxLayout(left)
        lv.addWidget(self.filter_edit)
        lv.addWidget(self.catalogue_view)
        lv.addWidget(self.catalogue_status)
        lv.addWidget(self.add_button)

        # middle: the mod
        self.mod_name_edit = QLineEdit()
        self.mod_name_edit.textChanged.connect(self._mod_name_changed)
        self.mod_notes_edit = QPlainTextEdit()
        self.mod_notes_edit.setMaximumHeight(60)
        self.mod_notes_edit.textChanged.connect(self._mod_notes_changed)
        self.entry_list = QListWidget()
        self.entry_list.currentRowChanged.connect(self._entry_selected)
        self.duplicate_button = QPushButton('Duplicate')
        self.duplicate_button.clicked.connect(self.duplicate_entry)
        self.remove_button = QPushButton('Remove')
        self.remove_button.clicked.connect(self.remove_entry)
        self.up_button = QPushButton('Up')
        self.up_button.clicked.connect(lambda: self.move_entry(-1))
        self.down_button = QPushButton('Down')
        self.down_button.clicked.connect(lambda: self.move_entry(1))
        buttons = QHBoxLayout()
        for b in (self.duplicate_button, self.remove_button, self.up_button, self.down_button):
            buttons.addWidget(b)
        self.witness_table = self._small_table(['Item', 'Price'])
        self.witness_table.itemChanged.connect(lambda _i: self._tables_to_mod())
        self.witness_add = QPushButton('Add witness price')
        self.witness_add.setToolTip('Changes the price of an existing item to a recognisable value: if it shows in '
                                    'the store, the game read the mod\'s files')
        self.witness_add.clicked.connect(self.add_witness)
        self.witness_remove = QPushButton('Remove')
        self.witness_remove.clicked.connect(lambda: self._remove_table_row(self.witness_table))
        self.changes_table = self._small_table(['Item', 'Field', 'Value'])
        self.changes_table.itemChanged.connect(lambda _i: self._tables_to_mod())
        self.changes_add = QPushButton('Add change')
        self.changes_add.setToolTip('Changes a field of an existing item (e.g. locked = 5 hides it from the store)')
        self.changes_add.clicked.connect(self.add_change)
        self.changes_remove = QPushButton('Remove')
        self.changes_remove.clicked.connect(lambda: self._remove_table_row(self.changes_table))
        middle = QGroupBox('Mod')
        mv = QVBoxLayout(middle)
        mf = QFormLayout()
        mf.addRow('Name:', self.mod_name_edit)
        mf.addRow('Notes:', self.mod_notes_edit)
        mv.addLayout(mf)
        mv.addWidget(QLabel('New ammunition (added in this order):'))
        mv.addWidget(self.entry_list, 3)
        mv.addLayout(buttons)
        mv.addWidget(QLabel('Witness prices (existing items):'))
        mv.addWidget(self.witness_table, 1)
        wb = QHBoxLayout()
        wb.addWidget(self.witness_add)
        wb.addWidget(self.witness_remove)
        mv.addLayout(wb)
        mv.addWidget(QLabel('Changes to existing items:'))
        mv.addWidget(self.changes_table, 1)
        cb = QHBoxLayout()
        cb.addWidget(self.changes_add)
        cb.addWidget(self.changes_remove)
        mv.addLayout(cb)

        # right: entry editor
        self.editor_tabs = QTabWidget()
        self.editor_tabs.addTab(self._general_tab(), 'General')
        self.editor_tabs.addTab(self._weapons_tab(), 'Weapons')
        self.editor_tabs.addTab(self._stats_tab(), 'Stats')
        self.editor_tabs.addTab(self._texts_tab(), 'Texts')
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.problems_label.setStyleSheet('color: #b00020')
        right = QGroupBox('Selected new ammo')
        rv = QVBoxLayout(right)
        rv.addWidget(self.editor_tabs)
        rv.addWidget(self.problems_label)
        self.editor_box = right

        top = QSplitter(Qt.Horizontal)
        top.addWidget(left)
        top.addWidget(middle)
        top.addWidget(right)
        top.setSizes([520, 380, 600])

        # bottom: output, build, report
        self.output_edit = QLineEdit()
        self.output_edit.textEdited.connect(self._output_edited)
        browse = QPushButton('Browse...')
        browse.clicked.connect(self._browse_output)
        self.overwrite_check = QCheckBox('Replace files not written by the previous build')
        self.build_button = QPushButton('Build')
        self.build_button.setDefault(True)
        self.build_button.clicked.connect(self.build)
        self.command_button = QPushButton('Copy command line')
        self.command_button.setToolTip('Equivalent command for the research log (needs the mod to be saved)')
        self.command_button.clicked.connect(self.copy_command_line)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(140)
        self.busy.setTextVisible(False)
        self.busy.hide()
        self.status_label = QLabel()
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel('Output folder (dropzone inside):'))
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(browse)
        out_row.addWidget(self.overwrite_check)
        build_row = QHBoxLayout()
        build_row.addWidget(self.build_button)
        build_row.addWidget(self.command_button)
        build_row.addWidget(self.busy)
        build_row.addWidget(self.status_label, 1)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont('Courier', 8)
        font.setStyleHint(QFont.Monospace)
        self.report_view.setFont(font)
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.addLayout(out_row)
        bv.addLayout(build_row)
        bv.addWidget(self.report_view)

        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        main.addWidget(bottom)
        main.setSizes([620, 260])
        self.setCentralWidget(main)

    @staticmethod
    def _small_table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        return t

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.name_edit = QLineEdit()
        self.name_edit.setToolTip('Internal name: ASCII letters, digits and _ ; becomes the hash of the new item')
        self.source_combo = QComboBox()
        self.source_label = QLabel()
        self.source_label.setStyleSheet('color: gray')
        self.price_check = QCheckBox('Set the store price')
        self.price_spin = QSpinBox()
        self.price_spin.setRange(0, 99999999)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText('Empty: keep the source\'s label (property note, English)')
        self.entity_combo = QComboBox()
        for key, text in ENTITY_CHOICES:
            self.entity_combo.addItem(text, key)
        self.fields_table = self._small_table(['Field', 'Value'])
        self.fields_add = QPushButton('Add field')
        self.fields_add.clicked.connect(lambda: self._add_table_row(self.fields_table, ['locked', '']))
        self.fields_remove = QPushButton('Remove')
        self.fields_remove.clicked.connect(lambda: self._remove_table_row(self.fields_table))
        price_row = QHBoxLayout()
        price_row.addWidget(self.price_check)
        price_row.addWidget(self.price_spin, 1)
        fb = QHBoxLayout()
        fb.addWidget(self.fields_add)
        fb.addWidget(self.fields_remove)
        form.addRow('Name:', self.name_edit)
        form.addRow('Copied from:', self.source_combo)
        form.addRow('', self.source_label)
        form.addRow('Price:', price_row)
        form.addRow('Label:', self.label_edit)
        form.addRow('Entity (.ee):', self.entity_combo)
        form.addRow(QLabel('Other fields of the new ammo (name or 0x hash, keeps the field\'s type):'))
        form.addRow(self.fields_table)
        form.addRow(fb)
        for sig in (self.name_edit.textChanged, self.label_edit.textChanged):
            sig.connect(self._editor_changed)
        self.source_combo.currentIndexChanged.connect(self._editor_changed)
        self.price_check.toggled.connect(self._editor_changed)
        self.price_check.toggled.connect(self.price_spin.setEnabled)
        self.price_spin.valueChanged.connect(self._editor_changed)
        self.entity_combo.currentIndexChanged.connect(self._editor_changed)
        self.fields_table.itemChanged.connect(lambda _i: self._editor_changed())
        return w

    def _weapons_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.same_weapons_check = QCheckBox('Same weapons as the source')
        self.weapons_list = QListWidget()
        self.force_check = QCheckBox('Allow weapons missing from the weapons category')
        v.addWidget(self.same_weapons_check)
        v.addWidget(self.weapons_list)
        v.addWidget(self.force_check)
        note = QLabel('Compatibility is declared on the ammo side only: the weapons are not modified. The i-th '
                      'weapon gets the key compatibility_i, as in the game.')
        note.setWordWrap(True)
        note.setStyleSheet('color: gray')
        v.addWidget(note)
        self.same_weapons_check.toggled.connect(self._same_weapons_toggled)
        self.weapons_list.itemChanged.connect(self._weapon_toggled)
        self.force_check.toggled.connect(self._editor_changed)
        self._weapon_order: List[str] = []
        return w

    def _stats_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.stats_check = QCheckBox('Add a row to equipment_stats_ui (stats shown in the store, ownership logo)')
        self.stats_template_combo = QComboBox()
        self.stats_table = QTableWidget(0, 3)
        self.stats_table.setHorizontalHeaderLabels(['Column', 'Copied value', 'New value (empty: keep)'])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.stats_table.horizontalHeader().setStretchLastSection(True)
        self.own_cells_check = QCheckBox('Own cells for every column (by default, unchanged columns share the '
                                         'copied row\'s cells, as the game does)')
        self.stats_note = QLabel()
        self.stats_note.setWordWrap(True)
        self.stats_note.setStyleSheet('color: gray')
        row = QHBoxLayout()
        row.addWidget(QLabel('Copy the row of:'))
        row.addWidget(self.stats_template_combo, 1)
        v.addWidget(self.stats_check)
        v.addLayout(row)
        v.addWidget(self.stats_table)
        v.addWidget(self.own_cells_check)
        v.addWidget(self.stats_note)
        self.stats_check.toggled.connect(self._editor_changed)
        self.stats_template_combo.currentIndexChanged.connect(self._stats_template_changed)
        self.stats_table.itemChanged.connect(lambda _i: self._editor_changed())
        self.own_cells_check.toggled.connect(self._editor_changed)
        return w

    def _texts_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.name_texts = TextsEditor('Display name')
        self.desc_texts = TextsEditor('Description')
        note = QLabel('Written to string_ids.stringlookup under the keys <name>_name and <name>_description. The game '
                      'stores names in capitals: type them in capitals for a consistent look. Empty: the new ammo '
                      'shows the source\'s texts.')
        note.setWordWrap(True)
        note.setStyleSheet('color: gray')
        v.addWidget(self.name_texts)
        v.addWidget(self.desc_texts)
        v.addWidget(note)
        self.name_texts.changed.connect(self._editor_changed)
        self.desc_texts.changed.connect(self._editor_changed)
        return w

    # ----- catalogue -----

    def task_running(self) -> bool:
        return self._task is not None

    def _start(self, func, on_success, label: str, expected=()):
        self._task = CallTask(func, expected)
        self._task.message.connect(lambda t: self.status_label.setText('{}: {}'.format(label, t)))
        self._task.succeeded.connect(on_success)
        self._task.failed.connect(lambda reason: self._task_failed(label, reason))
        self._task.done.connect(self._task_done)
        self.status_label.setText(label + '...')
        self.busy.show()
        self._update_actions()
        self._thread = start_task(self._task, self)

    def _task_failed(self, label: str, reason: str):
        self.status_label.setText(label + ': failed')
        self.report_view.setPlainText(reason)
        self.error_dialog('{} failed.\n\n{}'.format(label, reason.strip().splitlines()[-1] if reason.strip() else ''))

    def _task_done(self):
        self._task = None
        self._thread = None
        self.busy.hide()
        self._update_actions()
        if self.catalogue is not None and self.catalogue.language != self.language:
            self.load_catalogue()   # language changed while another task was running

    def load_catalogue(self):
        project, language, kat, projet = self.project_dir, self.language, self.kat, self._projet

        def work(progress):
            vfs = projet.open_project(project)
            try:
                return kat.charger(vfs, language, progress)
            finally:
                vfs.db_conn.close()

        self._start(work, self._catalogue_loaded, 'Reading the game ammunition', (kat.ECatalogue,))

    def _catalogue_loaded(self, catalogue):
        self.catalogue = catalogue
        self.catalogue_model.set_items(catalogue.ammo)
        self.catalogue_view.resizeColumnsToContents()
        text = '{} ammunition, {} weapons ({} texts).'.format(len(catalogue.ammo), len(catalogue.weapons),
                                                             LANGUAGE_NAMES[catalogue.language])
        if catalogue.warnings:
            text += '\n' + '\n'.join(catalogue.warnings)
        self.catalogue_status.setText(text)
        self.status_label.setText('Ready.')
        self._fill_combos()
        self._fill_weapons()
        self._refresh_mod_panel()

    def _language_changed(self, _index):
        code = self.language_combo.currentData()
        if code == self.language:
            return
        self.language = code
        if self.task_running():
            return   # reloaded when the current task ends (see _task_done)
        self.load_catalogue()

    # ----- mod panel -----

    def _entries(self):
        return self.mf.mod.munitions

    def current_index(self) -> int:
        return self.entry_list.currentRow()

    def current_entry(self):
        i = self.current_index()
        return self._entries()[i] if 0 <= i < len(self._entries()) else None

    def _entry_text(self, i: int, m) -> str:
        return '{}  ·  {}'.format(m.nom, 'source price' if m.prix is None else m.prix)

    def _refresh_mod_panel(self, select: Optional[int] = None):
        self._loading = True
        try:
            self.mod_name_edit.setText(self.mf.name)
            if self.mod_notes_edit.toPlainText() != self.mf.notes:
                self.mod_notes_edit.setPlainText(self.mf.notes)
            row = self.current_index() if select is None else select
            self.entry_list.clear()
            for i, m in enumerate(self._entries()):
                self.entry_list.addItem(QListWidgetItem(self._entry_text(i, m)))
            self._fill_small_tables()
        finally:
            self._loading = False
        n = len(self._entries())
        if n:
            self.entry_list.setCurrentRow(min(max(row, 0), n - 1))
        else:
            self._entry_selected(-1)
        self._refresh_problems()
        self._update_actions()

    def _fill_small_tables(self):
        for table, rows in ((self.witness_table, [spec.partition('=')[::2] for spec in self.mf.mod.controle_prix]),
                            (self.changes_table, [self.cm.parse_field_spec(s, True) for s in self.mf.mod.modifier])):
            table.blockSignals(True)
            table.setRowCount(0)
            for values in rows:
                r = table.rowCount()
                table.insertRow(r)
                for c, v in enumerate(values):
                    table.setItem(r, c, QTableWidgetItem(str(v)))
            table.blockSignals(False)

    def _tables_to_mod(self):
        if self._loading:
            return
        witness, changes = [], []
        for r in range(self.witness_table.rowCount()):
            item, price = (self._cell(self.witness_table, r, c) for c in range(2))
            if item or price:
                witness.append('{}={}'.format(item, price))
        for r in range(self.changes_table.rowCount()):
            item, fld, value = (self._cell(self.changes_table, r, c) for c in range(3))
            if item or fld or value:
                changes.append('{}:{}={}'.format(item, fld, value))
        self.mf.mod.controle_prix, self.mf.mod.modifier = witness, changes
        self._set_dirty()

    @staticmethod
    def _cell(table, r, c) -> str:
        item = table.item(r, c)
        return item.text().strip() if item is not None else ''

    def _add_table_row(self, table, values):
        table.blockSignals(True)
        r = table.rowCount()
        table.insertRow(r)
        for c, v in enumerate(values):
            table.setItem(r, c, QTableWidgetItem(str(v)))
        table.blockSignals(False)
        table.setCurrentCell(r, len(values) - 1)
        table.itemChanged.emit(table.item(r, 0))

    def _remove_table_row(self, table):
        r = table.currentRow()
        if r < 0:
            return
        table.removeRow(r)
        table.itemChanged.emit(None)

    def selected_game_item(self):
        rows = self.catalogue_view.selectionModel().selectedRows() if self.catalogue_view.selectionModel() else []
        if not rows:
            return None
        return self.catalogue_proxy.data(rows[0], Qt.UserRole)

    def select_game_item(self, name: str) -> bool:
        for r in range(self.catalogue_proxy.rowCount()):
            idx = self.catalogue_proxy.index(r, 0)
            if self.catalogue_proxy.data(idx) == name:
                self.catalogue_view.selectRow(r)
                return True
        return False

    def add_witness(self):
        item = self.selected_game_item()
        self._add_table_row(self.witness_table, [item.name if item else '', 5432])

    def add_change(self):
        item = self.selected_game_item()
        self._add_table_row(self.changes_table, [item.name if item else '', 'locked', ''])

    def _mod_name_changed(self, text):
        if self._loading:
            return
        self.mf.name = text
        if self._output_is_default:
            self._set_output(os.path.join(cotw_tools.default_mods_dir(), text or 'mod'))
        self._set_dirty()

    def _mod_notes_changed(self):
        if self._loading:
            return
        self.mf.notes = self.mod_notes_edit.toPlainText()
        self._set_dirty()

    def add_from_selected(self):
        item = self.selected_game_item()
        if item is None or self.catalogue is None:
            self.error_dialog('Select an ammunition of the game first.')
            return
        taken = self.catalogue.names() | {m.nom for m in self._entries()}
        self._entries().append(new_entry(self.cm, item, taken))
        self._set_dirty()
        self._refresh_mod_panel(select=len(self._entries()) - 1)
        self.editor_tabs.setCurrentIndex(0)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def duplicate_entry(self):
        m = self.current_entry()
        if m is None:
            return
        taken = (self.catalogue.names() if self.catalogue else set()) | {e.nom for e in self._entries()}
        dup = copy.deepcopy(m)
        dup.nom = suggest_name(m.source, taken)
        self._entries().insert(self.current_index() + 1, dup)
        self._set_dirty()
        self._refresh_mod_panel(select=self.current_index() + 1)

    def remove_entry(self):
        i = self.current_index()
        if i < 0:
            return
        del self._entries()[i]
        self._set_dirty()
        self._refresh_mod_panel(select=min(i, len(self._entries()) - 1))

    def move_entry(self, delta: int):
        i = self.current_index()
        j = i + delta
        if i < 0 or not 0 <= j < len(self._entries()):
            return
        e = self._entries()
        e[i], e[j] = e[j], e[i]
        self._set_dirty()
        self._refresh_mod_panel(select=j)

    # ----- entry editor -----

    def _fill_combos(self):
        self._loading = True
        try:
            for combo in (self.source_combo, self.stats_template_combo):
                combo.clear()
            if self.catalogue is not None:
                for it in sorted(self.catalogue.ammo, key=lambda x: x.name):
                    label = '{}  ({})'.format(it.name, it.display_name) if it.display_name else it.name
                    self.source_combo.addItem(label, it.name)
                    if it.stats is not None:
                        self.stats_template_combo.addItem(label, it.name)
        finally:
            self._loading = False

    def _fill_weapons(self):
        self._loading = True
        try:
            self.weapons_list.clear()
            self._weapon_order = []
            if self.catalogue is not None:
                for w in self.catalogue.weapons:
                    item = QListWidgetItem('{}  ({})'.format(w.display_name, w.name) if w.display_name else w.name)
                    item.setData(Qt.UserRole, w.name)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Unchecked)
                    self.weapons_list.addItem(item)
        finally:
            self._loading = False

    @staticmethod
    def _combo_select(combo: QComboBox, value: Optional[str], extra_label: str = '(not in the game)'):
        """Select ``value``; a value absent from the game (entry of the mod, unknown name of a mod file) gets a
        temporary item, removed the next time."""
        for i in range(combo.count() - 1, -1, -1):
            if combo.itemData(i, Qt.UserRole + 1):
                combo.removeItem(i)
        if value is None:
            return
        i = combo.findData(value)
        if i < 0:
            combo.addItem('{}  {}'.format(value, extra_label), value)
            i = combo.count() - 1
            combo.setItemData(i, True, Qt.UserRole + 1)
        combo.setCurrentIndex(i)

    def _entry_selected(self, row: int):
        m = self.current_entry() if row >= 0 else None
        self.editor_box.setEnabled(m is not None)
        if m is None:
            self.problems_label.setText('')
            return
        self._loading = True
        try:
            self.name_edit.setText(m.nom)
            self._combo_select(self.source_combo, m.source, '(entry of this mod)')
            self._update_source_label(m)
            self.price_check.setChecked(m.prix is not None)
            self.price_spin.setEnabled(m.prix is not None)
            src = self.catalogue.find(m.source) if self.catalogue else None
            self.price_spin.setValue(m.prix if m.prix is not None else (src.price or 0 if src else 0))
            self.label_edit.setText(m.libelle or '')
            self.entity_combo.setCurrentIndex(max(0, self.entity_combo.findData(m.entite)))
            self.fields_table.setRowCount(0)
            for spec in m.champs:
                f, _, v = spec.partition('=')
                r = self.fields_table.rowCount()
                self.fields_table.insertRow(r)
                self.fields_table.setItem(r, 0, QTableWidgetItem(f.strip()))
                self.fields_table.setItem(r, 1, QTableWidgetItem(v.strip()))
            self._show_weapons(m)
            self.force_check.setChecked(m.forcer)
            self.stats_check.setChecked(m.stats_de is not None)
            self._combo_select(self.stats_template_combo, m.stats_de or m.source, '(entry of this mod)')
            self._show_stats(m)
            self.own_cells_check.setChecked(m.stats_cellules_propres)
            self.name_texts.set_texts(self.cmod.specs_to_texts(m.nom_affiche))
            self.desc_texts.set_texts(self.cmod.specs_to_texts(m.description))
            self._update_text_hints(m)
        finally:
            self._loading = False
        self._refresh_problems()

    def _update_source_label(self, m):
        src = self.catalogue.find(m.source) if self.catalogue else None
        if src is None:
            self.source_label.setText('')
            return
        self.source_label.setText('{} · price {} · {} · entity {}'.format(
            src.display_name or src.name, src.price, src.availability, 'yes' if src.entity else 'none'))

    def _update_text_hints(self, m):
        src = self.catalogue.find(m.source) if self.catalogue else None
        lang = LANGUAGE_NAMES[self.language]
        self.name_texts.hint.setText('Source ({}): {}'.format(lang, src.display_name) if src and src.display_name else '')
        self.desc_texts.hint.setText('Source ({}): {}'.format(lang, src.description) if src and src.description else '')

    def _show_weapons(self, m):
        same = m.armes is None
        src = self.catalogue.find(m.source) if self.catalogue else None
        checked = list(src.weapons if (same and src) else (m.armes or []))
        self._weapon_order = list(checked)
        known = {self.weapons_list.item(r).data(Qt.UserRole) for r in range(self.weapons_list.count())}
        for r in range(self.weapons_list.count() - 1, -1, -1):   # extra rows of a previous entry
            if self.weapons_list.item(r).data(Qt.UserRole + 1):
                self.weapons_list.takeItem(r)
        for name in checked:
            if name not in known:
                item = QListWidgetItem('{}  (not in the weapons category)'.format(name))
                item.setData(Qt.UserRole, name)
                item.setData(Qt.UserRole + 1, True)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                self.weapons_list.addItem(item)
        for r in range(self.weapons_list.count()):
            item = self.weapons_list.item(r)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in checked else Qt.Unchecked)
        self.same_weapons_check.setChecked(same)
        self.weapons_list.setEnabled(not same)

    def _same_weapons_toggled(self, same: bool):
        self.weapons_list.setEnabled(not same)
        if self._loading:
            return
        m = self.current_entry()
        if m is not None and not same and m.armes is None:
            src = self.catalogue.find(m.source) if self.catalogue else None
            self._weapon_order = list(src.weapons if src else [])
        self._editor_changed()

    def _weapon_toggled(self, item):
        if self._loading:
            return
        name = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked and name not in self._weapon_order:
            self._weapon_order.append(name)
        elif item.checkState() != Qt.Checked and name in self._weapon_order:
            self._weapon_order.remove(name)
        self._editor_changed()

    def _template_values(self, name: Optional[str]):
        if name is None or self.catalogue is None:
            return None
        it = self.catalogue.find(name)
        if it is not None:
            return it.stats
        for m in self._entries():   # entry of the mod: values of its own template, then its overrides
            if m.nom == name and m.stats_de and m.stats_de != name:
                base = self._template_values(m.stats_de)
                if base is None:
                    return None
                base = list(base)
                base[0] = name
                for key, value in m.stats.items():
                    try:
                        base[self.xls.resolve_column(self.catalogue.stats_header, key)] = value
                    except self.xls.EXls:
                        pass
                return base
        return None

    def _show_stats(self, m):
        header = self.catalogue.stats_header if self.catalogue else []
        self._stats_keys, self._stats_unresolved = {}, {}
        new_values: Dict[int, str] = {}
        for key, value in m.stats.items():
            try:
                col = self.xls.resolve_column(header, key)
            except self.xls.EXls:
                self._stats_unresolved[key] = value
                continue
            self._stats_keys[col] = key
            new_values[col] = value
        template = self._template_values(self.stats_template_combo.currentData())
        self.stats_table.blockSignals(True)
        self.stats_table.setRowCount(0)
        for col in range(1, len(header)):
            r = self.stats_table.rowCount()
            self.stats_table.insertRow(r)
            name = header[col] if isinstance(header[col], str) else '#{} (no name)'.format(col)
            for c, text in enumerate((name, '' if template is None else str(template[col]), new_values.get(col, ''))):
                item = QTableWidgetItem(text)
                if c < 2:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.stats_table.setItem(r, c, item)
        self.stats_table.blockSignals(False)
        notes = []
        if self._stats_unresolved:
            notes.append('Kept from the mod file, no matching column: {}'.format(
                ', '.join('{}={}'.format(k, v) for k, v in self._stats_unresolved.items())))
        notes.append('The new row is inserted before the empty rows of the ammo sheet: the game stops reading stats '
                     'at the first empty row (test12 H).')
        self.stats_note.setText('\n'.join(notes))
        enabled = m.stats_de is not None
        for w in (self.stats_template_combo, self.stats_table, self.own_cells_check):
            w.setEnabled(enabled)

    def _stats_template_changed(self, _index):
        if self._loading:
            return
        m = self.current_entry()
        if m is not None:
            self._editor_to_entry(m)
            self._loading = True
            try:
                self._show_stats(m)
            finally:
                self._loading = False
        self._editor_changed()

    def _editor_to_entry(self, m):
        m.nom = self.name_edit.text().strip()
        m.source = self.source_combo.currentData() or m.source
        m.prix = self.price_spin.value() if self.price_check.isChecked() else None
        m.libelle = self.label_edit.text() or None
        m.entite = self.entity_combo.currentData()
        m.champs = ['{}={}'.format(self._cell(self.fields_table, r, 0), self._cell(self.fields_table, r, 1))
                    for r in range(self.fields_table.rowCount()) if self._cell(self.fields_table, r, 0)]
        m.armes = None if self.same_weapons_check.isChecked() else list(self._weapon_order)
        m.forcer = self.force_check.isChecked()
        if self.stats_check.isChecked():
            m.stats_de = self.stats_template_combo.currentData() or m.source
            header = self.catalogue.stats_header if self.catalogue else []
            stats = dict(self._stats_unresolved)
            for r in range(self.stats_table.rowCount()):
                col = r + 1
                value = self._cell(self.stats_table, r, 2)
                if not value:
                    continue
                key = self._stats_keys.get(col) or (header[col] if isinstance(header[col], str) else str(col))
                stats[key] = value
            m.stats = stats
            m.stats_cellules_propres = self.own_cells_check.isChecked()
        else:
            m.stats_de, m.stats, m.stats_cellules_propres = None, {}, False
        m.nom_affiche = self.cmod.texts_to_specs(self.name_texts.texts())
        m.description = self.cmod.texts_to_specs(self.desc_texts.texts())

    def _editor_changed(self, *_args):
        if self._loading:
            return
        m = self.current_entry()
        if m is None:
            return
        old_source = m.source
        self._editor_to_entry(m)
        if m.source != old_source:
            self._update_source_label(m)
            self._update_text_hints(m)
            if m.armes is None:
                self._loading = True
                try:
                    self._show_weapons(m)
                finally:
                    self._loading = False
        for w in (self.stats_template_combo, self.stats_table, self.own_cells_check):
            w.setEnabled(m.stats_de is not None)
        item = self.entry_list.item(self.current_index())
        if item is not None:
            item.setText(self._entry_text(self.current_index(), m))
        self._set_dirty()
        self._refresh_problems()

    def _refresh_problems(self):
        problems = mod_problems(self.mf.mod, self.catalogue, self.cm, self.sl)
        for i in range(self.entry_list.count()):
            item = self.entry_list.item(i)
            item.setForeground(QBrush(QColor('#b00020')) if i in problems else QBrush())
            item.setToolTip('\n'.join(['Copy of {}'.format(self._entries()[i].source)] + problems.get(i, [])))
        self.problems_label.setText('\n'.join(problems.get(self.current_index(), [])))
        self._problems = problems
        self._update_actions()

    # ----- file -----

    def _set_dirty(self, dirty: bool = True):
        if self._loading:
            return
        self.dirty = dirty
        self._update_title()

    def _update_title(self):
        name = self.mf.name or 'new mod'
        file = os.path.basename(self.mod_path) if self.mod_path else 'not saved'
        self.setWindowTitle('{} — {}{} ({}) — {}'.format(WINDOW_TITLE, name, ' *' if self.dirty else '', file,
                                                                   self.project_dir))

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        choice = self.ask_save_changes()
        if choice == 'save':
            return self.mod_save()
        return choice == 'discard'

    def mod_new(self):
        if not self._confirm_discard():
            return
        self.mf = self.cmod.ModFile(self.cm.Mod([]), name='new_mod')
        self.mod_path = None
        self._output_is_default = True
        self._set_output(os.path.join(cotw_tools.default_mods_dir(), self.mf.name))
        self.dirty = False
        self._refresh_mod_panel(select=-1)
        self._update_title()

    def mod_open(self, path: Optional[str] = None):
        if not self._confirm_discard():
            return False
        path = path if isinstance(path, str) else self.ask_open_path()
        if not path:
            return False
        try:
            mf = self.cmod.load(path)
        except self.cmod.EMod as e:
            self.error_dialog(str(e))
            return False
        self.mf, self.mod_path, self.dirty = mf, path, False
        self._output_is_default = True
        self._set_output(os.path.join(cotw_tools.default_mods_dir(), mf.name or Path(path).name.split('.')[0]))
        self._refresh_mod_panel(select=0)
        self._update_title()
        return True

    def mod_save(self) -> bool:
        if not self.mod_path:
            return self.mod_save_as()
        return self._write(self.mod_path)

    def mod_save_as(self) -> bool:
        path = self.ask_save_path()
        if not path:
            return False
        return self._write(path)

    def _write(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.cmod.save(self.mf, path)
        except OSError as e:
            self.error_dialog('Could not save {}:\n{}'.format(path, e))
            return False
        self.mod_path = path
        self.dirty = False
        self._update_title()
        self.status_label.setText('Saved: {}'.format(path))
        return True

    # ----- build -----

    def _set_output(self, path: str):
        self.output_edit.setText(os.path.normpath(path))

    def _output_edited(self, _text):
        self._output_is_default = False

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Output folder', self.output_edit.text() or self._mods_dir())
        if path:
            self._set_output(path)
            self._output_is_default = False

    def _update_actions(self):
        busy = self.task_running()
        has_entries = bool(self._entries())
        problems = getattr(self, '_problems', {})
        self.build_button.setEnabled(not busy and self.catalogue is not None and has_entries and not problems)
        self.build_button.setToolTip('Fix the entries shown in red first' if problems else
                                     'Rebuild the dropzone of the mod from the game\'s original files')
        for w in (self.add_button, self.language_combo, self.action_open, self.action_new):
            w.setEnabled(not busy)
        self.add_button.setEnabled(not busy and self.catalogue is not None)
        for w in (self.duplicate_button, self.remove_button, self.up_button, self.down_button):
            w.setEnabled(not busy and self.current_entry() is not None)

    def build(self):
        if self.task_running():
            return
        problems = mod_problems(self.mf.mod, self.catalogue, self.cm, self.sl)
        if problems:
            self.error_dialog('Some entries have problems (shown in red):\n\n' + '\n'.join(
                '{}: {}'.format(self._entries()[i].nom, ' '.join(p)) for i, p in problems.items()))
            return
        output = self.output_edit.text().strip()
        if not output:
            self.error_dialog('Choose an output folder.')
            return
        mod_dict = self.cmod.to_dict(self.mf)   # the worker gets its own copy: editing can go on during the build
        project, overwrite, cmod, projet = self.project_dir, self.overwrite_check.isChecked(), self.cmod, self._projet

        def work(progress):
            mf = cmod.from_dict(mod_dict)
            vfs = projet.open_project(project)
            try:
                result = cmod.construire(vfs, mf, etape=progress)
                lines = cmod.ecrire(result, Path(output), overwrite, mf.name)
            finally:
                vfs.db_conn.close()
            return result.rapport + lines

        self.report_view.setPlainText('')
        self._start(work, self._built, 'Building', (cmod.EMod, self.cm.EClone))

    def _built(self, report: List[str]):
        self.last_report = report
        self.report_view.setPlainText('\n'.join(report))
        self.report_view.moveCursor(QTextCursor.MoveOperation.End)
        self.status_label.setText('Built: {}'.format(os.path.join(self.output_edit.text(), 'dropzone')))

    def command_line(self) -> Optional[str]:
        if not self.mod_path:
            return None
        return self.cmod.command_line(self.project_dir, self.mod_path, self.output_edit.text().strip(),
                                      self.overwrite_check.isChecked())

    def copy_command_line(self):
        if not self.mod_path or self.dirty:
            if not self.mod_save():
                return
        cmd = self.command_line()
        QGuiApplication.clipboard().setText(cmd)
        self.status_label.setText('Command copied to the clipboard.')
        self.report_view.appendPlainText(cmd)

    # ----- closing -----

    def closeEvent(self, event):
        if self.task_running():
            self.info_dialog('A task is still running; close the window when it is finished.')
            event.ignore()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        event.accept()

