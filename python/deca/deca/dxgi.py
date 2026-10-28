import time
import numpy as np
import io
import struct
import os
import ctypes
import sys

from .path import UniPath
from .fast_file import *
from .dxgi_97 import process_image_97
from .dxgi_94_95_96 import process_image_94_95_96
from .dxgi_bc import DECODERS as BC_DECODERS  # BC1 a BC5 (COTWed DECA : defauts D1 a D4 corriges)
from .dxgi_types import *
from numba import njit

process_image_func = None
c_process_image_lib = None
c_process_image_func = None


# https://docs.microsoft.com/en-us/windows/desktop/direct3d9/opaque-and-1-bit-alpha-textures
# https://msdn.microsoft.com/ja-jp/library/bb173059(v=vs.85).aspx
# https://docs.microsoft.com/en-us/windows/win32/api/dxgiformat/ne-dxgiformat-dxgi_format
# less than 32 bit floats
#   https://docs.microsoft.com/en-us/windows/win32/direct3d10/d3d10-graphics-programming-guide-resources-float-rules


@njit(inline='always')
def ux_to_fx_to_f32(s, e, f, den):
    if s == 1:
        s = np.float32(-1)
    else:
        s = np.float32(1)

    if e == 31 and f != 0:
        return np.float32(np.nan)
    elif e == 31 and f == 0:
        return np.float32(s * np.inf)
    elif e == 0 and f != 0:
        return np.float32(s * (2.0**(e-14)) * f * den)
    elif e == 0 and f == 0:
        return np.float32(s * 0.0)
    else:
        return np.float32(s * (2.0 ** (e-15)) * (1 + f * den))


@njit(inline='always')
def u10_to_f10_in_f32(value):
    s = 0
    e = (value >> 5) & 0x1F
    f = value & 0x1F
    den = (1 / 32.0)    # COTWed DECA, 2026-10-18 : 1 / 2^5 (was 1 / 31 : the mantissa overshot by up to 3 %)
    return ux_to_fx_to_f32(s, e, f, den)


@njit(inline='always')
def u11_to_f11_in_f32(value):
    s = 0
    e = (value >> 6) & 0x1F
    f = value & 0x3F
    den = (1 / 64.0)    # COTWed DECA, 2026-10-18 : 1 / 2^6 (was 1 / 63)
    return ux_to_fx_to_f32(s, e, f, den)


@njit(inline='always')
def u16_to_f16_in_f32(value):
    s = value >> 15
    e = (value >> 10) & 0x1F
    f = value & 0x3FF
    den = (1 / 1024.0)  # COTWed DECA, 2026-10-18 : 1 / 2^10 (was 1 / 1023)
    return ux_to_fx_to_f32(s, e, f, den)


@njit
def process_image_2(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R32G32B32A32_FLOAT
    pos = 0
    chans = [0.0] * 4
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_f32(buffer, n_buffer, pos)
            chans[1], pos = ff_read_f32(buffer, n_buffer, pos)
            chans[2], pos = ff_read_f32(buffer, n_buffer, pos)
            chans[3], pos = ff_read_f32(buffer, n_buffer, pos)
            image[yi, xi, :] = chans


@njit
def process_image_10(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R16G16B16A16_FLOAT
    pos = 0
    chans = [0] * 4
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_u16(buffer, n_buffer, pos)
            chans[1], pos = ff_read_u16(buffer, n_buffer, pos)
            chans[2], pos = ff_read_u16(buffer, n_buffer, pos)
            chans[3], pos = ff_read_u16(buffer, n_buffer, pos)
            image[yi, xi, 0] = u16_to_f16_in_f32(chans[0])
            image[yi, xi, 1] = u16_to_f16_in_f32(chans[1])
            image[yi, xi, 2] = u16_to_f16_in_f32(chans[2])
            image[yi, xi, 3] = u16_to_f16_in_f32(chans[3])


@njit
def process_image_26(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R11G11B10_FLOAT
    pos = 0
    for yi in range(ny):
        for xi in range(nx):
            chans, pos = ff_read_u32(buffer, n_buffer, pos)
            image[yi, xi, 0] = u11_to_f11_in_f32((chans >> 0) & 0x07ff)
            image[yi, xi, 1] = u11_to_f11_in_f32((chans >> 11) & 0x07ff)
            image[yi, xi, 2] = u10_to_f10_in_f32((chans >> 22) & 0x03ff)
            image[yi, xi, 3] = 1.0


@njit
def process_image_28(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R8G8B8A8_UNORM
    pos = 0
    chans = [0] * 4
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_u8(buffer, n_buffer, pos)
            chans[1], pos = ff_read_u8(buffer, n_buffer, pos)
            chans[2], pos = ff_read_u8(buffer, n_buffer, pos)
            chans[3], pos = ff_read_u8(buffer, n_buffer, pos)
            image[yi, xi, :] = chans[:]


@njit
def process_image_41(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R32_FLOAT
    pos = 0
    chans = [0.0] * 4
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_f32(buffer, n_buffer, pos)
            chans[1] = 0.0
            chans[2] = 0.0
            chans[3] = 1.0
            image[yi, xi, :] = chans


@njit
def process_image_53(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R16_ u16
    pos = 0
    chans = [0] * 1
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_u16(buffer, n_buffer, pos)
            image[yi, xi, 0] = chans[0]
            image[yi, xi, 1] = 0
            image[yi, xi, 2] = 0
            image[yi, xi, 3] = 0


@njit
def process_image_54(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R16_FLOAT
    pos = 0
    chans = [0] * 1
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_u16(buffer, n_buffer, pos)
            image[yi, xi, 0] = u16_to_f16_in_f32(chans[0])
            image[yi, xi, 1] = 0
            image[yi, xi, 2] = 0
            image[yi, xi, 3] = 0

@njit
def process_image_58(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R16_ s16
    pos = 0
    chans = [0] * 1
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_s16(buffer, n_buffer, pos)
            image[yi, xi, 0] = chans[0]
            image[yi, xi, 1] = 0
            image[yi, xi, 2] = 0
            image[yi, xi, 3] = 0


@njit
def process_image_60(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R8_ u8
    pos = 0
    chans = [0] * 1
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_u8(buffer, n_buffer, pos)
            image[yi, xi, 0] = chans[0]
            image[yi, xi, 1] = 0
            image[yi, xi, 2] = 0
            image[yi, xi, 3] = 0


@njit
def process_image_63(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_R8_ s8
    pos = 0
    chans = [0] * 1
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_s8(buffer, n_buffer, pos)
            image[yi, xi, 0] = chans[0]
            image[yi, xi, 1] = 0
            image[yi, xi, 2] = 0
            image[yi, xi, 3] = 0


@njit
def process_image_87(image, buffer, n_buffer, nx, ny):  # DXGI_FORMAT_B8G8R8A8_UNORM
    pos = 0
    chans = [0] * 4
    for yi in range(ny):
        for xi in range(nx):
            chans[0], pos = ff_read_u8(buffer, n_buffer, pos)
            chans[1], pos = ff_read_u8(buffer, n_buffer, pos)
            chans[2], pos = ff_read_u8(buffer, n_buffer, pos)
            chans[3], pos = ff_read_u8(buffer, n_buffer, pos)
            image[yi, xi, 0] = chans[2]
            image[yi, xi, 1] = chans[1]
            image[yi, xi, 2] = chans[0]
            image[yi, xi, 3] = chans[3]


def process_image_python(image, raw, nx, ny, pixel_format):
    # print(f'PF = {pixel_format}')
    base_format = dxgi_base_format_db[pixel_format]

    loaders = {
        2: process_image_2,    # DXGI_FORMAT_R32G32B32A32_FLOAT
        10: process_image_10,  # DXGI_FORMAT_R16G16B16A16_FLOAT
        26: process_image_26,  # DXGI_FORMAT_R11G11B10_FLOAT
        28: process_image_28,  # DXGI_FORMAT_R8G8B8A8_UNORM
        41: process_image_41,  # DXGI_FORMAT_R32_FLOAT
        53: process_image_53,  # DXGI_FORMAT_R16_TYPELESS u16
        54: process_image_54,  # DXGI_FORMAT_R16_FLOAT f16
        58: process_image_58,  # DXGI_FORMAT_R16_SNORM s16
        60: process_image_60,  # DXGI_FORMAT_R8_TYPELESS u8
        63: process_image_63,  # DXGI_FORMAT_R8_TYPELESS s8
        87: process_image_87,  # DXGI_FORMAT_B8G8R8A8_UNORM
        94: process_image_94_95_96,  # DXGI_FORMAT_BC6H_TYPELESS
        97: process_image_97,  # DXGI_FORMAT_BC7_TYPELESS
    }

    if pixel_format in BC_DECODERS:  # format exact : BC4/BC5 SNORM ont leur decodeur
        BC_DECODERS[pixel_format](image, raw, len(raw), nx, ny)
    elif base_format in loaders:
        loaders[base_format](image, raw, len(raw), nx, ny)
    else:
        raise Exception('Unknown DCC format {} base {}'.format(pixel_format, base_format))


def process_image_c(image, raw, nx, ny, pixel_format):

    if pixel_format in {2, 10, 26, 41, 94, 95, 96}:  # do floating point loads in python
        process_image_python(image, raw, nx, ny, pixel_format)
    else:
        global c_process_image_func
        ret = c_process_image_func(
            image.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            len(image),
            raw,
            len(raw),
            nx,
            ny,
            pixel_format)

        if ret == -1:
            print('FALLING BACK TO PYTHON PARSER')
            process_image_python(image, raw, nx, ny, pixel_format)
        elif ret == -3:  # COTWed DECA : donnee trop courte, comme les decodeurs Python
            raise FFError('process_image_c: not enough data')
        elif ret != 0:
            raise Exception('process_image_c failed with return {}'.format(ret))


def process_image(*args, **kwargs):
    global process_image_func
    global c_process_image_lib
    global c_process_image_func

    if process_image_func is None:
        c_process_image_lib = None
        exe_path, exe_name = UniPath.split(sys.argv[0])
        lib_path = UniPath.normpath(UniPath.join("./", exe_path, "..", "..", "..", "root", "lib"))

        # process_image_func = setup_image_wasm

        if process_image_func is None:
            # "C:\Program Files (x86)\Microsoft Visual Studio\2017\Community\VC\Auxiliary\Build\vcvars64.bat"
            # "cl.exe /D_USRDLL /D_WINDLL cpp/process_image/src/process_image.c /link /DLL /OUT:process_image.dll"

            # gcc -fPIC -shared -O3 cpp/process_image/src/process_image.c -o process_image.so
            paths = [
                "process_image.dll",
                UniPath.join(lib_path, "process_image.dll"),
                "process_image.so",
                UniPath.join(lib_path, "process_image.so"),
            ]

            for path in paths:
                if UniPath.isfile(path):
                    print(f"Using C version of process_image from {path}")
                    if UniPath.splitext(path)[1].lower() == ".dll":
                        c_process_image_lib = ctypes.WinDLL(path)
                    else:
                        c_process_image_lib = ctypes.CDLL(path)
                    break

            if c_process_image_lib is not None:
                prototype = ctypes.CFUNCTYPE(
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_uint32,
                    ctypes.POINTER(ctypes.c_char),
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                )
                paramflags = \
                    (1, 'dst_image_buf'), \
                    (1, 'dst_image_sz'), \
                    (1, 'src_buffer_buf'), \
                    (1, 'src_buffer_sz'), \
                    (1, 'nx'), \
                    (1, 'ny'), \
                    (1, 'pixel_format')
                c_process_image_func = prototype(("process_image", c_process_image_lib), paramflags)
                process_image_func = process_image_c
            else:
                print('Using Python version of process_image')
                process_image_func = process_image_python

    process_image_func(*args, **kwargs)

    # t0 = time.time()
    # process_image_func(*args, **kwargs)
    # t1 = time.time()
    # print('C: Runtime {}'.format(t1-t0))
    #
    # t0 = time.time()
    # process_image_python(*args, **kwargs)
    # t1 = time.time()
    # print('Py: Runtime {}'.format(t1-t0))
    #
    # if (t1 - t0) > 1.0:
    #     print('--------------------------------------------------------------')
    #     print('PT == {}'.format(args[-1]))
    #     process_image_76.inspect_types()
    #     print('--------------------------------------------------------------')
    #     for k, v in process_image_76.inspect_llvm().items():
    #         print(k, v)
    #     print('--------------------------------------------------------------')
    #     for k, v in process_image_76.inspect_asm().items():
    #         print(k, v)
    #     print('--------------------------------------------------------------')

    #
    # t0 = time.time()
    # process_image_cython(*args, **kwargs)
    # t1 = time.time()
    # print('Runtime {}'.format(t1-t0))
