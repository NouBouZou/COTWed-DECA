import traceback
from typing import Callable, Dict, List, Optional

from deca.db_processor import VfsProcessor
from deca.ff_types import *
from .viewer_adf import DataViewerAdf
from .viewer_rtpc import DataViewerRtpc
from .viewer_image import DataViewerImage
from .viewer_raw import DataViewerRaw
from .viewer_info import DataViewerInfo
from .viewer_text import DataViewerText
from .viewer_sarc import DataViewerSarc
from .viewer_obc import DataViewerObc
from .viewer_structure import DataViewerStructure
from .viewer_havok import DataViewerHavok
from .viewer_stringlookup import DataViewerStringLookup
from .viewer_xlsbook import DataViewerXlsBook
from .reading import STRINGLOOKUP_TYPE, XLSBOOK_TYPE, adf_instance_types, node_bytes
from .deca_interfaces import IVfsViewSrc
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget, QVBoxLayout, QTabWidget

# DECA file type -> tab showing the content (the routing of vnode_2click_selected). cotw/deca_audit.py keeps a
# copy (LECTEURS, no Qt dependency) to measure what the fork can read; test_audit_couverture.py checks both agree.
TEXT_TYPES = {FTYPE_TXT}
SARC_TYPES = {FTYPE_SARC}
IMAGE_TYPES = {FTYPE_AVTX, FTYPE_ATX, FTYPE_HMDDSC, FTYPE_DDS, FTYPE_BMP, FTYPE_RAWC}
RTPC_TYPES = {FTYPE_RTPC, FTYPE_RTPC_INLINE}
OBC_TYPES = {FTYPE_OBC}
STRUCTURE_TYPES = {FTYPE_FMOD_GUIDS, FTYPE_TTF, FTYPE_BTC, FTYPE_RIFF, FTYPE_FSB5C, FTYPE_MDI, FTYPE_GFX, FTYPE_BINK_BIK}
HAVOK_TYPES = {FTYPE_H2014, FTYPE_PFX}
VIEWER_TABS = dict([(t, 'Text') for t in TEXT_TYPES] + [(t, 'SARC') for t in SARC_TYPES] +
                   [(t, 'Image') for t in IMAGE_TYPES] + [(t, 'RTPC') for t in RTPC_TYPES] +
                   [(t, 'OBC') for t in OBC_TYPES] + [(t, 'Structure') for t in STRUCTURE_TYPES] +
                   [(t, 'Havok') for t in HAVOK_TYPES] +
                   [(t, 'ADF/GDC' if t == FTYPE_ADF_BARE else 'ADF') for t in ftype_adf_family])


class DataViewWidget(QWidget):
    """
    Right-hand panel: the viewers of the node double-clicked in a list.

    COTWed DECA: heavy viewers run only when their tab is shown (the generic ADF tree of the 38.8 MB string_ids
    takes DECA 17 s to read; its dedicated Strings tab reads the bytes directly). An error in a viewer is shown in the
    Info tab instead of being lost in the console.
    """

    def __init__(self, *args, **kwargs):
        QWidget.__init__(self, *args, **kwargs)

        self.data_source: Optional[IVfsViewSrc] = None

        self.tab_info = DataViewerInfo()
        self.tab_raw = DataViewerRaw()
        self.tab_text = DataViewerText()
        self.tab_sarc = DataViewerSarc()
        self.tab_image = DataViewerImage()
        self.tab_strings = DataViewerStringLookup()
        self.tab_sheet = DataViewerXlsBook()
        self.tab_adf = DataViewerAdf()
        self.tab_adf_gdc = DataViewerAdf()
        self.tab_rtpc = DataViewerRtpc()
        self.tab_obc = DataViewerObc()
        self.tab_structure = DataViewerStructure()
        self.tab_havok = DataViewerHavok()

        self.tab_widget = QTabWidget()
        self.tab_info_index = self.tab_widget.addTab(self.tab_info, 'Info')
        self.tab_raw_index = self.tab_widget.addTab(self.tab_raw, 'Raw/Hex')
        self.tab_text_index = self.tab_widget.addTab(self.tab_text, 'Text')
        self.tab_sarc_index = self.tab_widget.addTab(self.tab_sarc, 'SARC')
        self.tab_image_index = self.tab_widget.addTab(self.tab_image, 'Image')
        self.tab_strings_index = self.tab_widget.addTab(self.tab_strings, 'Strings')
        self.tab_sheet_index = self.tab_widget.addTab(self.tab_sheet, 'Sheet')
        self.tab_adf_index = self.tab_widget.addTab(self.tab_adf, 'ADF')
        self.tab_adf_gdc_index = self.tab_widget.addTab(self.tab_adf_gdc, 'ADF/GDC')
        self.tab_rtpc_index = self.tab_widget.addTab(self.tab_rtpc, 'RTPC')
        self.tab_obc_index = self.tab_widget.addTab(self.tab_obc, 'OBC')
        self.tab_structure_index = self.tab_widget.addTab(self.tab_structure, 'Structure')
        self.tab_havok_index = self.tab_widget.addTab(self.tab_havok, 'Havok')

        self._pending: Dict[int, Callable[[], None]] = {}
        self.errors: List[str] = []
        self.tab_widget.currentChanged.connect(self._run_pending)
        self.tab_sarc.open_entry = lambda uid: self.vnode_2click_selected([uid])

        self.clear()

        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        size.setVerticalStretch(1)
        self.tab_widget.setSizePolicy(size)

        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(self.tab_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.main_layout)

    def data_source_set(self, data_source: IVfsViewSrc):
        self.data_source = data_source
        self.data_source.signal_selection_changed.connect(self.vnode_selection_changed)
        self.tab_sarc.signal_archive_open.connect(self.data_source.archive_open)

    def clear(self):
        """
        Forget the node shown, e.g. when another project is opened.

        Viewers keep their last node: the SARC viewer's "Open Archive" used it as a node id of the new project, i.e.
        opened an unrelated file. Every tab is disabled until a node of the new project is double-clicked.
        """
        self._pending.clear()
        self.tab_sarc.vnode = None
        self.tab_sarc.vfs = None
        self.tab_info.text_box.clear()
        for index in range(self.tab_widget.count()):
            self.tab_widget.setTabEnabled(index, index == self.tab_info_index)
        self.tab_widget.setCurrentIndex(self.tab_info_index)
        self.tab_widget.setEnabled(False)

    def vnode_selection_changed(self):
        pass

    # ----- running viewers -----

    def _process(self, index: int, viewer, vfs, vnode):
        """Run a viewer; on error, keep the tab enabled with nothing new and report in the Info tab."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            viewer.vnode_process(vfs, vnode)
        except Exception:  # noqa: BLE001 - a broken file must not break the panel
            text = '{} viewer failed:\n{}'.format(self.tab_widget.tabText(index), traceback.format_exc())
            self.errors.append(text)
            self.tab_info.text_box.append('\n' + text)
        finally:
            QApplication.restoreOverrideCursor()

    def _enable(self, index: int, viewer, vfs, vnode, now: bool):
        self.tab_widget.setTabEnabled(index, True)
        if now:
            self._process(index, viewer, vfs, vnode)
        else:
            self._pending[index] = lambda: self._process(index, viewer, vfs, vnode)

    def _run_pending(self, index: int):
        job = self._pending.pop(index, None)
        if job is not None:
            job()

    def _show(self, index: int):
        self.tab_widget.setCurrentIndex(index)
        self._run_pending(index)   # no currentChanged signal if this tab was already the current one

    def vnode_2click_selected(self, uids: List[int]):
        vfs: VfsProcessor = self.data_source.vfs_get()
        vnodes = [vfs.node_where_uid(uid) for uid in uids]
        vnode = vnodes[0]

        self._pending.clear()
        self.errors = []
        self.tab_widget.setEnabled(True)
        for index in range(self.tab_widget.count()):
            self.tab_widget.setTabEnabled(index, index in (self.tab_info_index, self.tab_raw_index))
        self._process(self.tab_info_index, self.tab_info, vfs, vnode)
        self._process(self.tab_raw_index, self.tab_raw, vfs, vnode)

        target = self.tab_raw_index
        if vnode.file_type in TEXT_TYPES:
            self._enable(self.tab_text_index, self.tab_text, vfs, vnode, True)
            target = self.tab_text_index
        elif vnode.file_type in SARC_TYPES:
            self._enable(self.tab_sarc_index, self.tab_sarc, vfs, vnode, True)
            target = self.tab_sarc_index
        elif vnode.file_type in IMAGE_TYPES:
            self._enable(self.tab_image_index, self.tab_image, vfs, vnode, True)
            target = self.tab_image_index
        elif vnode.file_type in ftype_adf_family:
            # GenZero: ADF files can come in pairs (an ADF and a bare ADF of the gdcc)
            vnodes_adf = [v for v in vnodes if v.file_type != FTYPE_ADF_BARE]
            vnodes_adfb = [v for v in vnodes if v.file_type == FTYPE_ADF_BARE]
            if vnodes_adf:
                adf_node = vnodes_adf[0]
                try:
                    types = adf_instance_types(node_bytes(vfs, adf_node))
                except Exception:  # noqa: BLE001 - routing only: the generic viewer will report the problem
                    types = []
                if STRINGLOOKUP_TYPE in types:
                    target = self.tab_strings_index
                    self._enable(target, self.tab_strings, vfs, adf_node, True)
                elif XLSBOOK_TYPE in types:
                    target = self.tab_sheet_index
                    self._enable(target, self.tab_sheet, vfs, adf_node, True)
                else:
                    target = self.tab_adf_index
                # the generic tree is always available; computed now only if it is the tab shown
                self._enable(self.tab_adf_index, self.tab_adf, vfs, adf_node, target == self.tab_adf_index)
            if vnodes_adfb:
                if not vnodes_adf:
                    target = self.tab_adf_gdc_index
                self._enable(self.tab_adf_gdc_index, self.tab_adf_gdc, vfs, vnodes_adfb[0],
                             target == self.tab_adf_gdc_index)
        elif vnode.file_type in RTPC_TYPES:
            self._enable(self.tab_rtpc_index, self.tab_rtpc, vfs, vnode, True)
            target = self.tab_rtpc_index
        elif vnode.file_type in OBC_TYPES:
            self._enable(self.tab_obc_index, self.tab_obc, vfs, vnode, True)
            target = self.tab_obc_index
        elif vnode.file_type in STRUCTURE_TYPES:
            self._enable(self.tab_structure_index, self.tab_structure, vfs, vnode, True)
            target = self.tab_structure_index
        elif vnode.file_type in HAVOK_TYPES:
            self._enable(self.tab_havok_index, self.tab_havok, vfs, vnode, True)
            target = self.tab_havok_index

        self._show(target)
