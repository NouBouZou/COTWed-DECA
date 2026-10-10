"""
Processed export of sounds (File > Export processed).

COTWed DECA, 2026-10-10: .wavc (FSB5c) and FMOD Studio banks (riff) are rebuilt as files a player opens
(deca.ff_fsb5: Ogg Vorbis for COTW, WAV for PCM, MP3 as stored) instead of the bare FSB5 that DECA wrote:

    <path>.wavc           -> <path>.wavc.DECA.ogg            (one sample; a folder <path>.wavc.DECA\\ if several)
    <path>.fmod_bankc     -> <path>.fmod_bankc.DECA\\<sample name>.ogg   (banks without sounds: nothing)
    unnamed file          -> <hash>.dat.DECA.ogg / <hash>.dat.DECA\\

A sample whose codec or Vorbis setup packet is unknown is not rebuilt: the bare FSB5 (<path>.DECA.fsb, which
vgmstream plays) is written instead, with a warning in the log.
"""
from typing import List, Tuple

from .db_core import VfsDatabase, VfsNode
from .util import make_dir_for_file
from .path import UniPath


def _base(node: VfsNode, extract_dir) -> str:
    if node.v_path is None:
        return UniPath.join(extract_dir, '{:08X}.dat'.format(node.v_hash))
    return UniPath.join(extract_dir, node.v_path.decode('utf-8'))


def _write(vfs: VfsDatabase, ofile: str, content: bytes, allow_overwrite: bool) -> bool:
    if not allow_overwrite and UniPath.isfile(ofile):
        vfs.logger.log('WARNING: {} exists, skipping'.format(ofile))
        return False
    vfs.logger.log('Exporting {}'.format(ofile))
    make_dir_for_file(ofile)
    with open(ofile, 'wb') as fo:
        fo.write(content)
    return True


def fsb_files(fsb, base: str) -> List[Tuple[str, bytes]]:
    """(file, content) of every sample of an FSB5: one file next to `base`, or a folder for several samples.
    Raises deca.ff_fsb5.EFsb5 if a sample cannot be rebuilt."""
    if len(fsb.samples) == 1:
        return [('{}.DECA.{}'.format(base, fsb.extension()), fsb.sample_file(fsb.samples[0]))]
    folder = base + '.DECA'
    return [(UniPath.join(folder, name), fsb.sample_file(s)) for s, name in zip(fsb.samples, fsb.file_names())]


def node_export_fsb5c_processed(
        vfs: VfsDatabase,
        node: VfsNode,
        extract_dir,
        allow_overwrite=False
):
    from .ff_fsb5 import EFsb5, wavc
    with vfs.file_obj_from(node) as f:
        buffer = f.read(node.size_u)
    base = _base(node, extract_dir)
    try:
        files = fsb_files(wavc(buffer), base)
    except EFsb5 as e:
        vfs.logger.log('WARNING: {}: {}; bare FSB5 written'.format(base, e))
        files = [('{}.DECA.fsb'.format(base), buffer[16:])]
    for ofile, content in files:
        _write(vfs, ofile, content, allow_overwrite)


def node_export_fmod_bank_processed(
        vfs: VfsDatabase,
        node: VfsNode,
        extract_dir,
        allow_overwrite=False
):
    import io
    from .ff_fmod_bank import sound_fsb_full
    from .ff_fsb5 import EFsb5
    with vfs.file_obj_from(node) as f:
        buffer = f.read(node.size_u)
    base = _base(node, extract_dir)
    fsb = sound_fsb_full(io.BytesIO(buffer), len(buffer))
    if fsb is None:
        vfs.logger.log('{}: FMOD bank without sounds, nothing exported'.format(base))
        return
    folder = base + '.DECA'
    failed = 0
    for s, name in zip(fsb.samples, fsb.file_names()):
        try:
            content = fsb.sample_file(s)
        except EFsb5 as e:
            vfs.logger.log('WARNING: {} sample {} ({}): {}'.format(base, s.index, s.name, e))
            failed += 1
            continue
        _write(vfs, UniPath.join(folder, name), content, allow_overwrite)
    if failed:      # the samples not rebuilt stay readable with vgmstream
        _write(vfs, '{}.DECA.fsb'.format(base), fsb.raw(), allow_overwrite)
