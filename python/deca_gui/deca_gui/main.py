import argparse
import multiprocessing
import re
import shutil
import sys
import os
import time
from typing import Optional, List
from deca.errors import *
from deca.db_core import VfsNode
from deca.db_processor import VfsProcessor, vfs_structure_empty, project_working_dir
from deca.game_info import determine_game
from deca.db_view import VfsView
from deca.builder import Builder
from deca.path import UniPath
from deca.util import Logger, to_unicode, deca_root
from deca.cmds.tool_make_web_map import ToolMakeWebMap
from deca.export_import import \
    nodes_export_raw, nodes_export_contents, nodes_export_processed, nodes_export_gltf, nodes_export_map
from .main_window import Ui_MainWindow
from .deca_interfaces import IVfsViewSrc
from .vfsdirwidget import VfsDirWidget
from .vfsnodetablewidget import VfsNodeTableWidget
from .project_task import ProjectTask, start_task, project_scan_state, KIND_NEW, KIND_OPEN, SCAN_STARTED, \
    SCAN_COMPLETE
from PySide6.QtCore import Slot, QUrl, Signal, QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QTabBar, QMessageBox, QFileDialog, QStyle, QDockWidget, \
    QPlainTextEdit, QProgressBar, QLabel
from PySide6.QtGui import QDesktopServices, QKeyEvent, QFont, QCloseEvent, QAction

LOG_MAX_LINES = 5000

window_title = 'decaGUI: v0.2.19rc'


class MainWindowDataSource(IVfsViewSrc):
    def __init__(self, main_window, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.main_window: MainWindow = main_window

    def vfs_get(self) -> Optional[VfsProcessor]:
        vfs_view = self.main_window.vfs_view_current()
        return None if vfs_view is None else vfs_view.vfs()

    def vfs_view_get(self) -> Optional[VfsView]:
        return self.main_window.vfs_view_current()

    def archive_open(self, selection):
        return self.main_window.slot_archive_open(selection)


class MainWindow(QMainWindow):
    signal_visible_changed = Signal(VfsView)
    signal_selection_changed = Signal(VfsView)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.data_source = MainWindowDataSource(self)

        self.vfs: Optional[VfsProcessor] = None
        self.logger = Logger('./')

        self.builder = Builder()
        self.current_uids = None
        self.vfs_view_root: Optional[VfsView] = None

        self.tab_nodes_deletable = set()

        # background open / scan (see project_task.py)
        self._task: Optional[ProjectTask] = None
        self._task_thread = None
        self._task_started_at = 0.0
        self._task_label = ''
        self._setup_task_widgets()
        # replaced by the tests: leaving during a scan must not end the test process
        self.hard_exit = os._exit

        self.signal_visible_changed.connect(self.slot_visible_changed)
        self.signal_selection_changed.connect(self.slot_selection_changed)

        # Configure Actions
        self.ui.action_project_new.triggered.connect(self.project_new)
        self.ui.action_project_open.triggered.connect(self.project_open)
        self.ui.action_file_gz_open.triggered.connect(self.file_gz_open)
        self.ui.action_external_add.triggered.connect(self.external_add)
        self.ui.action_external_add.setEnabled(False)

        # self.ui.action_external_manage.triggered.connect(self.external_manage)
        self.ui.action_exit.triggered.connect(self.exit_app)
        self.ui.action_make_web_map.triggered.connect(self.tool_make_web_map)
        self.ui.action_make_web_map.setEnabled(False)

        # COTW: Ammo Workshop (ammo_workshop.py), one window bound to the project open when it was opened
        self.ammo_workshop = None
        self.action_ammo_workshop = QAction('Ammo &Workshop...', self)
        self.action_ammo_workshop.setToolTip('Add new ammunition without overwriting anything (mod file, dropzone)')
        self.action_ammo_workshop.triggered.connect(self.ammo_workshop_open)
        self.action_ammo_workshop.setEnabled(False)
        self.ui.menu_Tools.addAction(self.action_ammo_workshop)

        # COTW: save viewer (save_viewer.py); works without a project, names the items with the open one
        self.save_viewers = []
        self.action_save_open = QAction('Open &Save...', self)
        self.action_save_open.setToolTip('Read a theHunter: COTW save file (owned items, slots, loadouts); '
                                         'nothing is written')
        self.action_save_open.triggered.connect(self.save_open)
        self.ui.menu_File.insertAction(self.ui.action_exit, self.action_save_open)

        # filter
        self.ui.filter_edit.textChanged.connect(self.filter_text_changed)
        self.ui.filter_edit.installEventFilter(self)
        self.ui.filter_set_bt.clicked.connect(self.filter_text_accepted)
        self.ui.filter_clear_bt.clicked.connect(self.filter_text_cleared)

        self.ui.vhash_to_vpath_in_edit.textChanged.connect(self.vhash_to_vpath_text_changed)

        self.ui.chkbx_export_raw_extract.setChecked(True)
        self.ui.chkbx_export_contents_extract.setChecked(False)
        self.ui.chkbx_export_text_extract.setChecked(False)
        self.ui.chkbx_export_processed_extract.setChecked(False)

        self.ui.chkbx_export_raw_mods.setChecked(True)
        self.ui.chkbx_export_contents_mods.setChecked(False)
        self.ui.chkbx_export_processed_mods.setChecked(False)

        self.ui.chkbx_mod_build_subset.setChecked(False)
        self.ui.chkbx_mod_build_subset.clicked.connect(self.slot_mod_build_subset_clicked)

        self.ui.chkbx_export_save_to_one_dir.setChecked(False)

        self.ui.bt_extract.setEnabled(False)
        self.ui.bt_extract.clicked.connect(self.slot_extract_clicked)

        self.ui.bt_extract_folder_show.setEnabled(False)
        self.ui.bt_extract_folder_show.setIcon(self.style().standardIcon(QStyle.SP_DirLinkIcon))
        self.ui.bt_extract_folder_show.clicked.connect(self.slot_folder_show_clicked)

        self.ui.bt_extract_gltf_3d.setEnabled(False)
        self.ui.bt_extract_gltf_3d.clicked.connect(self.slot_extract_gltf_clicked)

        self.ui.bt_extract_gltf_3d_folder_show.setEnabled(False)
        self.ui.bt_extract_gltf_3d_folder_show.setIcon(self.style().standardIcon(QStyle.SP_DirLinkIcon))
        self.ui.bt_extract_gltf_3d_folder_show.clicked.connect(self.slot_folder_show_clicked)

        self.ui.bt_mod_prep.setEnabled(False)
        self.ui.bt_mod_prep.clicked.connect(self.slot_mod_prep_clicked)

        self.ui.bt_mod_folder_show.setEnabled(False)
        self.ui.bt_mod_folder_show.setIcon(self.style().standardIcon(QStyle.SP_DirLinkIcon))
        self.ui.bt_mod_folder_show.clicked.connect(self.slot_folder_show_clicked)

        self.ui.bt_mod_build_folder_show.setEnabled(False)
        self.ui.bt_mod_build_folder_show.setIcon(self.style().standardIcon(QStyle.SP_DirLinkIcon))
        self.ui.bt_mod_build_folder_show.clicked.connect(self.slot_folder_show_clicked)

        self.ui.bt_mod_build.setEnabled(False)
        self.ui.bt_mod_build.clicked.connect(self.slot_mod_build_clicked)

        self.ui.tabs_nodes.tabCloseRequested.connect(self.slot_nodes_tab_close)
        self.ui.tabs_nodes.currentChanged.connect(self.slot_nodes_tab_current_changed)

        self.ui.data_view.data_source_set(self.data_source)

        self.update_ui_state()

    def _setup_task_widgets(self):
        """Log dock and status-bar widgets used while a project is being opened or scanned."""
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_MAX_LINES)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont('Courier', 8)
        font.setStyleHint(QFont.Monospace)
        self.log_view.setFont(font)

        self.log_dock = QDockWidget('Log', self)
        self.log_dock.setObjectName('log_dock')
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        self.task_label = QLabel()
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 0)  # busy indicator: DECA does not know the total amount of work
        self.task_progress.setMaximumWidth(160)
        self.task_progress.setTextVisible(False)
        self.ui.statusbar.addPermanentWidget(self.task_label)
        self.ui.statusbar.addPermanentWidget(self.task_progress)
        self.task_label.hide()
        self.task_progress.hide()

        self.task_timer = QTimer(self)
        self.task_timer.setInterval(1000)
        self.task_timer.timeout.connect(self._task_tick)

    def task_running(self) -> bool:
        return self._task is not None

    def _task_tick(self):
        elapsed = int(time.time() - self._task_started_at)
        self.task_label.setText('{}  {:d}:{:02d}'.format(self._task_label, elapsed // 60, elapsed % 60))

    def task_start(self, kind: str, path: str):
        """Open (KIND_OPEN, project.json) or create and scan (KIND_NEW, game exe) a project in the background."""
        if self.task_running():
            self.error_dialog('A project is already being opened or scanned.')
            return

        resumed = False
        if kind == KIND_NEW:
            self._task_label = 'Scanning game'
        else:
            state = project_scan_state(path)
            resumed = state == SCAN_STARTED
            if state == SCAN_COMPLETE:
                self._task_label = 'Opening project'
            elif resumed:
                self._task_label = 'Resuming unfinished scan'
            else:
                self._task_label = 'Scanning game'  # project.json without a scanned database: full scan
        self._task_started_at = time.time()
        self.log_view.appendPlainText('---- {}: {}'.format(self._task_label, path))
        if resumed:
            self.log_view.appendPlainText(
                'The scan of this project was interrupted: DECA resumes it. For a reference scan (audits), delete the '
                'project and create it again (File > New Project > Delete and rescan).')
        self.log_dock.show()
        self.task_label.show()
        self.task_progress.show()
        self._task_tick()
        self.task_timer.start()

        self._task = ProjectTask(kind, path)
        self._task.message.connect(self.log_view.appendPlainText)
        self._task.succeeded.connect(self._task_succeeded)
        self._task.failed.connect(self._task_failed)
        self._task.done.connect(self._task_done)
        self._task_thread = start_task(self._task, self)
        self.update_ui_state()

    def _task_succeeded(self, vfs):
        self.ui.statusbar.showMessage('Building views...')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.vfs_set(vfs)
        finally:
            QApplication.restoreOverrideCursor()

    def _task_failed(self, reason):
        self.log_view.appendPlainText(reason)
        self.ui.statusbar.showMessage('FAILED')
        self.error_dialog('{} failed.\n\n{}'.format(self._task_label, reason.strip().splitlines()[-1] if reason else ''))

    def _task_done(self):
        elapsed = time.time() - self._task_started_at
        self.log_view.appendPlainText('---- {} finished in {:.1f} s'.format(self._task_label, elapsed))
        self.task_timer.stop()
        self.task_label.hide()
        self.task_progress.hide()
        self._task = None
        self._task_thread = None
        self.update_ui_state()

    def project_open_path(self, path: str):
        """Open a project from a project.json file or from the folder that contains it."""
        if os.path.isdir(path):
            path = os.path.join(path, 'project.json')
        if not os.path.isfile(path):
            self.error_dialog('Project file not found: {}'.format(path))
            return
        self.task_start(KIND_OPEN, path)

    def ammo_workshop_open(self, checked=False):
        """Show the Ammo Workshop of the current project (the window of another project is closed first)."""
        if self.vfs is None:
            return
        project_dir = self.vfs.working_dir
        if self.ammo_workshop is not None and \
                os.path.normpath(self.ammo_workshop.project_dir) != os.path.normpath(project_dir):
            if not self.ammo_workshop_close():
                return
        if self.ammo_workshop is None:
            from .ammo_workshop import AmmoWorkshop   # imported on demand: DECA starts without the COTW tools
            try:
                self.ammo_workshop = AmmoWorkshop(project_dir, self)
            except RuntimeError as exc:
                self.error_dialog(str(exc))
                return
        self.ammo_workshop.show()
        self.ammo_workshop.raise_()
        self.ammo_workshop.activateWindow()

    def save_project(self):
        """Project whose files name the items of a save: the open one, unless it is being opened or scanned."""
        return None if self.task_running() else self.vfs

    def save_open(self, checked=False, path=None):
        """Open a save file in a new Save viewer window; returns the window (None if cancelled or unreadable)."""
        from .save_viewer import SaveViewer, default_saves_dir   # imported on demand, like the Ammo Workshop
        if not isinstance(path, str):
            path, _ = QFileDialog.getOpenFileName(self, 'Open Save ...', default_saves_dir(), 'All files (*)')
            if not path:
                return None
        try:
            viewer = SaveViewer(self.save_project, self)
        except RuntimeError as exc:
            self.error_dialog(str(exc))
            return None
        if not viewer.open_file(path):
            self.error_dialog('Not readable as a save: {}\n\n{}'.format(path, '\n'.join(viewer.errors)))
            viewer.deleteLater()
            return None
        self.save_viewers = [v for v in self.save_viewers if v.isVisible()] + [viewer]
        viewer.show()
        viewer.raise_()
        return viewer

    def ammo_workshop_close(self) -> bool:
        """Close the Ammo Workshop; False if it refused (task running, unsaved mod kept)."""
        if self.ammo_workshop is None:
            return True
        if self.ammo_workshop.isVisible() and not self.ammo_workshop.close():
            return False
        self.ammo_workshop.deleteLater()
        self.ammo_workshop = None
        return True

    def closeEvent(self, event: QCloseEvent):
        if not self.ammo_workshop_close():
            event.ignore()
            return
        if not self.task_running():
            event.accept()
            return
        answer = QMessageBox.question(
            self, 'DECA',
            'A project is still being opened or scanned.\n\nQuit anyway? The scan stops and the project is left '
            'unfinished. To scan it again from the start: File > New Project > Delete and rescan.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            event.ignore()
            return
        event.accept()
        self.task_abort_and_exit()

    def task_abort_and_exit(self):
        """
        Stop a running open / scan and end the process immediately.

        DECA's scan has no cancellation point. A normal exit would hang: Python's shutdown waits for the scan worker
        processes, which only stop when the scan thread sends them 'exit' at the end of the scan, and the scan thread
        itself keeps running. So the worker processes are terminated and the process ends without the normal Python
        shutdown. The database is left as an unfinished scan (PRAGMA user_version 1); SQLite rolls back the
        interrupted transaction the next time it is opened.
        """
        children = multiprocessing.active_children()
        for child in children:
            child.terminate()
        for child in children:
            child.join(timeout=5)
        self.hard_exit(1)

    def eventFilter(self, source, event):
        if event.type() == QEvent.KeyRelease and source is self.ui.filter_edit:
            self.filter_text_key_release(source, event)

        return super().eventFilter(source, event)

    def error_dialog(self, s):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Warning)

        msg.setWindowTitle("DECA: ERROR")
        msg.setText(s)
        # msg.setInformativeText("This is additional information")
        # msg.setDetailedText("The details are as follows:")
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()

    def dialog_good(self, s):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)

        msg.setWindowTitle("DECA")
        msg.setText(s)
        # msg.setInformativeText("This is additional information")
        # msg.setDetailedText("The details are as follows:")
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()

    def vfs_view_create(self, *args, **kwargs):
        vfs_view = VfsView(*args, **kwargs)
        vfs_view.signal_visible_changed.connect(
            self, lambda x: x.signal_visible_changed.emit(vfs_view))
        vfs_view.signal_selection_changed.connect(
            self, lambda x: x.signal_selection_changed.emit(vfs_view))

        return vfs_view

    def tabs_nodes_clear(self):
        """Remove every node tab, so that opening a second project does not add its tabs next to the first one."""
        while self.ui.tabs_nodes.count() > 0:
            widget = self.ui.tabs_nodes.widget(0)
            self.ui.tabs_nodes.removeTab(0)
            self.tab_nodes_deletable.discard(widget)
            if widget is not None:
                widget.deleteLater()

    def project_close(self):
        """
        Remove the views of the current project and close its database.

        Without the explicit close, the previous project's database stayed open after another project was opened:
        on Windows an open SQLite file cannot be deleted (File > New Project > Delete and rescan failed).
        """
        self.tabs_nodes_clear()
        self.ui.data_view.clear()
        self.current_uids = None
        self.vfs_view_root = None
        if self.vfs is not None:
            self.vfs.db_conn.close()
        self.vfs = None

    def vfs_set(self, vfs):
        if self.vfs is not vfs:
            self.project_close()
        else:
            self.tabs_nodes_clear()
            self.ui.data_view.clear()
        self.vfs = vfs
        self.vfs_view_root = self.vfs_view_create(vfs, None, b'^.*$')

        # Configure VFS dir table
        widget = self.tab_nodes_add(VfsDirWidget, self.vfs_view_root, 'Directory')

        # Configure VFS Node table (non-mapped nodes)
        widget = self.tab_nodes_add(VfsNodeTableWidget, self.vfs_view_root, 'Non-Mapped List')
        widget.show_all_set(False)

        # Configure VFS Node table (all nodes)
        widget = self.tab_nodes_add(VfsNodeTableWidget, self.vfs_view_root, 'Raw List')
        widget.show_all_set(True)

        self.update_ui_state()

        self.ui.statusbar.showMessage("LOAD COMPLETE")

    def vfs_view_current(self) -> Optional[VfsView]:
        widget = self.ui.tabs_nodes.currentWidget()
        if widget is None:
            return None
        return widget.vfs_view_get()

    def tab_nodes_add(self, widget_class, vfs_view, name, deletable=False):
        # self.tab_extract = QtWidgets.QWidget()
        # self.tab_extract.setObjectName("tab_extract")
        # self.gridLayout = QtWidgets.QGridLayout(self.tab_extract)
        # self.gridLayout.setObjectName("gridLayout")
        widget = widget_class(vfs_view, self.ui.tabs_nodes)
        tabIndex = self.ui.tabs_nodes.addTab(widget, name)
        widget.vnode_2click_selected = self.vnode_2click_selected

        if deletable:
            self.tab_nodes_deletable.add(widget)
        else:
            self.ui.tabs_nodes.tabBar().setTabButton(tabIndex, QTabBar.RightSide, None)

        return widget

    def slot_archive_open(self, vnode: VfsNode):
        if vnode is not None:
            vfs_view = self.vfs_view_create(self.vfs_view_current(), parent_id=vnode.uid)
            self.tab_nodes_add(VfsDirWidget, vfs_view, to_unicode(vnode.v_path), True)

    def slot_nodes_tab_close(self, index):
        widget = self.ui.tabs_nodes.widget(index)
        if widget in self.tab_nodes_deletable:
            self.tab_nodes_deletable.remove(widget)
            if widget is not None:
                widget.deleteLater()
            self.ui.tabs_nodes.removeTab(index)

    def slot_nodes_tab_current_changed(self, index):
        vfs_view = self.vfs_view_current()
        if vfs_view is None:
            # no tab left (all closed, or the tabs of the previous project are being removed)
            self.setWindowTitle(window_title)
            return
        self.setWindowTitle("{}: Archive: {}".format(window_title, vfs_view.vfs().game_info.game_dir))
        self.update_select_state(vfs_view)

    def slot_visible_changed(self, vfs_view: VfsView):
        self.update_select_state(vfs_view)

    def slot_selection_changed(self, vfs_view: VfsView):
        self.update_select_state(vfs_view)

    def update_ui_state(self):
        busy = self.task_running()
        self.ui.action_project_new.setEnabled(not busy)
        self.ui.action_project_open.setEnabled(not busy)
        self.ui.action_file_gz_open.setEnabled(not busy)
        if self.vfs_view_current() is None or busy:
            self.ui.bt_mod_build_folder_show.setEnabled(False)
            self.ui.bt_mod_build.setEnabled(False)
            self.ui.action_external_add.setEnabled(False)
            self.ui.action_make_web_map.setEnabled(False)
            self.action_ammo_workshop.setEnabled(False)
        else:
            self.ui.bt_mod_build_folder_show.setEnabled(True)
            self.ui.bt_mod_build.setEnabled(True)
            self.ui.action_external_add.setEnabled(True)
            self.ui.action_make_web_map.setEnabled(True)
            self.action_ammo_workshop.setEnabled(True)
        self.filter_text_changed()

    def update_select_state(self, vfs_view):
        if vfs_view is not None and vfs_view == self.vfs_view_current():
            any_selected = vfs_view.capture_count() > 0

            if not self.ui.filter_edit.hasFocus():
                self.ui.filter_edit.setText(to_unicode(vfs_view.mask))

            self.ui.bt_extract.setEnabled(any_selected)
            self.ui.bt_extract_gltf_3d.setEnabled(any_selected)
            self.ui.bt_mod_prep.setEnabled(any_selected)
            self.ui.bt_extract_folder_show.setEnabled(any_selected)
            self.ui.bt_extract_gltf_3d_folder_show.setEnabled(any_selected)
            self.ui.bt_mod_folder_show.setEnabled(any_selected)

            str_vpaths = self.vfs_view_current().capture_path_summary_str()
            self.ui.bt_extract.setText('EXTRACT: {}'.format(str_vpaths))
            self.ui.bt_extract_gltf_3d.setText('EXTRACT 3D/GLTF2: {}'.format(str_vpaths))
            self.ui.bt_mod_prep.setText('PREP MOD: {}'.format(str_vpaths))

            if self.ui.chkbx_mod_build_subset.isChecked():
                self.ui.bt_mod_build.setText('Build Mod Subset: {}'.format(str_vpaths))
                self.ui.bt_mod_build.setEnabled(any_selected)
            else:
                self.ui.bt_mod_build.setText('Build Mod All')

    def vnode_2click_selected(self, uids: List[int]):
        self.current_uids = uids
        self.ui.data_view.vnode_2click_selected(uids)

    def extract(
            self, eid, extract_dir, export_raw, export_contents, save_to_processed, save_to_text,
            export_map_full, export_map_tiles):
        if self.vfs_view_current() and self.vfs_view_current().node_selected_count() > 0:
            try:
                if export_raw:
                    nodes_export_raw(self.vfs_view_current().vfs(), self.vfs_view_current(), extract_dir)

                if export_contents:
                    nodes_export_contents(self.vfs_view_current().vfs(), self.vfs_view_current(), extract_dir)

                if export_map_full or export_map_tiles:
                    nodes_export_map(self.vfs_view_current().vfs(), self.vfs_view_current(), extract_dir, export_map_full, export_map_tiles)

                nodes_export_processed(
                    self.vfs_view_current().vfs(), self.vfs_view_current(), extract_dir,
                    allow_overwrite=False,
                    save_to_processed=save_to_processed,
                    save_to_text=save_to_text)

            except EDecaFileExists as exce:
                self.error_dialog('{} Canceled: File Exists: {}'.format(eid, exce.args))

    def extract_gltf(self, eid, extract_dir, save_to_one_dir, include_skeleton, texture_format):
        if self.vfs_view_current() and self.vfs_view_current().node_selected_count() > 0:
            try:
                nodes_export_gltf(
                    self.vfs_view_current().vfs(), self.vfs_view_current(), extract_dir,
                    allow_overwrite=False,
                    save_to_one_dir=save_to_one_dir,
                    include_skeleton=include_skeleton,
                    texture_format=texture_format)

            except EDecaFileExists as exce:
                self.error_dialog('{} Canceled: File Exists: {}'.format(eid, exce.args))

    def slot_folder_show_clicked(self, checked):
        if self.vfs_view_current() is not None:

            root = None
            path_required = False

            if self.sender() == self.ui.bt_extract_folder_show:
                root = UniPath.join(self.vfs_view_current().vfs().working_dir, 'extracted')
                path_required = True
            elif self.sender() == self.ui.bt_extract_gltf_3d_folder_show:
                root = UniPath.join(self.vfs_view_current().vfs().working_dir, 'gltf2_3d')
                path_required = True
            elif self.sender() == self.ui.bt_mod_folder_show:
                root = UniPath.join(self.vfs_view_current().vfs().working_dir, 'mod')
                path_required = True
            elif self.sender() == self.ui.bt_mod_build_folder_show:
                root = UniPath.join(self.vfs_view_current().vfs().working_dir, 'build')
                path_required = False

            if root:
                if path_required and (self.vfs_view_current().capture_count() > 0):
                    path = self.vfs_view_current().capture_path_common_prefix()
                    path = UniPath.join(root, path)
                    if not UniPath.isdir(path):
                        path = UniPath.dirname(path)
                else:
                    path = root

                #self.logger.log(f'View folder: {path}')

                if UniPath.isdir(path):
                    QDesktopServices.openUrl(QUrl(f'{path}'))
                else:
                    self.error_dialog(f'You must extract or process the files before you can use them. Directory does not exist: {path}')
                    self.logger.warning(f'Directory does not exist: {path}')
            else:
                self.logger.warning(f'There is no directory specified.')
        else:
            self.logger.warning(f'There is no VFS.')

    def slot_extract_clicked(self, checked):
        if self.vfs_view_current():
            self.extract(
                'Extraction', UniPath.join(self.vfs_view_current().vfs().working_dir, 'extracted'),
                export_raw=self.ui.chkbx_export_raw_extract.isChecked(),
                export_contents=self.ui.chkbx_export_contents_extract.isChecked(),
                save_to_processed=self.ui.chkbx_export_processed_extract.isChecked(),
                save_to_text=self.ui.chkbx_export_text_extract.isChecked(),
                export_map_full=self.ui.cmbbx_map_format.currentText().find('Full') > -1,
                export_map_tiles=self.ui.cmbbx_map_format.currentText().find('Tiles') > -1)

    def slot_extract_gltf_clicked(self, checked):
        if self.vfs_view_current():
            self.extract_gltf(
                'GLTF2 / 3D', UniPath.join(self.vfs_view_current().vfs().working_dir, 'gltf2_3d'),
                save_to_one_dir=self.ui.chkbx_export_save_to_one_dir.isChecked(),
                include_skeleton=self.ui.chkbx_export_3d_include_skeleton.isChecked(),
                texture_format=self.ui.cmbbx_texture_format.currentText())

    def slot_mod_build_subset_clicked(self, checked):
        self.update_select_state(self.vfs_view_current())

    def slot_mod_prep_clicked(self, checked):
        if self.vfs_view_current():
            self.extract(
                'Mod Prep', UniPath.join(self.vfs_view_current().vfs().working_dir, 'mod'),
                export_raw=self.ui.chkbx_export_raw_mods.isChecked(),
                export_contents=self.ui.chkbx_export_contents_mods.isChecked(),
                save_to_processed=self.ui.chkbx_export_processed_mods.isChecked(),
                save_to_text=False,
                export_map_full=False,
                export_map_tiles=False)

    def slot_mod_build_clicked(self, checked):
        try:
            subset = None
            if self.ui.chkbx_mod_build_subset.isChecked():
                subset = self.vfs_view_current().nodes_selected_uids_get()

            self.builder.build_dir(
                self.vfs_view_current().vfs(),
                UniPath.join(self.vfs_view_current().vfs().working_dir, 'mod'),
                UniPath.join(self.vfs_view_current().vfs().working_dir, 'build'),
                subset=subset,
                symlink_changed_file=False,
                do_not_build_archive=self.ui.chkbx_mod_do_not_build_archives.isChecked()
            )
            self.dialog_good('BUILD SUCCESS')
        except EDecaFileExists as ex:
            self.error_dialog('Build Failed: File Exists: {}'.format(ex.args))
        except EDecaBuildError as ex:
            self.error_dialog('Build Failed: {}'.format(ex.args))

    def filter_text_get(self):
        txt = self.ui.filter_edit.text()

        if len(txt) == 0:
            txt = '^.*$'
        else:
            if txt[0] != '^':
                txt = '^' + txt
            if txt[-1] != '$':
                txt = txt + '$'

        return txt

    def filter_text_changed(self):
        txt = self.filter_text_get()
        
        valid = False
        changed = False
        has_vfs = self.vfs_view_current() is not None

        try:
            valid = True
            re.compile(txt)  # test compile
        except re.error as err:
            valid = False

        if self.vfs_view_current():
            changed = txt != to_unicode(self.vfs_view_current().mask)

        if not valid:
            ss = 'QLineEdit {background-color: red;}'
        elif not changed:
            ss = ''
        else:
            ss = 'QLineEdit {background-color: yellow;}'

        self.ui.filter_edit.setStyleSheet(ss)
        self.ui.filter_edit.setEnabled(has_vfs)
        self.ui.filter_set_bt.setEnabled(has_vfs and valid and changed)
        self.ui.filter_clear_bt.setEnabled(has_vfs and changed)

    def filter_text_key_release(self, source, event: QKeyEvent):
        if event.text() == '\r':
            if self.ui.filter_set_bt.isEnabled():
                self.filter_text_accepted(True)
        elif event.text() == '\x1b':
            self.filter_text_cleared(True)

    def filter_text_accepted(self, checked):
        if self.vfs_view_current():
            txt = self.filter_text_get()
            self.vfs_view_current().mask_set(txt.encode('utf-8'))
            self.filter_text_changed()

    def filter_text_cleared(self, checked):
        if self.vfs_view_current():
            txt = to_unicode(self.vfs_view_current().mask)
        else:
            txt = '^.*$'
        self.ui.filter_edit.setText(txt)

    def vhash_to_vpath_text_changed(self):
        txt_in = self.ui.vhash_to_vpath_in_edit.text()

        txt_out = ''
        if self.vfs_view_current() is not None:
            try:
                val_in = int(txt_in, 0)
                strings = self.vfs_view_current().vfs().hash_string_match(hash32=val_in)
                for s in strings:
                    if len(s) > 0:
                        txt_out = s[1].decode('utf-8')
            except ValueError:
                pass

        self.ui.vhash_to_vpath_out_edit.setText(txt_out)

    @Slot()
    def project_new(self, checked):
        if os.name == 'nt':
            game_loc = 'C:/Program Files (x86)/Steam/steamapps/common/'
        else:
            game_loc = UniPath.expanduser('~/.steam/steamapps/common')

        filename = QFileDialog.getOpenFileName(self, 'Create Project ...', game_loc, 'Game EXE (*.exe *.EXE)')

        if filename is None or len(filename[0]) == 0:
            self.logger.log('No game executable file selected')
            return

        exe_path = UniPath.normpath(filename[0])
        game_dir, exe_name = UniPath.split(exe_path)
        game_info = determine_game(UniPath.join(game_dir, ''), exe_name)
        if game_info is None:
            self.logger.log('Unknown Game {}'.format(filename))
            self.error_dialog(
                'Unknown game: {}\n\nFor COTW the path must contain "theHunterCotW" (case-sensitive).'.format(exe_path))
            return

        working_dir = project_working_dir(game_info)
        if os.path.isfile(os.path.join(working_dir, 'project.json')):
            choice = self.existing_project_dialog(working_dir)
            if choice == 'open':
                self.task_start(KIND_OPEN, os.path.join(working_dir, 'project.json'))
                return
            if choice != 'rescan':
                return
            if not self.project_delete(working_dir):
                return

        self.task_start(KIND_NEW, exe_path)

    def existing_project_dialog(self, working_dir) -> str:
        """'open', 'rescan' or 'cancel'. Creating a project over an existing one only re-opens it in DECA."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle('DECA')
        text = 'A project already exists for this game:\n{}'.format(working_dir)
        if project_scan_state(os.path.join(working_dir, 'project.json')) != SCAN_COMPLETE:
            text += '\n\nIts scan is not finished: opening it resumes the scan.'
        box.setText(text)
        box.setInformativeText(
            'Open it, or delete it and scan the game again (about 40 minutes for COTW). Rescanning is needed after '
            'a change that affects the scan, such as a new dictionary in work\\filenames.txt.')
        bt_open = box.addButton('Open existing', QMessageBox.AcceptRole)
        bt_rescan = box.addButton('Delete and rescan', QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(bt_open)
        box.exec()
        if box.clickedButton() is bt_open:
            return 'open'
        if box.clickedButton() is bt_rescan:
            return 'rescan'
        return 'cancel'

    def project_delete(self, working_dir) -> bool:
        """Delete a project folder and its log (DECA writes it next to the folder: work/hplog.txt)."""
        log_file = working_dir + 'log.txt'
        answer = QMessageBox.warning(
            self, 'DECA', 'Delete these files?\n\n{}\n{}'.format(working_dir, log_file),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return False
        if self.ammo_workshop is not None and \
                UniPath.normpath(self.ammo_workshop.project_dir) == UniPath.normpath(working_dir):
            if not self.ammo_workshop_close():   # it may be reading this project's database
                return False
        if self.vfs is not None and UniPath.normpath(self.vfs.working_dir) == UniPath.normpath(working_dir):
            # the project shown is the one being deleted: release its database first
            self.project_close()
        try:
            shutil.rmtree(working_dir)
            if os.path.isfile(log_file):
                os.remove(log_file)
        except OSError as exc:
            self.error_dialog('Could not delete the project (is DB Browser or another tool using it?)\n\n{}'.format(exc))
            return False
        return True

    @Slot()
    def project_open(self, checked):

        filename = QFileDialog.getOpenFileName(self, 'Open Project ...', UniPath.join(deca_root(), '..', 'work'),
                                               'Project File (project.json)')
        if filename is not None and len(filename[0]) > 0:
            self.project_open_path(filename[0])
        else:
            self.logger.log('No project file selected')

    @Slot()
    def external_add(self, checked):
        filenames, selected_filter = QFileDialog.getOpenFileNames(self, 'Open External File ...', '.', 'Any File (*)')
        if filenames:
            for filename in filenames:
                if len(filename) > 0:
                    self.vfs_view_current().vfs().external_file_add(filename)
        else:
            self.logger.log('No file selected')

    @Slot()
    def file_gz_open(self, checked):
        filenames, selected_filter = QFileDialog.getOpenFileNames(self, 'Open GZ File ...',
                                                                  UniPath.join(deca_root(), '..', 'work'),
                                                                  'Any File (*)')

        if filenames and len(filenames[0]) > 0:
            path, _ = UniPath.split(filenames[0])
            vfs = vfs_structure_empty(path, 'GenerationZero')
            self.vfs_set(vfs)
            for filename in filenames:
                if len(filename) > 0:
                    self.vfs_view_current().vfs().external_file_add(filename)
        else:
            self.logger.log('No GenZero file selected')

    @Slot()
    def exit_app(self, checked):
        self.close()

    @Slot()
    def tool_make_web_map(self, checked):
        tool = ToolMakeWebMap(self.vfs_view_current().vfs())
        tool.make_web_map(self.vfs_view_current().vfs().working_dir, True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='DECA GUI')
    parser.add_argument('project', nargs='?', default=None,
                        help='project to open at start-up: a project.json file or the folder that contains it')
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    deca_root()

    # Qt Application
    app = QApplication([sys.argv[0]] + qt_args)
    window = MainWindow()
    window.setWindowTitle(window_title)
    window.show()
    if args.project:
        window.project_open_path(args.project)
    app.exec()

    return window.vfs
