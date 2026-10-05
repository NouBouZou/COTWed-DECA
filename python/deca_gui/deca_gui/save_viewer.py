"""
Save viewer (File > Open Save...): a theHunter: COTW save file read with cotw/sauvegarde.py, nothing is written.

Tabs: Summary (the text report of sauvegarde.py), Items (EquipmentOwned with amount, backpack, unlocked, name and
in-game name), Slots (InventorySlot and every loadout), Weapons (non-empty WeaponData entries), ADF (the whole file
as a lazy tree). Item hashes are named from the equipment_data.bin of the open project (all categories), from mod
files added with "Add mod file...", then from the project strings and the fork dictionary. A hash nobody names is
shown in red: an item removed from the game, or the item of a mod that is not loaded (unknown E4 of the research
log: a save keeps the hash of an added item).
"""
import os
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import cotw_tools
from .tree_view import TreeViewer

UNKNOWN_COLOR = QColor('#c62828')
ITEM_COLUMNS = ['#', 'Hash', 'Item', 'In-game name', 'Category', 'Amount', 'Backpack', 'Unlocked']


def default_saves_dir() -> str:
    """Documents/Avalanche Studios/COTW/Saves when it exists (research log 2026-10-03), else the home folder."""
    for base in (os.path.join(os.path.expanduser('~'), 'Documents'), os.path.expanduser('~')):
        d = os.path.join(base, 'Avalanche Studios', 'COTW', 'Saves')
        if os.path.isdir(d):
            return d
    return os.path.expanduser('~')


def yes_no(v: Optional[bool]) -> str:
    return '' if v is None else ('yes' if v else '-')


class SaveViewer(QWidget):
    def __init__(self, vfs_get=None, parent=None):
        """``vfs_get()``: the project to take item names from, asked at each reading (None: no project)."""
        super().__init__(parent, Qt.Window)
        cotw_tools.ensure_path()
        import _projet  # noqa: F401  (zugbruecke stand-in first)
        import sauvegarde
        import stringlookup
        self.S = sauvegarde
        self.vfs_get = vfs_get or (lambda: None)
        self.path: Optional[str] = None
        self.mods: List[str] = []
        self.reading = None
        self.errors: List[str] = []
        self.setWindowTitle('Save viewer')
        self.resize(1100, 760)

        self.path_label = QLabel('No file')
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_button = QPushButton('Open...')
        self.open_button.clicked.connect(self.open_dialog)
        self.mod_button = QPushButton('Add mod file...')
        self.mod_button.setToolTip('Names the items added by a mod (.cotwmod.json), which the game files do not know')
        self.mod_button.clicked.connect(self.add_mod_dialog)
        self.language = QComboBox()
        self.language.addItems(stringlookup.LANGUES)
        self.language.setToolTip('Language of the in-game names (string_ids of the open project)')
        self.language.currentIndexChanged.connect(lambda _i: self.reload())
        bar = QHBoxLayout()
        bar.addWidget(self.path_label, 1)
        bar.addWidget(QLabel('Language:'))
        bar.addWidget(self.language)
        bar.addWidget(self.mod_button)
        bar.addWidget(self.open_button)

        mono = QFont('Courier', 9)
        mono.setStyleHint(QFont.Monospace)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.summary.setFont(mono)
        self.items = QTableWidget(0, len(ITEM_COLUMNS))
        self.items.setHorizontalHeaderLabels(ITEM_COLUMNS)
        self.items.setEditTriggers(QTableWidget.NoEditTriggers)
        self.items.setSelectionBehavior(QTableWidget.SelectRows)
        self.items.verticalHeader().setVisible(False)
        self.items.setSortingEnabled(True)
        self.slots = QTreeWidget()
        self.slots.setHeaderLabels(['Slot', 'Item', 'Hash'])
        self.weapons = QTableWidget(0, 0)
        self.weapons.setEditTriggers(QTableWidget.NoEditTriggers)
        self.weapons.verticalHeader().setVisible(False)
        self.adf = TreeViewer()
        self.tabs = QTabWidget()
        for widget, title in ((self.summary, 'Summary'), (self.items, 'Items'), (self.slots, 'Slots'),
                              (self.weapons, 'Weapons'), (self.adf, 'ADF')):
            self.tabs.addTab(widget, title)
        self.status = QLabel()
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.tabs)
        layout.addWidget(self.status)

    # ----- actions -----

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open Save', os.path.dirname(self.path or '') or
                                              default_saves_dir(), 'All files (*)')
        if path:
            self.open_file(path)

    def add_mod_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Add mod file', cotw_tools.default_mods_dir(),
                                              'COTWed mod (*.cotwmod.json);;All files (*)')
        if path:
            self.add_mod(path)

    def add_mod(self, path: str):
        if path not in self.mods:
            self.mods.append(path)
        self.reload()

    def open_file(self, path: str) -> bool:
        self.path = path
        return self.reload()

    def reload(self) -> bool:
        """Read the file again (language or mod files changed). False, with the reason shown, if unreadable."""
        if not self.path:
            return False
        self.errors = []
        try:
            self.reading = self.S.lire(self.path, self.vfs_get(), self.mods, self.language.currentText())
        except (self.S.ESauvegarde, OSError) as e:
            self.reading = None
            self.errors.append(str(e))
            self.path_label.setText(self.path)
            self.summary.setPlainText('Not readable as a save: {}'.format(e))
            self.items.setRowCount(0)
            self.slots.clear()
            self.weapons.setRowCount(0)
            self.status.setText('Not readable: {}'.format(e))
            return False
        self.path_label.setText(self.path)
        self.setWindowTitle('Save viewer - {}'.format(os.path.basename(self.path)))
        self._fill()
        return True

    # ----- display -----

    def _fill(self):
        r = self.reading
        lines = self.S.rapport(r.chemin, r.conteneur, r.adf, r.profil, r.nommeur)
        self.summary.setPlainText('\n'.join(['WARNING: ' + w for w in r.avertissements] + lines))
        self._fill_items()
        self._fill_slots()
        self._fill_weapons()
        self.adf.set_tree(self._adf_tree(), None, expand_depth=2)
        p = r.profil
        if p is None:
            self.status.setText('No player profile (PlayerProfileData) in this file: see the Summary and ADF tabs.')
        else:
            unknown = [h for h in p.hashes() if not r.nommeur.connu(h)]
            names = ('names from: ' + ', '.join(r.nommeur.index.sources)) if r.nommeur.index.sources else \
                'no item names (open a project first, or add a mod file)'
            self.status.setText('{} items owned, {} unknown item hashes; {}.'.format(len(p.possedes), len(unknown),
                                                                                  names))

    def _adf_tree(self):
        from .tree_items import adf_tree
        return adf_tree(self.reading.adf, self.S._NomsAdapter(self.reading.nommeur), os.path.basename(self.path))

    def _item_cells(self, h: int):
        """(item, in-game name, category, known) of a hash."""
        nom = self.reading.nommeur
        o = nom.objet(h)
        if o is not None:
            return o.nom, o.nom_affiche or '', o.categorie, True
        name = nom.noms.get(h) if nom.noms is not None else None
        if name:
            return name, '', '', True
        return 'unknown: removed item, or item of a mod not loaded?', '', '', False

    def _fill_items(self):
        p = self.reading.profil
        rows = p.possedes if p is not None else []
        self.items.setSortingEnabled(False)
        self.items.setRowCount(len(rows))
        for r, e in enumerate(rows):
            name, shown, cat, known = self._item_cells(e.hash)
            values = [e.index, '{:08x}'.format(e.hash), name, shown, cat,
                      '' if e.quantite is None else e.quantite, yes_no(e.sac), yes_no(e.debloque)]
            for c, v in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, v)
                if not known:
                    item.setForeground(QBrush(UNKNOWN_COLOR))
                    item.setToolTip('No source names hash {:08x}: an item removed from the game, or the item of a '
                                    'mod that is not loaded (Add mod file...).'.format(e.hash))
                self.items.setItem(r, c, item)
        self.items.setSortingEnabled(True)
        self.items.sortItems(0, Qt.AscendingOrder)
        self.items.resizeColumnsToContents()

    def _slot_group(self, title: str, slots) -> QTreeWidgetItem:
        group = QTreeWidgetItem([title, '{} of {} slots used'.format(sum(1 for s in slots if s), len(slots)), ''])
        for i, hashes in enumerate(slots):
            if not hashes:
                continue
            slot = QTreeWidgetItem(['[{}]'.format(i), '{} item(s)'.format(len(hashes)), ''])
            for h in hashes:
                name, shown, _cat, known = self._item_cells(h)
                child = QTreeWidgetItem(['', name + ('  "{}"'.format(shown) if shown else ''), '{:08x}'.format(h)])
                if not known:
                    child.setForeground(1, QBrush(UNKNOWN_COLOR))
                slot.addChild(child)
            group.addChild(slot)
        return group

    def _fill_slots(self):
        self.slots.clear()
        p = self.reading.profil
        if p is None:
            return
        self.slots.addTopLevelItem(self._slot_group('InventorySlot', p.emplacements))
        for label, slots in p.loadouts:
            self.slots.addTopLevelItem(self._slot_group(label, slots))
        self.slots.expandToDepth(1)
        self.slots.resizeColumnToContents(0)
        self.slots.setColumnWidth(1, 520)

    def _fill_weapons(self):
        p = self.reading.profil
        rows = p.armes if p is not None else []
        keys: List[str] = []
        for _i, w in rows:
            keys += [k for k in w if k not in keys]
        self.weapons.clear()
        self.weapons.setColumnCount(1 + len(keys))
        self.weapons.setHorizontalHeaderLabels(['#'] + keys)
        self.weapons.setRowCount(len(rows))
        nom = self.reading.nommeur
        for r, (i, w) in enumerate(rows):
            self.weapons.setItem(r, 0, QTableWidgetItem(str(i)))
            for c, k in enumerate(keys, 1):
                v = w.get(k, '')
                s = str(v)
                if isinstance(v, int) and not isinstance(v, bool) and v >= self.S.HASH_MIN and nom.connu(v):
                    s = '{} ({})'.format(v, nom.court(v))
                self.weapons.setItem(r, c, QTableWidgetItem(s))
        self.weapons.resizeColumnsToContents()
