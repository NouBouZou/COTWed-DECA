from .viewer import *
from deca.db_types import (
    node_flag_compression_type_mask, node_flag_compression_flag_mask, node_flag_compression_flag_shift,
    node_flag_temporary_file, node_flag_processed_file_raw_no_name, node_flag_processed_file_raw_with_name,
    node_flag_processed_file_type, node_flag_processed_file_specific)
from deca.dxgi_types import dxgi_name_db
from deca.ff_types import ftype_adf_family
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QTextEdit
from PySide6.QtGui import QFont

MAX_ANCESTORS = 32
MAX_COPIES_LISTED = 20

FLAG_NAMES = (
    (node_flag_temporary_file, 'temporary file'),
    (node_flag_processed_file_raw_no_name, 'type searched (no name)'),
    (node_flag_processed_file_raw_with_name, 'type searched (with name)'),
    (node_flag_processed_file_type, 'explored'),
    (node_flag_processed_file_specific, 'specific processing'),
)


def hash_text(vnode: VfsNode) -> str:
    if vnode.v_hash is None:
        return ''
    try:
        return vnode.v_hash_to_str()
    except NotImplementedError:  # hash width bits missing from the flags
        return '{:x} (unknown width)'.format(vnode.v_hash)


def node_label(vnode: VfsNode) -> str:
    """Short description of a node: its path if it has one, otherwise its type and hash."""
    if vnode.v_path is not None:
        return vnode.v_path.decode('utf-8', 'replace')
    if vnode.p_path is not None:
        return vnode.p_path
    parts = [vnode.file_type or 'unknown type']
    if vnode.v_hash is not None:
        parts.append('hash ' + hash_text(vnode))
    return '(no name) ' + ', '.join(parts)


def flags_text(flags: int) -> str:
    names = [name for bit, name in FLAG_NAMES if flags & bit]
    compression = flags & node_flag_compression_type_mask
    compression_flag = (flags & node_flag_compression_flag_mask) >> node_flag_compression_flag_shift
    if compression or compression_flag:
        names.append('compression type {} flag {}'.format(compression, compression_flag))
    return '0x{:08x}{}'.format(flags, ' (' + ', '.join(names) + ')' if names else '')


def magic_text(magic) -> str:
    if magic is None:
        return ''
    raw = int(magic).to_bytes(4, 'little')
    printable = ''.join(chr(b) if 0x20 <= b < 0x7f else '.' for b in raw)
    return '{} "{}"'.format(raw.hex(' ').upper(), printable)


def sub_type_text(vnode: VfsNode) -> str:
    if vnode.file_sub_type is None:
        return ''
    if vnode.file_type in ftype_adf_family:
        return '0x{:08x}'.format(vnode.file_sub_type)
    return '{} ({})'.format(dxgi_name_db.get(vnode.file_sub_type, 'UNKNOWN'), vnode.file_sub_type)


def node_info_text(vfs: VfsProcessor, vnode: VfsNode) -> str:
    """Everything the database knows about a node: fields, ancestors, children and copies of the same file."""
    rows = [
        ('Node', str(vnode.uid)),
        ('Virtual path', vnode.v_path.decode('utf-8', 'replace') if vnode.v_path is not None else '(no name)'),
        ('Physical path', vnode.p_path or ''),
        ('Hash', hash_text(vnode)),
        ('Extension hash', '{:08x}'.format(vnode.ext_hash) if vnode.ext_hash is not None else ''),
        ('Type', vnode.file_type or '(not recognised)'),
        ('Sub type', sub_type_text(vnode)),
        ('Magic', magic_text(vnode.magic)),
        ('Size', '{} bytes'.format(vnode.size_u) if vnode.size_u is not None else ''),
        ('Stored size', '{} bytes'.format(vnode.size_c) if vnode.size_c is not None else ''),
        ('Offset in parent', '0x{:x}'.format(vnode.offset) if vnode.offset is not None else ''),
        ('Index in parent', str(vnode.index) if vnode.index is not None else ''),
        ('Flags', flags_text(vnode.flags or 0)),
        ('Content hash', vnode.content_hash or ''),
        ('Used at runtime depth', str(vnode.used_at_runtime_depth) if vnode.used_at_runtime_depth is not None else ''),
    ]
    width = max(len(k) for k, _ in rows)
    lines = ['{:<{w}}  {}'.format(k, v, w=width) for k, v in rows]

    lines += ['', 'Ancestors (parent first)']
    pid, depth = vnode.pid, 0
    while pid is not None and depth < MAX_ANCESTORS:
        parent = vfs.node_where_uid(pid)
        if parent is None:
            lines.append('  {:>8}  (missing from the database)'.format(pid))
            break
        lines.append('  {:>8}  {:<8} {}'.format(parent.uid, parent.file_type or '', node_label(parent)))
        pid, depth = parent.pid, depth + 1
    if depth == 0 and vnode.pid is None:
        lines.append('  (none: top-level node)')

    n_children = vfs.db_query_one(
        'SELECT COUNT(*) FROM core_nodes WHERE parent_id == (?)', [vnode.uid], dbg='node_info_children')[0]
    lines += ['', 'Children: {}'.format(n_children)]

    if vnode.v_hash is not None:
        copies = vfs.db_query_all(
            'SELECT node_id, parent_id FROM core_nodes WHERE v_hash == (?) AND node_id != (?) ORDER BY node_id',
            [vnode.v_hash, vnode.uid], dbg='node_info_copies')
        lines += ['', 'Other nodes with the same hash: {}'.format(len(copies))]
        for uid, parent_id in copies[:MAX_COPIES_LISTED]:
            parent = vfs.node_where_uid(parent_id) if parent_id is not None else None
            lines.append('  {:>8}  in {}'.format(uid, node_label(parent) if parent is not None else '(top level)'))
        if len(copies) > MAX_COPIES_LISTED:
            lines.append('  ... {} more'.format(len(copies) - MAX_COPIES_LISTED))

    return '\n'.join(lines)


class DataViewerInfo(DataViewer):
    def __init__(self):
        DataViewer.__init__(self)

        self.text_box = QTextEdit()
        self.text_box.setReadOnly(True)
        font = QFont("Courier", 8)
        font.setStyleHint(QFont.Courier)
        self.text_box.setFont(font)
        self.text_box.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        size = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.text_box.setSizePolicy(size)

        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(self.text_box)
        self.setLayout(self.main_layout)

    def vnode_process(self, vfs: VfsProcessor, vnode: VfsNode):
        self.text_box.setPlainText(node_info_text(vfs, vnode))
