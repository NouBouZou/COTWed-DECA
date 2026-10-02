import re
from typing import TypeVar, Optional

from .util import DecaSignal, to_unicode, gc_paused
from .path import UniPath
from .ff_types import *
from .db_core import VfsDatabase, VfsNode
from .ff_adf import AdfDatabase

NodeListElement = TypeVar('NodeListElement', str, bytes, VfsNode)

# Masks that keep every path: no REGEXP_MATCH call is needed for them.
MATCH_ALL_MASKS = frozenset(['', '.*', '^.*', '.*$', '^.*$'])

_REGEX_META = frozenset('.^$*+?{}[]\\|()')
_REGEX_QUANTIFIERS = frozenset('*+?{')
# escaped characters that stand for themselves (r'\.' is a dot); other escapes (\d, \w, \b...) end the prefix
_REGEX_LITERAL_ESCAPES = frozenset('.^$*+?{}[]\\|()/-_ ')


def mask_is_match_all(mask) -> bool:
    return mask is None or to_unicode(mask) in MATCH_ALL_MASKS


def regex_literal_prefix(mask) -> str:
    """
    Literal text every match of ``mask`` must start with (re.match semantics), or '' if none can be deduced.

    Used to add ``v_path LIKE 'prefix%'`` in front of REGEXP_MATCH: LIKE runs inside SQLite, so the Python
    callback only sees the rows that can match. The prefix is a necessary condition only; the regular
    expression is still applied, so a conservative (short) prefix never changes the result.
    """
    s = to_unicode(mask) or ''
    i = 1 if s.startswith('^') else 0
    out = []
    while i < len(s):
        c = s[i]
        if c == '\\':
            if i + 1 < len(s) and s[i + 1] in _REGEX_LITERAL_ESCAPES:
                lit, step = s[i + 1], 2
            else:
                break
        elif c in _REGEX_META:
            break
        else:
            lit, step = c, 1
        nxt = s[i + step] if i + step < len(s) else ''
        if nxt in _REGEX_QUANTIFIERS:
            break  # 'ab?' : the last literal is optional or repeated, it cannot be part of the prefix
        if nxt == '|':
            return ''  # alternation at top level: 'a|b' has no common prefix
        out.append(lit)
        i += step
    if '|' in s[i:]:
        # an alternation later in the expression may apply to the whole pattern ('ab.*|cd'): stay safe
        return ''
    return ''.join(out)


def like_escape(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class VfsView:
    def __init__(self, *params, **kwargs):
        self._vfs: Optional[VfsDatabase] = None
        self._adf_db: Optional[AdfDatabase] = None
        self._vfs_view: Optional[VfsView] = None
        self.capture = None
        self.mask = None
        self.parent_id = None
        self._uids_dirty = True
        self._nodes_visible_dirty = True
        self._nodes_visible = dict()
        self._nodes_visible_uids = set()
        self._nodes_visible_uids_no_vpaths = set()
        self._nodes_selected_dirty = True
        self._nodes_selected = dict()
        self._nodes_selected_uids = set()
        self._nodes_selected_uids_no_vpaths = set()

        self.source_changed = True
        self.signal_visible_changed = DecaSignal()
        self.signal_selection_changed = DecaSignal()

        self.parent_id = kwargs.get('parent_id', None)

        if len(params) == 1:
            vfs_view = params[0]
            assert isinstance(vfs_view, VfsView)
            self._vfs_view = vfs_view
            self._vfs = vfs_view._vfs
            self._adf_db = vfs_view._adf_db
            self.capture = vfs_view.capture
            self.mask = vfs_view.mask
            vfs_view.signal_visible_changed.connect(self, lambda x: x.slot_visible_changed())
        elif len(params) == 3:
            vfs = params[0]
            assert isinstance(vfs, VfsDatabase)
            self._vfs_view = None
            self._vfs = vfs
            self._adf_db = AdfDatabase(vfs)
            self.capture = params[1]
            self.mask = params[2]
            vfs.db_changed_signal.connect(self, lambda x: x.slot_visible_changed())
        else:
            raise Exception('Incorrect Parameter count to VfsView')

    def vfs_view(self):
        return self._vfs_view

    def vfs(self):
        return self._vfs

    def adf_db(self):
        return self._adf_db

    def slot_visible_changed(self):
        print('VfsView.slot_visible_changed')
        self.source_changed = True
        self.signal_visible_changed.call()

    def mask_set(self, mask):
        print('VfsView.mask_set')
        self._nodes_visible_dirty = True
        self.mask = mask
        self.capture = set()
        self.signal_visible_changed.call()

    def capture_count(self):
        if self.capture is None:
            return 0
        else:
            return len(self.capture)

    def capture_set(self, capture):
        self._nodes_selected_dirty = True
        self.capture = capture
        self.signal_selection_changed.call()

    def capture_path_summary_str(self):
        count = self.capture_count()
        if count == 0:
            return '-- N/A --'
        elif count == 1:
            capture = self.capture[0]
            if isinstance(capture, VfsNode):
                return to_unicode(capture.v_path)
            else:
                if len(capture) == 0:
                    return '-- ALL --'
                elif self.capture[0][-1] == '/':
                    return to_unicode(capture) + '*'
                else:
                    return to_unicode(capture)
        else:
            return '-- MULTIPLE -- '

    def capture_path_common_prefix(self):
        if self.capture is not None:
            paths = [capture.v_path if isinstance(capture, VfsNode) else capture for capture in self.capture]
            return to_unicode(UniPath.commonpath(paths))
        return str()

    @staticmethod
    def _map_add(nodes_map, uid, v_path, file_type, parent_offset):
        """Group a named node under its path: [hard nodes (real data), symlinks / nodes without data]."""
        v_path = to_unicode(v_path)
        lst = nodes_map.get(v_path, None)
        if lst is None:
            lst = [[], []]
            nodes_map[v_path] = lst

        if file_type != FTYPE_SYMLINK and parent_offset is not None:
            lst[0].append(uid)
        else:
            lst[1].append(uid)

    def node_accumulate(self, nodes_map, nodes_uids, nodes_uids_no_vpath, mask=None, pid_in=None, capture=None):
        """Selection: nodes matching ``capture`` (a VfsNode or a LIKE pattern); the mask only filters the map."""
        if isinstance(capture, VfsNode):
            nodes_all = [(capture.uid, capture.v_path, capture.p_path, capture.file_type, capture.offset)]
        else:
            nodes_all = self.vfs().nodes_where_match(
                v_path_like=capture,
                pid_in=pid_in,
                output='node_id, v_path, p_path, file_type, parent_offset',
            )

        match_all = mask_is_match_all(mask)
        mask_expr = None if match_all else re.compile(to_unicode(mask))
        for uid, v_path, p_path, file_type, parent_offset in nodes_all:
            nodes_uids.add(uid)

            if v_path is None:
                if p_path is None:
                    nodes_uids_no_vpath.add(uid)
            elif match_all or mask_expr.match(to_unicode(v_path)) is not None:
                self._map_add(nodes_map, uid, v_path, file_type, parent_offset)

    def _visible_uids_update(self):
        """
        All nodes of the view, and those with neither a virtual nor a physical path.

        These sets never depended on the mask (the Raw and Non-Mapped lists ignore the filter), so they are only
        rebuilt when the database changes, not on every filter change.
        """
        if self.parent_id is None:
            where, params = '', []
        else:
            where, params = ' WHERE parent_id == (?)', [self.parent_id]
        with gc_paused():
            rows = self.vfs().db_query_all(
                'SELECT node_id, (v_path IS NULL AND p_path IS NULL) FROM core_nodes' + where, params,
                dbg='VfsView._visible_uids_update')
            self._nodes_visible_uids = {row[0] for row in rows}
            self._nodes_visible_uids_no_vpaths = {row[0] for row in rows if row[1]}
            del rows

    def _visible_map_update(self):
        """
        Named nodes whose path matches the mask, grouped by path.

        The mask is applied by SQLite (REGEXP_MATCH, same re.match semantics as before) so that only matching rows
        reach Python. Before, every node of the project was read and tested in Python on each filter change.
        """
        wheres = ['v_path IS NOT NULL']
        params = []
        if self.parent_id is not None:
            wheres.append('parent_id == (?)')
            params.append(self.parent_id)
        if not mask_is_match_all(self.mask):
            prefix = regex_literal_prefix(self.mask)
            if prefix:
                # CAST: on a BLOB value (paths written as bytes) LIKE never matches without it (tested, SQLite 3.45)
                wheres.append("CAST(v_path AS TEXT) LIKE (?) ESCAPE '\\'")
                params.append(like_escape(prefix) + '%')
            wheres.append('REGEXP_MATCH((?), v_path)')
            params.append(to_unicode(self.mask))
        with gc_paused():
            rows = self.vfs().db_query_all(
                'SELECT node_id, v_path, file_type, parent_offset FROM core_nodes WHERE ' + ' AND '.join(wheres), params,
                dbg='VfsView._visible_map_update')
            # Same grouping as _map_add, inlined: this loop runs once per named node of the project.
            nodes_map = dict()
            get = nodes_map.get
            for uid, v_path, file_type, parent_offset in rows:
                if isinstance(v_path, bytes):
                    v_path = v_path.decode('utf-8')
                lst = get(v_path)
                if lst is None:
                    lst = nodes_map[v_path] = [[], []]
                lst[0 if (file_type != FTYPE_SYMLINK and parent_offset is not None) else 1].append(uid)
            del rows
        self._nodes_visible = nodes_map

    def node_update(self):
        selection_changed = False
        if self.source_changed:
            self._adf_db.load_from_database(self._vfs)
            self.source_changed = False
            self._uids_dirty = True
            self._nodes_visible_dirty = True
            self._nodes_selected_dirty = True
            selection_changed = True

        if self._uids_dirty:
            self._visible_uids_update()
            self._uids_dirty = False

        if self._nodes_visible_dirty:
            self.vfs().logger.log(f'Nodes Visible Begin')
            self._visible_map_update()
            self._nodes_visible_dirty = False
            self._nodes_selected_dirty = True
            selection_changed = True
            self.vfs().logger.log(f'Nodes Visible End')

        if self._nodes_selected_dirty:
            self.vfs().logger.log(f'Nodes Selected Begin')

            # selected nodes
            self._nodes_selected = dict()
            self._nodes_selected_uids = set()
            self._nodes_selected_uids_no_vpaths = set()

            if self.capture is not None:
                for capture in self.capture:

                    if isinstance(capture, str):
                        if len(capture) > 0 and capture[-1] == '/':
                            capture = capture + r'%'
                        capture = capture.encode('utf-8')

                    self.node_accumulate(
                        self._nodes_selected, self._nodes_selected_uids, self._nodes_selected_uids_no_vpaths,
                        mask=self.mask, pid_in=self.parent_id, capture=capture)

            self._nodes_selected_dirty = False
            self.vfs().logger.log(f'Nodes Selected End')

        if selection_changed:
            self.signal_selection_changed.call()

    def node_visible_count(self):
        self.node_update()
        return len(self._nodes_visible)

    def nodes_visible_map_get(self):
        self.node_update()
        return self._nodes_visible

    def nodes_visible_uids_get(self):
        self.node_update()
        return self._nodes_visible_uids

    def nodes_visible_uids_no_vpath_get(self):
        self.node_update()
        return self._nodes_visible_uids_no_vpaths

    def node_visible_has(self, uids):
        self.node_update()
        for uid in uids:
            if uid in self._nodes_visible_uids:
                return True
        return False

    def node_selected_count(self):
        self.node_update()
        return len(self._nodes_selected)

    def nodes_selected_get(self):
        self.node_update()
        return self._nodes_selected

    def nodes_selected_uids_get(self):
        self.node_update()
        return self._nodes_selected_uids

    def node_selected_has(self, uids):
        self.node_update()
        for uid in uids:
            if uid in self._nodes_selected_uids:
                return True
        return False

    def nodes_visible_uids_sorted(self, order_by: str, descending: bool = False, no_vpath_only: bool = False):
        """
        Same nodes as nodes_visible_uids_get() (or the no-path subset), ordered by a SQL expression of core_nodes.

        Sorting is done by SQLite: sorting in Qt would ask the model for one value per row, i.e. one query per node.
        ``order_by`` must come from a fixed list in the caller, never from user input.
        """
        wheres, params = [], []
        if self.parent_id is not None:
            wheres.append('parent_id == (?)')
            params.append(self.parent_id)
        if no_vpath_only:
            wheres.append('v_path IS NULL AND p_path IS NULL')
        where = (' WHERE ' + ' AND '.join(wheres)) if wheres else ''
        direction = 'DESC' if descending else 'ASC'
        rows = self.vfs().db_query_all(
            'SELECT node_id FROM core_nodes{} ORDER BY {} {}, node_id {}'.format(where, order_by, direction, direction),
            params, dbg='VfsView.nodes_visible_uids_sorted')
        return [row[0] for row in rows]

    def node_where_uid(self, uid):
        return self.vfs().node_where_uid(uid)

    def nodes_where_uid(self, uids):
        return self.vfs().nodes_where_uid(uids)

    def lookup_note_from_file_path(self, path):
        return self.vfs().lookup_note_from_file_path(path)

    # def expand_vfs_paths(self):
    #     vos = []
    #
    #     for v in self.paths:
    #         id_pat = v
    #
    #         if isinstance(id_pat, str):
    #             id_pat = v.encode('ascii')
    #
    #         if isinstance(id_pat, bytes):
    #             nodes_all = self.vfs().nodes_where_match(v_path_like=id_pat, v_path_regexp=self.mask)
    #             nodes = []
    #             for node in nodes_all:
    #                 if node.file_type != FTYPE_SYMLINK and node.offset is not None:
    #                     nodes.append(node)
    #             nodes = dict([(n.v_path, n) for n in nodes])
    #             nodes = list(nodes.values())
    #             vos += nodes
    #         else:
    #             vos.append(v)
    #
    #     return vos

    # def find_vfs_node(self, v):
    #     node = None
    #     path = None
    #     if isinstance(v, bytes):
    #         path = v
    #     elif isinstance(v, VfsNode):
    #         node = v
    #     else:
    #         raise NotImplementedError('find_vfs_node: Could not extract {}'.format(v))
    #
    #     if path is not None:
    #         matching_nodes = self.vfs().nodes_where_match(v_path=path)
    #         node = None
    #         for matching_node in matching_nodes:
    #             if matching_node.offset is not None:
    #                 node = matching_node
    #                 break
    #         if node is None:
    #             raise EDecaFileMissing('find_vfs_node: Missing {}'.format(path.decode('utf-8')))
    #
    #     return node
