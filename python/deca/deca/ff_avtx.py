import os
import io
import struct
from typing import List
import pprint
import numpy as np
from PIL import Image
from .path import UniPath
from .file import ArchiveFile
from .errors import *
from .ff_types import *
from .db_core import VfsDatabase, VfsNode
from .util import make_dir_for_file
from .dxgi_types import *

import deca.dxgi


class DecaImage:
    def __init__(
            self, sx=None, sy=None, depth_cnt=None, depth_idx=None, surface_id=None, pixel_format=None,
            itype=None, data=None, raw_data=None, filename=None):
        self.size_x = sx
        self.size_y = sy
        self.depth_cnt = depth_cnt
        self.depth_idx = depth_idx
        self.surface_id = surface_id
        self.pixel_format = pixel_format
        self.itype = itype
        self.data = data
        self.raw_data = raw_data
        self.filename = filename

    def pil_image(self):
        return Image.fromarray(self.data)


# COTWed DECA (2026-10-29) : en-tete AVTX de theHunter: COTW, champ par champ (audits 27-textures et 28-textures, toutes
# les 27 631 textures du jeu) :
#   0x00 'AVTX' ; 0x04 u16 version (1) ;
#   0x06 u8 groupe de la texture (unknown0) : un par famille de contenu (4 interface, 6 objets construits, 7 personnages
#        et animaux, 8 armes et equipement, 10 terrain, 11 vegetation, 5 effets, 12 routes, 14 atmosphere, 18 eau...) ;
#   0x07 u8 dimension - 1 (2 : texture 2D, 3 : texture 3D) ; 0x08 u32 format DXGI ;
#   0x0c, 0x0e, 0x10 u16 largeur, hauteur, profondeur (tranches d'une texture 3D ou taille d'un tableau 2D) ;
#   0x12 u16 drapeaux : 0x1 niveaux hors du .ddsc (dans le .hmddsc, sans exception dans COTW), 0x8 textures de couleur
#        (_dif), 0x40 cube ;
#   0x14 u8 niveaux ; 0x15 u8 niveaux dans le .ddsc (les plus petits) ; 0x16 u16 (0) ;
#   0x18 u32 (unknown2) : 1 humains, 3 vegetation, 4 armes et equipement, 0 ailleurs (role inconnu) ; 0x1c u32 (0) ;
#   0x20 table des flux, 8 x 12 octets jusqu'a 0x80 : u32 position, u32 taille, u16 alignement (16), u8 (0), u8 source
#        (0 : ce .ddsc, 1 : le .hmddsc). Flux 0 = corps du .ddsc (position 128 = taille de l'en-tete, d'ou size_header et
#        size_body) ; puis un flux par niveau hors du .ddsc, du plus petit au plus grand, bout a bout dans le .hmddsc
#        (verifie sur les 14 392 textures concernees : tailles des niveaux et taille du .hmddsc).
AVTX_STREAM = struct.Struct('<IIHBB')
AVTX_STREAM_COUNT = 8
AVTX_STREAM_SOURCES = {0: '.ddsc', 1: '.hmddsc'}
AVTX_GROUPS = {0: 'Just Cause leftovers, missions', 1: 'dummies, color curves', 4: 'user interface', 5: 'effects, decals',
               6: 'man-made objects, shared, camo', 7: 'characters, animals', 8: 'weapons, equipment, sights, ammunition',
               9: 'vehicles', 10: 'terrain', 11: 'vegetation, climate', 12: 'roads', 13: 'emissive', 14: 'atmosphere, weather',
               18: 'water', 25: 'clues'}


class AvtxStream:
    """An entry of the stream table of an AVTX header (0x20 + 12 i)."""
    def __init__(self, offset, size, alignment, unknown, source):
        self.offset, self.size, self.alignment, self.unknown, self.source = offset, size, alignment, unknown, source

    def __repr__(self):
        return 'AvtxStream(offset={}, size={}, alignment={}, unknown={}, source={})'.format(
            self.offset, self.size, self.alignment, self.unknown, self.source)

    def is_empty(self):
        return not (self.offset or self.size or self.alignment or self.unknown or self.source)


class DdImageHeader:
    def __init__(self):
        self.header_buffer = None
        self.dds_header = DdsHeader()
        self.dds_header_dxt10 = DdsHeaderDxt10()

        self.magic = None
        self.version = None
        self.unknown0 = None
        self.flags = 0
        self.mip_count = None
        self.unknown1 = None
        self.unknown2 = None
        self.unknown3 = None
        self.size_header = None
        self.size_body = None
        self.unknown = None
        self.streams = []  # COTWed DECA: the stream table (AvtxStream), AVTX only

    def dump(self):
        print(f'magic: {self.magic}')
        print(f'version: {self.version}')
        print(f'UNKNOWN0: {self.unknown0}')
        print(f'flags: {self.flags}')
        print(f'mip_count: {self.mip_count}')
        print(f'UNKNOWN1: {self.unknown1}')
        print(f'UNKNOWN2: {self.unknown2}')
        print(f'UNKNOWN3: {self.unknown3}')
        print(f'size_header: {self.size_header}')
        print(f'size_body: {self.size_body}')
        print(f'UNKNOWN: {self.unknown}')

        ddsc_flags = [
            [0x01, "DDSC_MATCHING_ATX_FILES"],
            [0x08, "DDSC_is_dif_image_file?"],
            [0x40, "DDSC_CUBEMAP"],
        ]
        flags = self.flags
        for test in ddsc_flags:
            if 0 != flags & test[0]:
                print(f'flags: {test[1]}')
            flags = flags & (~test[0])

        print(f'flags: leftover: {flags}')

        pprint.pprint(self.dds_header)
        pprint.pprint(self.dds_header_dxt10)

        print('--- dwFlags')
        for test in dwFlagsTest:
            if 0 != self.dds_header.dwFlags & test[0]:
                print('dwFlags: {}'.format(test[1]))

        print('--- dwCaps')
        for test in dwCapsTest:
            if 0 != self.dds_header.dwCaps & test[0]:
                print('dwCaps: {}'.format(test[1]))

        print('--- dwCaps2')
        for test in dwCaps2Test:
            if 0 != self.dds_header.dwCaps2 & test[0]:
                print('dwCaps2: {}'.format(test[1]))

        print('--- dwPFFlags')
        for test in dwPFFlagsTest:
            if 0 != self.dds_header.ddspf.dwFlags & test[0]:
                print('dwPFFlags: {}'.format(test[1]))

    def deserialize_ddsc(self, buffer):
        with ArchiveFile(io.BytesIO(buffer)) as fh:
            self.magic = fh.read(4)
            self.version = fh.read_u16()
            self.unknown0 = fh.read_u8()

            self.dds_header.dwSize = 124
            self.dds_header.ddspf.dwSize = 32
            self.dds_header.ddspf.dwFourCC = b'DX10'
            self.dds_header.ddspf.dwFlags |= DDPF_FOURCC
            self.dds_header_dxt10.miscFlag = 0
            self.dds_header_dxt10.arraySize = 1
            self.dds_header_dxt10.miscFlags2 = 0

            self.dds_header_dxt10.resourceDimension = fh.read_u8() + 1
            self.dds_header_dxt10.dxgiFormat = fh.read_u32()

            self.dds_header.dwWidth = fh.read_u16()
            self.dds_header.dwHeight = fh.read_u16()
            self.dds_header.dwDepth = fh.read_u16()

            if self.dds_header_dxt10.dxgiFormat not in dxgi_base_format_db:
                raise EDecaIncorrectFileFormat('dxgi Format {} not in dxgi_base_format_db'.format(
                    self.dds_header_dxt10.dxgiFormat))

            fmt = dxgi_base_format_db[self.dds_header_dxt10.dxgiFormat]

            if fmt not in dxgi_format_db:
                raise EDecaIncorrectFileFormat('dxgi Format {} which maps to {} not in dxgi_base_format_db'.format(
                    self.dds_header_dxt10.dxgiFormat, fmt))

            is_uncompressed, ele_size = dxgi_format_db[fmt]

            if is_uncompressed:
                self.dds_header.dwFlags |= DDSD_PITCH
                pls = ele_size * self.dds_header.dwWidth
            else:
                self.dds_header.dwFlags |= DDSD_LINEARSIZE
                w = (self.dds_header.dwWidth + 3) // 4
                h = (self.dds_header.dwHeight + 3) // 4
                pls = ele_size * max(1, int(w * h))
            self.dds_header.dwPitchOrLinearSize = pls

            self.flags = fh.read_u16()
            self.dds_header.dwMipMapCount = fh.read_u8()
            self.mip_count = fh.read_u8()
            self.unknown1 = fh.read_u16()
            self.unknown2 = fh.read_u32()
            self.unknown3 = fh.read_u32()
            self.size_header = fh.read_u32()
            self.size_body = fh.read_u32()
            self.unknown = []
            while fh.tell() < self.size_header:
                self.unknown.append(fh.read_u8())

            # COTWed DECA: stream table, 8 x 12 bytes from 0x20 (the first stream is size_header, size_body)
            self.streams = []
            if len(buffer) >= 0x20 + AVTX_STREAM.size * AVTX_STREAM_COUNT and self.size_header >= 0x80:
                self.streams = [AvtxStream(*AVTX_STREAM.unpack_from(buffer, 0x20 + AVTX_STREAM.size * i))
                                for i in range(AVTX_STREAM_COUNT)]

            self.dds_header.dwFlags |= DDSD_CAPS
            self.dds_header.dwFlags |= DDSD_HEIGHT
            self.dds_header.dwFlags |= DDSD_WIDTH
            self.dds_header.dwFlags |= DDSD_PIXELFORMAT
            self.dds_header.dwFlags |= DDSD_MIPMAPCOUNT
            if self.dds_header.dwDepth > 1:
                self.dds_header.dwFlags |= DDSD_DEPTH

            self.dds_header.dwCaps |= DDSCAPS_TEXTURE
            if self.dds_header.dwMipMapCount > 1:
                self.dds_header.dwCaps |= DDSCAPS_COMPLEX
                self.dds_header.dwCaps |= DDSCAPS_MIPMAP

            if self.flags & 0x01:
                pass  # ATX files attached to ddsc

            if self.flags & 0x08:
                pass  # DIF image? only seems to be on dif files, not mpm, nrm, emi

            if self.flags & 0x40:
                self.dds_header.dwCaps |= DDSCAPS_COMPLEX
                self.dds_header.dwCaps2 |= DDS_CUBEMAP_ALLFACES | DDSCAPS2_CUBEMAP

            end_pos = fh.tell()

        # self.dump()

        return end_pos

    def deserialize_dds(self, buffer, convert_to_dx10=True):
        with ArchiveFile(io.BytesIO(buffer)) as f:
            self.magic = f.read(4)
            self.flags = 0

            if self.magic != b'DDS ':
                raise EDecaIncorrectFileFormat('deserialize_dds')

            # DDS_HEADER
            self.dds_header.dwSize = f.read_u32()
            self.dds_header.dwFlags = f.read_u32()
            self.dds_header.dwHeight = f.read_u32()
            self.dds_header.dwWidth = f.read_u32()
            self.dds_header.dwPitchOrLinearSize = f.read_u32()
            self.dds_header.dwDepth = f.read_u32()
            self.dds_header.dwMipMapCount = f.read_u32()

            self.dds_header.dwReserved1 = []
            for i in range(11):
                self.dds_header.dwReserved1.append(f.read_u32())

            # PIXEL_FORMAT
            self.dds_header.ddspf.dwSize = f.read_u32()
            self.dds_header.ddspf.dwFlags = f.read_u32()
            self.dds_header.ddspf.dwFourCC = f.read(4)
            self.dds_header.ddspf.dwRGBBitCount = f.read_u32()
            self.dds_header.ddspf.dwRBitMask = f.read_u32()
            self.dds_header.ddspf.dwGBitMask = f.read_u32()
            self.dds_header.ddspf.dwBBitMask = f.read_u32()
            self.dds_header.ddspf.dwABitMask = f.read_u32()

            # DDS_HEADER continued
            self.dds_header.dwCaps = f.read_u32()
            self.dds_header.dwCaps2 = f.read_u32()
            self.dds_header.dwCaps3 = f.read_u32()
            self.dds_header.dwCaps4 = f.read_u32()
            self.dds_header.dwReserved2 = f.read_u32()

            # DDS_HEADER_DXT10
            four_cc = self.dds_header.ddspf.dwFourCC
            dw_four_cc = struct.unpack('I', four_cc)[0]
            dxgi_format = dw_four_cc_convert.get(dw_four_cc, dw_four_cc_convert.get(four_cc, None))

            if self.dds_header.ddspf.dwFourCC == b'DX10':
                self.dds_header_dxt10.dxgiFormat = f.read_u32()
                self.dds_header_dxt10.resourceDimension = f.read_u32()
                self.dds_header_dxt10.miscFlag = f.read_u32()
                self.dds_header_dxt10.arraySize = f.read_u32()
                self.dds_header_dxt10.miscFlags2 = f.read_u32()
            elif convert_to_dx10 and dxgi_format is not None:
                self.dds_header.ddspf.dwFourCC = b'DX10'
                self.dds_header_dxt10.dxgiFormat = dxgi_format
                self.dds_header_dxt10.arraySize = 1  # TODO assume array size of one
                if self.dds_header.dwCaps2 & DDSCAPS2_VOLUME != 0:
                    self.dds_header_dxt10.resourceDimension = 3 + 1
                else:
                    # TODO assuming 2d texture
                    self.dds_header_dxt10.resourceDimension = 2 + 1
            elif convert_to_dx10 and dw_four_cc == 0:
                # older format, probably uses bitmasks
                dxgi_format = None
                r_mask = self.dds_header.ddspf.dwRBitMask
                g_mask = self.dds_header.ddspf.dwGBitMask
                b_mask = self.dds_header.ddspf.dwBBitMask
                a_mask = self.dds_header.ddspf.dwABitMask
                if self.dds_header.ddspf.dwRGBBitCount == 8:
                    if r_mask == 0xFF:
                        dxgi_format = DXGI_FORMAT_R8_TYPELESS
                elif self.dds_header.ddspf.dwRGBBitCount == 32:
                    if r_mask == (0xFF << 0) and g_mask == (0xFF << 8) and b_mask == (0xFF << 16) and a_mask == (0xFF << 24):
                        dxgi_format = DXGI_FORMAT_R8G8B8A8_UNORM
                    elif r_mask == (0xFF << 16) and g_mask == (0xFF << 8) and b_mask == (0xFF << 0) and a_mask == (0xFF << 24):
                        dxgi_format = DXGI_FORMAT_B8G8R8A8_UNORM

                if dxgi_format is not None:
                    self.dds_header.ddspf.dwFourCC = b'DX10'
                    self.dds_header_dxt10.dxgiFormat = dxgi_format
                    self.dds_header_dxt10.arraySize = 1  # TODO assume array size of one
                    self.dds_header_dxt10.resourceDimension = 2 + 1

            self.mip_count = self.dds_header.dwMipMapCount

            if self.dds_header.dwCaps2 & (DDS_CUBEMAP_ALLFACES | DDSCAPS2_CUBEMAP) != 0:
                self.flags |= 0x40

            end_pos = f.tell()

        # self.dump()

        return end_pos


def load_mip(mip, f, filename, save_raw_data, is_atx=False):
    pixel_format = mip.pixel_format
    nx = mip.size_x
    ny = mip.size_y
    if nx == 0 or ny == 0:
        return False
    nxm = max(4, nx)
    nym = max(4, ny)
    raw_size = deca.dxgi.raw_data_size(pixel_format, nx, ny)
    # print('Loading Data: {}'.format(raw_size))
    raw_data = f.read(raw_size)
    raw_size_read = len(raw_data)

    if is_atx:
        if raw_size_read == 0:
            return False  # Ran out of data probably because more data is in another atx
        elif raw_size_read < raw_size:
            raise Exception('Ddsc::load_atx: Not Enough Data')
    else:
        if raw_size_read < raw_size:
            raise Exception('Ddsc::load_ddsc: Not Enough Data')

    if pixel_format in {2, 10}:  # floating point 4 components
        inp_f32 = np.zeros((nym, nxm, 4), dtype=np.float32)
        deca.dxgi.process_image(inp_f32, raw_data, nx, ny, pixel_format)
        mn = np.nanmin(inp_f32)
        mx = np.nanmax(inp_f32)
        s = 1.0 / (mx - mn)
        inp = ((inp_f32 - mn) * s * 255).astype(dtype=np.uint8)
    elif pixel_format in {26}:  # floating point 3 components
        inp_f32 = np.zeros((nym, nxm, 4), dtype=np.float32)
        deca.dxgi.process_image(inp_f32, raw_data, nx, ny, pixel_format)
        mn = np.nanmin(inp_f32[:, :, 0:3])
        mx = np.nanmax(inp_f32[:, :, 0:3])
        s = 1.0 / (mx - mn)
        inp = np.zeros((nym, nxm, 4), dtype=np.uint8)
        inp[:, :, 0:3] = ((inp_f32[:, :, 0:3] - mn) * s * 255).astype(dtype=np.uint8)
        inp[:, :, 3] = 255
    elif pixel_format in {41,  53, 55, 56, 57,  54,  58, 59,  63, 64}:  # floating point 1 components
        inp_f32 = np.zeros((nym, nxm, 4), dtype=np.float32)
        deca.dxgi.process_image(inp_f32, raw_data, nx, ny, pixel_format)
        mn = np.nanmin(inp_f32[:, :, 0:1])
        mx = np.nanmax(inp_f32[:, :, 0:1])
        s = 1.0 / (mx - mn)
        inp = np.zeros((nym, nxm, 4), dtype=np.uint8)
        inp[:, :, 0:1] = ((inp_f32[:, :, 0:1] - mn) * s * 255).astype(dtype=np.uint8)
        inp[:, :, 1:3] = 0
        inp[:, :, 3] = 255
    else:
        inp = np.zeros((nym, nxm, 4), dtype=np.uint8)
        deca.dxgi.process_image(inp, raw_data, nx, ny, pixel_format)

    # inp = inp[0:ny, 0:nx, :]  # TODO Qt cannot display 2x2 for some reason
    if ny < nym or nx < nxm:
        inp[ny:, :, :] = 0
        inp[:, nx:, :] = 0

    if is_atx:
        mip.itype = 'atx'
    else:
        mip.itype = 'ddsc'
    mip.data = inp
    mip.filename = filename

    if save_raw_data:
        mip.raw_data = raw_data

    return True


class Ddsc:
    """
    contains surfaces, which contain mip levels, which contain depths, which are 2d images
    """
    def __init__(self):
        self.header_buffer = None
        self.header = DdImageHeader()
        self.mips = None
        self.mip_map = {}
        self.mips_avtx = []
        self.mips_dds = []

    def mip_sort_for_dds(self):
        pass

    def mip_sort_for_avtx(self):
        pass

    def load_bmp(self, f, filename=None):
        im = Image.open(f)
        im.convert('RGBA')
        self.mips = [DecaImage(sx=im.size[0], sy=im.size[1], itype='bmp', data=np.array(im), filename=filename)]

    def load_body(self, f, filename, save_raw_data, group_by_surface):
        nx0 = self.header.dds_header.dwWidth
        ny0 = self.header.dds_header.dwHeight
        self.mips = []

        if self.header.dds_header.dwMipMapCount == 0:
            mip_map_count = 1
            mip_map_count_in_file = 1
        else:
            mip_map_count = self.header.dds_header.dwMipMapCount
            mip_map_count_in_file = self.header.mip_count

        depth = max(1, self.header.dds_header.dwDepth)

        surfaces = []

        if self.header.dds_header.dwCaps2 & DDSCAPS2_CUBEMAP_POSITIVEX != 0:
            surfaces.append('xp')
        if self.header.dds_header.dwCaps2 & DDSCAPS2_CUBEMAP_NEGATIVEX != 0:
            surfaces.append('xn')
        if self.header.dds_header.dwCaps2 & DDSCAPS2_CUBEMAP_POSITIVEY != 0:
            surfaces.append('yp')
        if self.header.dds_header.dwCaps2 & DDSCAPS2_CUBEMAP_NEGATIVEY != 0:
            surfaces.append('yn')
        if self.header.dds_header.dwCaps2 & DDSCAPS2_CUBEMAP_POSITIVEZ != 0:
            surfaces.append('zp')
        if self.header.dds_header.dwCaps2 & DDSCAPS2_CUBEMAP_NEGATIVEZ != 0:
            surfaces.append('zn')

        if len(surfaces) == 0:
            surfaces = ['main']

        self.mip_map = {}
        self.mips_avtx = []
        self.mips_dds = []

        # create mip levels
        nx = nx0
        ny = ny0
        for i in range(mip_map_count):
            for sid in surfaces:
                for j in range(depth):
                    mip_id = (sid, i, j)
                    self.mip_map[mip_id] = DecaImage(
                        sx=nx, sy=ny,
                        depth_cnt=depth, depth_idx=j, surface_id=sid,
                        pixel_format=self.header.dds_header_dxt10.dxgiFormat, itype='missing')

            nx = max(1, nx // 2)
            ny = max(1, ny // 2)

        # DDS format
        for sid in surfaces:
            for i in range(mip_map_count):
                for j in range(depth):
                    mip_id = (sid, i, j)
                    self.mips_dds.append(self.mip_map[mip_id])

        # AVTX format
        for i in range(mip_map_count):
            for sid in surfaces:
                for j in range(depth):
                    mip_id = (sid, i, j)
                    self.mips_avtx.append(self.mip_map[mip_id])

        self.mips = self.mips_avtx

        n_surfaces = len(surfaces)
        begin = (mip_map_count - mip_map_count_in_file) * depth * n_surfaces
        end = mip_map_count * depth * n_surfaces
        for midx in range(begin, end):
            mip = self.mips[midx]
            if not load_mip(mip, f, filename, save_raw_data):
                break

    def load_dds(self, f, filename=None, save_raw_data=False):
        header = f.read(256)
        hl = self.header.deserialize_dds(header)
        self.header_buffer = header[0:hl]
        f.seek(hl)
        self.load_body(f, filename, save_raw_data, group_by_surface=True)

    def load_ddsc(self, f, filename=None, save_raw_data=False):
        header = f.read(256)
        hl = self.header.deserialize_ddsc(header)
        self.header_buffer = header[0:hl]
        f.seek(hl)
        self.load_body(f, filename, save_raw_data, group_by_surface=False)

    def load_atx(self, f, filename=None, save_raw_data=False):
        first_loaded = 0
        while first_loaded < len(self.mips):
            if self.mips[first_loaded].data is None:
                first_loaded = first_loaded + 1
            else:
                break

        for midx in range(first_loaded - 1, -1, -1):
            mip = self.mips[midx]
            if not load_mip(mip, f, filename, save_raw_data, is_atx=True):
                break

    def load_ddsc_atx(self, files, save_raw_data=False):
        self.load_ddsc(files[0][1], filename=files[0][0], save_raw_data=save_raw_data)
        for filename, f in files[1:]:
            self.load_atx(f, filename=filename, save_raw_data=save_raw_data)


def avtx_header_lines(header: DdImageHeader) -> List[str]:
    """COTWed DECA: the AVTX header field by field (see the comment of AVTX_STREAM), for the Image tab."""
    if header.magic != b'AVTX':
        return []
    h, x = header.dds_header, header.dds_header_dxt10
    fmt = x.dxgiFormat
    lines = ['AVTX v{} : {} ({}), {} x {}{}, {} levels ({} in the .ddsc)'.format(
        header.version, dxgi_name_db.get(fmt, '?'), fmt, h.dwWidth, h.dwHeight,
        ' x {}'.format(h.dwDepth) if h.dwDepth > 1 else '', h.dwMipMapCount, header.mip_count)]
    flags = []
    for bit, name in ((0x01, 'levels in .hmddsc'), (0x08, 'color (dif)'), (0x40, 'cube')):
        if header.flags & bit:
            flags.append(name)
    rest = header.flags & ~0x49
    if rest:
        flags.append('0x{:x} unknown'.format(rest))
    lines.append('0x06 group {} ({}) ; 0x07 dimension {} ; 0x12 flags 0x{:x} ({})'.format(
        header.unknown0, AVTX_GROUPS.get(header.unknown0, 'unknown'), x.resourceDimension, header.flags,
        ', '.join(flags) or 'none'))
    lines.append('0x16 u16 {} ; 0x18 u32 {} (1 humans, 3 vegetation, 4 weapons) ; 0x1c u32 {}'.format(
        header.unknown1, header.unknown2, header.unknown3))
    for i, s in enumerate(header.streams):
        if s.is_empty():
            continue
        lines.append('stream {} : {} offset {} size {} alignment {}{}'.format(
            i, AVTX_STREAM_SOURCES.get(s.source, 'source {}'.format(s.source)), s.offset, s.size, s.alignment,
            ' byte 10 = {}'.format(s.unknown) if s.unknown else ''))
    return lines


def image_load(vfs: VfsDatabase, vnode: VfsNode, save_raw_data=False):
    if vnode.file_type == FTYPE_BMP:
        f_ddsc = vfs.file_obj_from(vnode)
        ddsc = Ddsc()
        ddsc.load_bmp(f_ddsc)

    elif vnode.file_type == FTYPE_DDS:
        # f_ddsc = vfs.file_obj_from(vnode)
        # ddsc = Ddsc()
        # ddsc.load_dds(f_ddsc)

        if vnode.v_path is None:
            filename = None
        else:
            filename = UniPath.splitext(vnode.v_path)
        f_ddsc = vfs.file_obj_from(vnode)
        ddsc = Ddsc()
        ddsc.load_dds(ArchiveFile(f_ddsc), filename=filename, save_raw_data=save_raw_data)

    elif vnode.file_type in {FTYPE_AVTX, FTYPE_ATX, FTYPE_HMDDSC}:
        if vnode.v_path is None:
            f_ddsc = vfs.file_obj_from(vnode)
            ddsc = Ddsc()
            ddsc.load_ddsc(f_ddsc, save_raw_data=save_raw_data)
        else:
            filename = UniPath.splitext(vnode.v_path)
            if len(filename[1]) == 0 and vnode.file_type == FTYPE_AVTX:
                filename_ddsc = vnode.v_path
            else:
                filename_ddsc = filename[0] + b'.ddsc'

            filename_ddsc_nodes = vfs.nodes_where_match(v_path=filename_ddsc)

            if filename_ddsc_nodes:
                extras = [b'.hmddsc']
                for i in range(1, 16):
                    extras.append('.atx{}'.format(i).encode('ascii'))

                files = []
                files.append([
                    filename_ddsc,
                    vfs.file_obj_from(filename_ddsc_nodes[0]),
                ])
                for extra in extras:
                    filename_atx = filename[0] + extra
                    filename_atx_nodes = vfs.nodes_where_match(v_path=filename_atx)
                    if filename_atx_nodes:
                        files.append([
                            filename_atx,
                            vfs.file_obj_from(filename_atx_nodes[0]),
                        ])
                ddsc = Ddsc()
                ddsc.load_ddsc_atx(files, save_raw_data=save_raw_data)
            else:
                raise EDecaFileMissing('File {} is missing.'.format(filename_ddsc))
    else:
        raise EDecaIncorrectFileFormat('Cannot handle format {} in {}'.format(vnode.file_type, vnode.v_path))

    return ddsc


def ddsc_clean(ddsc: Ddsc, prefer_old_format=True):
    # prefer old header for better compatibility
    if prefer_old_format and ddsc.header.dds_header.ddspf.dwFourCC == b'DX10':
        ddsc.header.dds_header.ddspf.dwFourCC = \
            dw_four_cc_downgrade.get(ddsc.header.dds_header_dxt10.dxgiFormat, b'DX10')

    # remove missing mip levels
    first_good = -1

    for i, mip in enumerate(ddsc.mips):
        if mip.raw_data is not None:
            first_good = i
            break

    if first_good > 0:
        ddsc.header.dds_header.dwMipMapCount -= first_good
        ddsc.header.dds_header.dwWidth = ddsc.mips[first_good].size_x
        ddsc.header.dds_header.dwHeight = ddsc.mips[first_good].size_y

        max_x = ddsc.mips[first_good].size_x
        max_y = ddsc.mips[first_good].size_y

        ddsc.mips = [mip for mip in ddsc.mips if mip.size_x <= max_x and mip.size_y <= max_y]
        ddsc.mips_avtx = [mip for mip in ddsc.mips_avtx if mip.size_x <= max_x and mip.size_y <= max_y]
        ddsc.mips_dds = [mip for mip in ddsc.mips_dds if mip.size_x <= max_x and mip.size_y <= max_y]

        remove_keys = []
        v: DecaImage
        for k, v in ddsc.mip_map.items():
            if v.size_x > max_x or v.size_y > max_y:
                remove_keys.append(k)

        for k in remove_keys:
            ddsc.mip_map.pop(k, None)

        return True

    return False


def ddsc_write_to_png(ddsc, output_file_name):
    image = None
    for i in range(len(ddsc.mips)):
        if ddsc.mips[i].data is not None:
            image = ddsc.mips[i].pil_image()
            break
    if image is None:
        raise EDecaIncorrectFileFormat('Could not find image data')

    image.save(output_file_name)


def ddsc_header_dds_write(ddsc, f):
    # magic word
    f.write(b'DDS ')
    # DDS_HEADER
    f.write_u32(124)  # dwSize
    f.write_u32(ddsc.header.dds_header.dwFlags)  # dwFlags
    f.write_u32(ddsc.header.dds_header.dwHeight)  # dwHeight
    f.write_u32(ddsc.header.dds_header.dwWidth)  # dwWidth
    f.write_u32(ddsc.header.dds_header.dwPitchOrLinearSize)  # dwPitchOrLinearSize
    f.write_u32(ddsc.header.dds_header.dwDepth)  # dwDepth
    f.write_u32(ddsc.header.dds_header.dwMipMapCount)  # dwMipMapCount
    for i in range(11):
        f.write_u32(0)  # dwReserved1

    # PIXEL_FORMAT
    f.write_u32(32)  # DWORD dwSize
    f.write_u32(ddsc.header.dds_header.ddspf.dwFlags)  # DWORD dwFlags
    f.write(ddsc.header.dds_header.ddspf.dwFourCC)  # DWORD dwFourCC
    f.write_u32(ddsc.header.dds_header.ddspf.dwRGBBitCount)  # DWORD dwRGBBitCount
    f.write_u32(ddsc.header.dds_header.ddspf.dwRBitMask)  # DWORD dwRBitMask
    f.write_u32(ddsc.header.dds_header.ddspf.dwGBitMask)  # DWORD dwGBitMask
    f.write_u32(ddsc.header.dds_header.ddspf.dwBBitMask)  # DWORD dwBBitMask
    f.write_u32(ddsc.header.dds_header.ddspf.dwABitMask)  # DWORD dwABitMask

    # DDS_HEADER, cont...
    f.write_u32(ddsc.header.dds_header.dwCaps)  # dwCaps
    f.write_u32(ddsc.header.dds_header.dwCaps2)  # dwCaps2
    f.write_u32(ddsc.header.dds_header.dwCaps3)  # dwCaps3
    f.write_u32(ddsc.header.dds_header.dwCaps4)  # dwCaps4
    f.write_u32(0)  # dwReserved2

    # DDS_HEADER_DXT10
    if ddsc.header.dds_header.ddspf.dwFourCC == b'DX10':
        f.write_u32(ddsc.header.dds_header_dxt10.dxgiFormat)
        f.write_u32(ddsc.header.dds_header_dxt10.resourceDimension)
        f.write_u32(ddsc.header.dds_header_dxt10.miscFlag)
        f.write_u32(ddsc.header.dds_header_dxt10.arraySize)
        f.write_u32(ddsc.header.dds_header_dxt10.miscFlags2)


def ddsc_write_to_dds(ddsc, output_file_name):
    with ArchiveFile(open(output_file_name, 'wb')) as f:
        ddsc_header_dds_write(ddsc, f)
        for mip in ddsc.mips_dds:
            f.write(mip.raw_data)


def ddsc_header_ddsc_write(ddsc, f):
    f.write(b'AVTX')
    f.write_u16(1)  # version
    f.write_u8(ddsc.header.unknown0)
    f.write_u8(ddsc.header.dds_header_dxt10.resourceDimension - 1)
    f.write_u32(ddsc.header.dds_header_dxt10.dxgiFormat)

    f.write_u16(ddsc.header.dds_header.dwWidth)
    f.write_u16(ddsc.header.dds_header.dwHeight)
    f.write_u16(ddsc.header.dds_header.dwDepth)

    f.write_u16(ddsc.header.flags)
    f.write_u8(ddsc.header.dds_header.dwMipMapCount)
    f.write_u8(ddsc.header.mip_count)
    f.write_u16(ddsc.header.unknown1)
    f.write_u32(ddsc.header.unknown2)
    f.write_u32(ddsc.header.unknown3)
    f.write_u32(ddsc.header.size_header)
    f.write_u32(ddsc.header.size_body)

    for v in ddsc.header.unknown:
        f.write_u8(v)

    end_pos = f.tell()

    return end_pos


def image_export(vfs: VfsDatabase, node: VfsNode, extract_dir, export_raw, export_processed, allow_overwrite=False):
    existing_files = []
    ddsc = image_load(vfs, node, save_raw_data=True)

    if ddsc is not None:
        multifile = node.file_type in {FTYPE_AVTX, FTYPE_ATX, FTYPE_HMDDSC}

        if export_raw or not multifile:
            if multifile:
                cnodes = [mip.filename for mip in ddsc.mips]
                cnodes = set(cnodes)
                cnodes = [vfs.nodes_where_match(v_path=cnode)[0] for cnode in cnodes]
            else:
                cnodes = [node]

            for cnode in cnodes:
                if cnode.v_path is None:
                    ofile = UniPath.join(extract_dir, '{:08X}.dat'.format(cnode.v_hash))
                else:
                    ofile = UniPath.join(extract_dir, '{}'.format(cnode.v_path.decode('utf-8')))

                make_dir_for_file(ofile)

                if not allow_overwrite and UniPath.isfile(ofile):
                    existing_files.append(ofile)
                else:
                    with open(ofile, 'wb') as fo:
                        with vfs.file_obj_from(cnode) as fi:
                            buffer = fi.read(cnode.size_u)
                            fo.write(buffer)

        if export_processed and multifile:
            if node.v_path is None:
                ofile = UniPath.join(extract_dir, '{:08X}.dat'.format(node.v_hash))
            else:
                ofile = UniPath.join(extract_dir, '{}'.format(node.v_path.decode('utf-8')))

            make_dir_for_file(ofile)

            ofile = UniPath.splitext(ofile)[0]
            ofile = ofile + '.ddsc'

            # export to reference png file
            ofile_img = ofile + '.DECA.REFERENCE.png'
            if not allow_overwrite and UniPath.isfile(ofile_img):
                existing_files.append(ofile_img)
            else:
                ddsc_write_to_png(ddsc, ofile_img)

            # export dds with all mip levels
            ofile_img = ofile + '.dds'
            if not allow_overwrite and UniPath.isfile(ofile_img):
                existing_files.append(ofile_img)
            else:
                ddsc_write_to_dds(ddsc, ofile_img)

        # raise exception if any files could not be overwritten
        if len(existing_files) > 0:
            raise EDecaFileExists(existing_files)


def image_import(vfs: VfsDatabase, node: VfsNode, ifile: str, opath: str):
    print('Importing Image: {}\n  input {}\n  opath {}'.format(node.v_path, ifile, opath))
    ddsc = image_load(vfs, node, save_raw_data=True)

    ddsc_in = Ddsc()
    with ArchiveFile(open(ifile, 'rb')) as f:
        ddsc_in.load_dds(f, save_raw_data=True)

    sz_old = (ddsc.header.dds_header.dwWidth, ddsc.header.dds_header.dwHeight),
    sz_new = (ddsc_in.header.dds_header.dwWidth, ddsc_in.header.dds_header.dwHeight),

    compiled_files = []
    if ddsc is None:
        raise EDecaBuildError('Missing vpath: {}'.format(node.v_path))
    elif ddsc_in is None:
        raise EDecaBuildError('Could not load: {}'.format(ifile))
    elif ddsc.header.dds_header_dxt10.dxgiFormat != ddsc_in.header.dds_header_dxt10.dxgiFormat:
        raise EDecaBuildError('dxgiFormat do not match for ({}, {}) and ({}, {})'.format(
            node.v_path, ddsc.header.dds_header_dxt10.dxgiFormat,
            ifile, ddsc_in.header.dds_header_dxt10.dxgiFormat,
        ))
    elif sz_old != sz_new:
        raise EDecaBuildError('Size do not match for ({}, {}) and ({}, {})'.format(node.v_path, sz_old, ifile, sz_new))
    else:
        out_vpaths = [mip.filename for mip in ddsc.mips]
        out_vpaths = set(out_vpaths)

        for vpath_out in out_vpaths:
            fout_name = UniPath.join(opath, vpath_out.decode('utf-8'))

            make_dir_for_file(fout_name)

            with ArchiveFile(open(fout_name, 'wb')) as file_out:
                if vpath_out.endswith(b'.ddsc'):
                    ddsc.header.dds_header_dxt10.dxgiFormat = ddsc_in.header.dds_header_dxt10.dxgiFormat
                    ddsc_header_ddsc_write(ddsc, file_out)

                    # DDSC files have high to low resolution
                    for mip_index in range(0, len(ddsc_in.mips_avtx)):
                        mip = ddsc.mips_avtx[mip_index]
                        mip_in = ddsc_in.mips_avtx[mip_index]
                        if vpath_out == mip.filename:
                            file_out.write(mip_in.raw_data)
                else:
                    # HMDDSC ATX files have low to high resolution
                    for mip_index in range(len(ddsc_in.mips_avtx)-1, -1, -1):
                        mip = ddsc.mips_avtx[mip_index]
                        mip_in = ddsc_in.mips_avtx[mip_index]
                        if vpath_out == mip.filename:
                            file_out.write(mip_in.raw_data)

            compiled_files.append((vpath_out, fout_name))

    return compiled_files
