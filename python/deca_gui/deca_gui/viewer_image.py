from .viewer import *
from deca.ff_avtx import Ddsc
import deca.ff_avtx
import os
from PySide6.QtCore import Qt, QPoint, QRectF, Signal
from PySide6.QtGui import QImage, QPixmap, QBrush, QColor
from PySide6.QtWidgets import \
    QGraphicsView, QSizePolicy, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QGraphicsScene, QGraphicsPixmapItem, \
    QFrame, QWidget, QToolButton, QLineEdit, QCheckBox, QPlainTextEdit
from deca.ff_types import *
from deca.errors import EDecaFileMissing
from deca.file import ArchiveFile

# Initial version from
# https://stackoverflow.com/questions/35508711/how-to-enable-pan-and-zoom-in-a-qgraphicsview/35514531#35514531


class PhotoViewer(QGraphicsView):
    photoClicked = Signal(QPoint)

    def __init__(self, parent):
        super(PhotoViewer, self).__init__(parent)
        self._zoom = 0
        self._empty = True
        self._scene = QGraphicsScene(self)
        self._photo = QGraphicsPixmapItem()
        self._scene.addItem(self._photo)
        self.setScene(self._scene)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setFrameShape(QFrame.NoFrame)

    def hasPhoto(self):
        return not self._empty

    def fitInView(self, scale=True):
        rect = QRectF(self._photo.pixmap().rect())
        if not rect.isNull():
            self.setSceneRect(rect)
            if self.hasPhoto():
                unity = self.transform().mapRect(QRectF(0, 0, 1, 1))
                self.scale(1 / unity.width(), 1 / unity.height())
                viewrect = self.viewport().rect()
                scenerect = self.transform().mapRect(rect)
                factor = min(viewrect.width() / scenerect.width(),
                             viewrect.height() / scenerect.height())
                self.scale(factor, factor)
            self._zoom = 0

    def setPhoto(self, pixmap=None):
        self._zoom = 0
        if pixmap and not pixmap.isNull():
            self._empty = False
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._photo.setPixmap(pixmap)
        else:
            self._empty = True
            self.setDragMode(QGraphicsView.NoDrag)
            self._photo.setPixmap(QPixmap())
        self.fitInView()

    def wheelEvent(self, event):
        if self.hasPhoto():
            if event.angleDelta().y() > 0:
                factor = 1.25
                self._zoom += 1
            else:
                factor = 0.8
                self._zoom -= 1
            if self._zoom > 0:
                self.scale(factor, factor)
            elif self._zoom == 0:
                self.fitInView()
            else:
                self._zoom = 0

    def toggleDragMode(self):
        if self.dragMode() == QGraphicsView.ScrollHandDrag:
            self.setDragMode(QGraphicsView.NoDrag)
        elif not self._photo.pixmap().isNull():
            self.setDragMode(QGraphicsView.ScrollHandDrag)

    def mousePressEvent(self, event):
        if self._photo.isUnderMouse():
            self.photoClicked.emit(QPoint(event.pos()))
        super(PhotoViewer, self).mousePressEvent(event)


class Window(QWidget):
    def __init__(self):
        super(Window, self).__init__()
        self.viewer = PhotoViewer(self)

        # 'Load image' button
        self.btnLoad = QToolButton(self)
        self.btnLoad.setText('Load image')
        self.btnLoad.clicked.connect(self.loadImage)

        # Button to change from drag/pan to getting pixel info
        self.btnPixInfo = QToolButton(self)
        self.btnPixInfo.setText('Enter pixel info mode')
        self.btnPixInfo.clicked.connect(self.pixInfo)
        self.editPixInfo = QLineEdit(self)
        self.editPixInfo.setReadOnly(True)
        self.viewer.photoClicked.connect(self.photoClicked)

        # Arrange layout
        VBlayout = QVBoxLayout(self)
        VBlayout.addWidget(self.viewer)
        HBlayout = QHBoxLayout()
        HBlayout.setAlignment(Qt.AlignLeft)
        HBlayout.addWidget(self.btnLoad)
        HBlayout.addWidget(self.btnPixInfo)
        HBlayout.addWidget(self.editPixInfo)
        VBlayout.addLayout(HBlayout)

    def loadImage(self):
        self.viewer.setPhoto(QPixmap('image.jpg'))

    def pixInfo(self):
        self.viewer.toggleDragMode()

    def photoClicked(self, pos):
        if self.viewer.dragMode() == QGraphicsView.NoDrag:
            self.editPixInfo.setText('%d, %d' % (pos.x(), pos.y()))


def node_data(vfs, vnode) -> bytes:
    with vfs.file_obj_from(vnode) as f:
        return f.read(vnode.size_u)


class _RawMip:
    """What update_image() needs from a ddsc mip, for a raw array (COTW *.rawc)."""
    def __init__(self, data, label):
        self.data = data
        self.size_y, self.size_x = data.shape[0], data.shape[1]
        self.depth_idx = self.depth_cnt = None
        self.itype = label


class _RawImage:
    def __init__(self, mips):
        self.mips = mips


def rawc_image(vfs, vnode):
    """*.rawc (raw terrain arrays): layout guessed by deca.ff_rawc (no header), shown in grey levels."""
    import deca.ff_rawc as R
    with vfs.file_obj_from(vnode) as f:
        data = f.read(vnode.size_u)
    name = vnode.v_path.decode('utf-8', 'replace') if isinstance(vnode.v_path, bytes) else (vnode.v_path or '')
    layout = R.guess_layout(data, name)
    if layout is None:
        raise ValueError('rawc: no square layout for {} bytes'.format(len(data)))
    return _RawImage([_RawMip(R.to_rgba8(layout), 'rawc ' + R.describe(layout))])


class _GuessedMip:
    """A level of a texture without header (deca.ff_bc_raw): decoded when the dropdown shows it."""
    def __init__(self, data: bytes, guess, level: int, rank: int, count: int):
        self._raw, self._guess, self._level = data, guess, level
        self.size_x, self.size_y = guess.layout.level_sizes()[level]
        self.depth_idx = self.depth_cnt = None
        tag = 'best guess' if rank == 0 else 'guess {}/{}'.format(rank + 1, count)
        self.itype = '{}, {} level {}, {}, score {:.2f}'.format(
            guess.format_name, 'mip' if level else 'largest', level, tag, guess.score)
        self._data = None

    @property
    def data(self):
        if self._data is None:
            import deca.ff_bc_raw as R
            img = R.decode_level(self._raw, self._guess.layout, self._guess.fmt, self._level)
            if self._guess.fmt in (80, 83):        # BC4 / BC5: no alpha
                img[:, :, 3] = 255
            self._data = img
        return self._data


def guessed_image(data: bytes, alternatives: int = 5):
    """Texture without header: the levels of the best guess, then the largest level of the next guesses."""
    import deca.ff_bc_raw as R
    guesses = R.guess(data)
    if not guesses:
        raise ValueError('{} bytes: no block-compressed mip chain has this size'.format(len(data)))
    best = guesses[0]
    mips = [_GuessedMip(data, best, i, 0, len(guesses)) for i in range(best.layout.levels)]
    mips += [_GuessedMip(data, g, 0, k, len(guesses)) for k, g in enumerate(guesses[1:1 + alternatives], 1)]
    return _RawImage(mips)


def is_guessed_texture(vnode) -> bool:
    """Untyped file whose size is that of a block-compressed mip chain (unnamed .hmddsc data, COTW)."""
    from deca.ff_bc_raw import is_bc_raw_size
    return vnode.file_type in (None, FTYPE_NOT_HANDLED) and vnode.size_u is not None and is_bc_raw_size(vnode.size_u)


class DataViewerImage(DataViewer):
    def __init__(self):
        DataViewer.__init__(self)

        self.ddsc = None

        self.image_display = PhotoViewer(self)
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.image_display.setSizePolicy(size)

        self.select_dropdown = QComboBox()
        self.select_dropdown.setEditable(False)
        self.select_dropdown.addItem('A')
        self.select_dropdown.addItem('B')
        self.select_dropdown.addItem('C')
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.select_dropdown.setSizePolicy(size)
        self.select_dropdown.currentIndexChanged.connect(self.select_dropdown_current_index_changed)

        self.checkbox_opaque = QCheckBox(self)
        self.checkbox_opaque.setText('Opaque')
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.checkbox_opaque.setSizePolicy(size)
        self.checkbox_opaque.setChecked(True)
        self.checkbox_opaque.clicked.connect(self.color_control_clicked)

        self.checkbox_show_r = QCheckBox(self)
        self.checkbox_show_r.setText('Red')
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.checkbox_show_r.setSizePolicy(size)
        self.checkbox_show_r.setChecked(True)
        self.checkbox_show_r.clicked.connect(self.color_control_clicked)

        self.checkbox_show_g = QCheckBox(self)
        self.checkbox_show_g.setText('Green')
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.checkbox_show_g.setSizePolicy(size)
        self.checkbox_show_g.setChecked(True)
        self.checkbox_show_g.clicked.connect(self.color_control_clicked)

        self.checkbox_show_b = QCheckBox(self)
        self.checkbox_show_b.setText('Blue')
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.checkbox_show_b.setSizePolicy(size)
        self.checkbox_show_b.setChecked(True)
        self.checkbox_show_b.clicked.connect(self.color_control_clicked)

        self.color_layout = QHBoxLayout()
        self.color_layout.addWidget(self.checkbox_opaque)
        self.color_layout.addWidget(self.checkbox_show_r)
        self.color_layout.addWidget(self.checkbox_show_g)
        self.color_layout.addWidget(self.checkbox_show_b)

        # COTWed DECA: the AVTX header, field by field (deca.ff_avtx.avtx_header_lines); hidden for other images
        self.header_text = QPlainTextEdit(self)
        self.header_text.setReadOnly(True)
        self.header_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.header_text.setMaximumHeight(self.header_text.fontMetrics().lineSpacing() * 7 + 12)
        self.header_text.setSizePolicy(QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed))
        self.header_text.hide()

        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(self.image_display)
        self.main_layout.addWidget(self.select_dropdown)
        self.main_layout.addLayout(self.color_layout)
        self.main_layout.addWidget(self.header_text)
        self.setLayout(self.main_layout)

        self.update_image()

    def update_image(self):
        v = self.select_dropdown.currentIndex()
        if self.ddsc is not None and 0 <= v < len(self.ddsc.mips):
            npimp = self.ddsc.mips[v].data
            if npimp is not None:
                npimp = npimp.copy()
                if self.checkbox_opaque.isChecked() and npimp.shape[2] == 4:
                    npimp[:, :, 3] = 0xFF
                if not self.checkbox_show_r.isChecked():
                    npimp[:, :, 0] = 0
                if not self.checkbox_show_g.isChecked():
                    npimp[:, :, 1] = 0
                if not self.checkbox_show_b.isChecked():
                    npimp[:, :, 2] = 0

                if npimp.shape[2] == 3:
                    frmt = QImage.Format_RGB888
                elif npimp.shape[2] == 4:
                    frmt = QImage.Format_RGBA8888
                else:
                    raise Exception('Unhandled byte counts for image')

                qimg = QImage(npimp.data, npimp.shape[1], npimp.shape[0], npimp.shape[1] * npimp.shape[2], frmt)
                pixmap = QPixmap.fromImage(qimg)
                self.image_display.setPhoto(pixmap)

    def select_dropdown_current_index_changed(self, v):
        self.update_image()

    def color_control_clicked(self, checked):
        self.update_image()

    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        self.ddsc = None
        self.select_dropdown.clear()

        if vnode.file_type in {FTYPE_ATX, FTYPE_HMDDSC}:
            try:
                self.ddsc = deca.ff_avtx.image_load(vfs, vnode)
            except EDecaFileMissing:
                # COTW: 775 .hmddsc have no .ddsc in the game (no header): layout guessed
                self.ddsc = guessed_image(node_data(vfs, vnode))
        elif vnode.file_type in {FTYPE_BMP, FTYPE_DDS, FTYPE_AVTX}:
            self.ddsc = deca.ff_avtx.image_load(vfs, vnode)
        elif vnode.file_type == FTYPE_RAWC:
            self.ddsc = rawc_image(vfs, vnode)
        elif is_guessed_texture(vnode):
            self.ddsc = guessed_image(node_data(vfs, vnode))

        header_lines = []
        if self.ddsc is not None and getattr(self.ddsc, 'header', None) is not None:
            header_lines = deca.ff_avtx.avtx_header_lines(self.ddsc.header)
        self.header_text.setPlainText('\n'.join(header_lines))
        self.header_text.setVisible(bool(header_lines))

        if self.ddsc is not None and self.ddsc.mips is not None:
            first_valid = None
            for i in range(len(self.ddsc.mips)):
                mip = self.ddsc.mips[i]
                if first_valid is None and mip.data is not None:
                    first_valid = i
                depth_info = ''
                if mip.depth_idx is not None and mip.depth_cnt is not None:
                    depth_info = 'd:{}/{} '.format(mip.depth_idx, mip.depth_cnt)
                self.select_dropdown.addItem('{}x{} {}({})'.format(mip.size_y, mip.size_x, depth_info, mip.itype))

            self.select_dropdown.setCurrentIndex(first_valid)
