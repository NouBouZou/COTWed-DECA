// gcc -fPIC -shared -O3 process_image.c -o process_image.so
//
// Version C (facultative) de deca.dxgi.process_image : DECA l'utilise si process_image.dll / .so est trouve, sinon
// les decodeurs Python (numba). Les formats non traites ici rendent -1 et DECA retombe sur Python.
//
// COTWed DECA (2026-10-28) : BC1 a BC5 refaits pour donner exactement les memes pixels que deca/dxgi_bc.py (regles
// de Direct3D ; voir ce module) : extremites 565 en UNORM (r / 31, g / 63) interpolees puis arrondies (defauts D1
// et D4), 4 couleurs toujours en BC2 / BC3 (D2), ceil(nx / 4) blocs par ligne et pixels hors image non ecrits (D3),
// alpha 4 bits du BC2 x 17. Largeur d'une ligne de l'image : max(4, nx) pixels (ff_avtx.load_mip alloue au moins
// 4 x 4) ; avant, les formats non compresses ecrivaient avec un pas de nx, faux pour un niveau de moins de 4 pixels.
// Donnee trop courte : -3 (rien n'est ecrit).

#ifdef __cplusplus
extern "C" {
#endif

#ifdef _WINDLL
#define DLLEXPORT   __declspec( dllexport )
#else
#define DLLEXPORT
#endif

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

static u32 rd16(u8 const * p) { return (u32)p[0] | ((u32)p[1] << 8); }
static u32 rd32(u8 const * p) { return rd16(p) | (rd16(p + 2) << 16); }
static u64 rd48(u8 const * p) { return (u64)rd32(p) | ((u64)rd16(p + 4) << 32); }

// round(num * 255 / den), demie arrondie vers le haut
static u8 unorm8(u32 num, u32 den) { return (u8)((510u * num + den) / (2u * den)); }

// palette RGBA (4 x 4) d'un bloc couleur ; always_four : BC2 et BC3
static void color_palette(u32 c0, u32 c1, int always_four, u8 * colors)
{
    int four = always_four || c0 > c1;
    for(u32 i = 0; i < 3; ++i)
    {
        u32 a, b, m;
        if(i == 0) { a = (c0 >> 11) & 0x1F; b = (c1 >> 11) & 0x1F; m = 31; }
        else if(i == 1) { a = (c0 >> 5) & 0x3F; b = (c1 >> 5) & 0x3F; m = 63; }
        else { a = c0 & 0x1F; b = c1 & 0x1F; m = 31; }
        colors[0*4 + i] = unorm8(a, m);
        colors[1*4 + i] = unorm8(b, m);
        if(four)
        {
            colors[2*4 + i] = unorm8(2 * a + b, 3 * m);
            colors[3*4 + i] = unorm8(a + 2 * b, 3 * m);
        }
        else
        {
            colors[2*4 + i] = unorm8(a + b, 2 * m);
            colors[3*4 + i] = 0;
        }
    }
    colors[0*4 + 3] = 255;
    colors[1*4 + 3] = 255;
    colors[2*4 + 3] = 255;
    colors[3*4 + 3] = four ? 255 : 0;
}

// palette (8) d'un bloc alpha / BC4 UNORM
static void alpha_palette(u32 a0, u32 a1, u8 * pal)
{
    pal[0] = (u8)a0;
    pal[1] = (u8)a1;
    if(a0 > a1)
    {
        for(u32 k = 1; k < 7; ++k)
            pal[1 + k] = (u8)(((7 - k) * a0 + k * a1 + 3) / 7);
    }
    else
    {
        for(u32 k = 1; k < 5; ++k)
            pal[1 + k] = (u8)(((5 - k) * a0 + k * a1 + 2) / 5);
        pal[6] = 0;
        pal[7] = 255;
    }
}

static void put(u8 * img, u32 w, u32 h, u32 x, u32 y, u32 c, u8 v)
{
    if(x < w && y < h)
        img[(y * w + x) * 4 + c] = v;
}

// BC1 (kind 1), BC2 (2), BC3 (3), BC4 UNORM (4), BC5 UNORM (5)
static int decode_bc(u8 * img, u8 const * src, u32 src_sz, u32 nx, u32 ny, int kind)
{
    u32 w = nx < 4 ? 4 : nx;
    u32 h = ny < 4 ? 4 : ny;
    u32 bnx = (nx + 3) / 4; if(bnx == 0) bnx = 1;
    u32 bny = (ny + 3) / 4; if(bny == 0) bny = 1;
    u32 bs = (kind == 1 || kind == 4) ? 8 : 16;
    if((u64)src_sz < (u64)bnx * bny * bs)
        return -3;
    u8 colors[16];
    u8 pal[2][8];
    for(u32 yi = 0; yi < bny; ++yi)
    {
        for(u32 xi = 0; xi < bnx; ++xi, src += bs)
        {
            u32 ox = xi * 4, oy = yi * 4;
            if(kind <= 3)
            {
                u8 const * cb = kind == 1 ? src : src + 8;
                color_palette(rd16(cb), rd16(cb + 2), kind != 1, colors);
                u32 idx = rd32(cb + 4);
                u64 aidx = 0;
                if(kind == 3)
                {
                    alpha_palette(src[0], src[1], pal[0]);
                    aidx = rd48(src + 2);
                }
                for(u32 p = 0; p < 16; ++p)
                {
                    u32 k = (idx >> (2 * p)) & 3;
                    u32 x = ox + p % 4, y = oy + p / 4;
                    for(u32 c = 0; c < 3; ++c)
                        put(img, w, h, x, y, c, colors[k * 4 + c]);
                    u8 a = colors[k * 4 + 3];
                    if(kind == 2)
                        a = (u8)(((src[p / 2] >> (4 * (p % 2))) & 0xF) * 17);
                    else if(kind == 3)
                        a = pal[0][(aidx >> (3 * p)) & 7];
                    put(img, w, h, x, y, 3, a);
                }
            }
            else
            {
                u32 nch = kind == 4 ? 1 : 2;
                u64 idx[2] = {0, 0};
                for(u32 ch = 0; ch < nch; ++ch)
                {
                    alpha_palette(src[8 * ch], src[8 * ch + 1], pal[ch]);
                    idx[ch] = rd48(src + 8 * ch + 2);
                }
                for(u32 p = 0; p < 16; ++p)
                {
                    u32 x = ox + p % 4, y = oy + p / 4;
                    put(img, w, h, x, y, 0, pal[0][(idx[0] >> (3 * p)) & 7]);
                    put(img, w, h, x, y, 1, nch == 2 ? pal[1][(idx[1] >> (3 * p)) & 7] : 0);
                    put(img, w, h, x, y, 2, 0);
                    put(img, w, h, x, y, 3, 255);
                }
            }
        }
    }
    return 0;
}

DLLEXPORT
int process_image(
    u8 * dst_image_buf,
    u32 dst_image_sz,
    u8 const * src_buffer_buf,
    u32 src_buffer_sz,
    u32 nx,
    u32 ny,
    u32 pixel_format
    )
{
    u32 stride = nx < 4 ? 4 : nx;  // pixels par ligne de l'image de destination
    (void)dst_image_sz;
    switch(pixel_format)
    {
        case 10:  // R16G16B16A16 : octet de poids fort (DECA appelle la version Python pour ce format)
            {
                if((u64)src_buffer_sz < (u64)nx * ny * 8) return -3;
                u8 const * iptr = src_buffer_buf;
                for(u32 yi = 0; yi < ny; ++yi)
                {
                    u8 * optr = dst_image_buf + (u64)yi * stride * 4;
                    for(u32 xi = 0; xi < nx; ++xi, iptr += 8, optr += 4)
                    {
                        optr[0] = iptr[1];
                        optr[1] = iptr[3];
                        optr[2] = iptr[5];
                        optr[3] = iptr[7];
                    }
                }
                return 0;
            }
        case 26:  // R11G11B10 : bits de poids fort (DECA appelle la version Python pour ce format)
            {
                if((u64)src_buffer_sz < (u64)nx * ny * 4) return -3;
                u8 const * iptr = src_buffer_buf;
                for(u32 yi = 0; yi < ny; ++yi)
                {
                    u8 * optr = dst_image_buf + (u64)yi * stride * 4;
                    for(u32 xi = 0; xi < nx; ++xi, iptr += 4, optr += 4)
                    {
                        u32 v = rd32(iptr);
                        optr[0] = (u8)(((v >> 21) & 0x07ff) >> 3);
                        optr[1] = (u8)(((v >> 10) & 0x07ff) >> 3);
                        optr[2] = (u8)(((v >> 0) & 0x03ff) >> 2);
                        optr[3] = 0xFF;
                    }
                }
                return 0;
            }
        case 28:  // R8G8B8A8
        case 87:  // B8G8R8A8
            {
                if((u64)src_buffer_sz < (u64)nx * ny * 4) return -3;
                u8 const * iptr = src_buffer_buf;
                int bgr = pixel_format == 87;
                for(u32 yi = 0; yi < ny; ++yi)
                {
                    u8 * optr = dst_image_buf + (u64)yi * stride * 4;
                    for(u32 xi = 0; xi < nx; ++xi, iptr += 4, optr += 4)
                    {
                        optr[0] = iptr[bgr ? 2 : 0];
                        optr[1] = iptr[1];
                        optr[2] = iptr[bgr ? 0 : 2];
                        optr[3] = iptr[3];
                    }
                }
                return 0;
            }
        case 70: case 71: case 72:
            return decode_bc(dst_image_buf, src_buffer_buf, src_buffer_sz, nx, ny, 1);
        case 73: case 74: case 75:
            return decode_bc(dst_image_buf, src_buffer_buf, src_buffer_sz, nx, ny, 2);
        case 76: case 77: case 78:
            return decode_bc(dst_image_buf, src_buffer_buf, src_buffer_sz, nx, ny, 3);
        case 79: case 80:
            return decode_bc(dst_image_buf, src_buffer_buf, src_buffer_sz, nx, ny, 4);
        case 82: case 83:
            return decode_bc(dst_image_buf, src_buffer_buf, src_buffer_sz, nx, ny, 5);
        default:
            return -1; // format non traite : DECA retombe sur la version Python (SNORM, BC6H, BC7, flottants...)
    }
}

#ifdef __cplusplus
}
#endif
