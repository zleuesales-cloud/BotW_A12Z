"""Run the production classic present encoder on a cropped 720p image."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NVN = ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn'


def production_function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 0
    quote = None
    i = opening
    while i < len(source):
        if quote:
            if source[i] == '\\':
                i += 2
                continue
            if source[i] == quote:
                quote = None
        elif source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
            continue
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                raise AssertionError(f'unterminated comment after {signature}')
            i = end + 2
            continue
        elif source[i] in ('"', "'"):
            quote = source[i]
        elif source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise AssertionError(f'unterminated function {signature}')


PROBE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "totk_hdr_present.h"

#define MLOG(...) fprintf(stderr, __VA_ARGS__)
#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); abort(); } while (0)
static id<MTLDevice> g_dev;
static _Atomic uint32_t g_out_w, g_out_h;
static _Atomic int g_pic_big;
static id<MTLTexture> g_fps_tex[4], g_shd_tex[4], g_load_tex[4];
static id<MTLRenderPipelineState> g_blit_pso, g_blit_hdr_pso;
static MTLPixelFormat g_blit_fmt = MTLPixelFormatInvalid;
static int g_blit_hdr_source;
static id<MTLSamplerState> g_blit_samp;
static float t_hdr_present_peak = 1, t_hdr_present_white = 1;
static float (*front_hdr_headroom)(void);
static float (*runtime_aspect_now)(void);
static id<MTLLibrary> mtl_counted_library(id<MTLDevice> d, NSString *s, MTLCompileOptions *o, NSError **e) { return [d newLibraryWithSource:s options:o error:e]; }
static id<MTLRenderPipelineState> mtl_counted_render(id<MTLDevice> d, MTLRenderPipelineDescriptor *p, NSError **e) { return [d newRenderPipelineStateWithDescriptor:p error:e]; }
static MTLCompileOptions *totk_precise_opts(void) { return [MTLCompileOptions new]; }
static BOOL totk_hdr_available(void) { return NO; }
static int hdr_on(void) { return 0; }
static void es_reset(void) {}
static int fps_overlay_prepare(int s, MTLViewport v, MTLViewport *o, float u[8]) { (void)s; (void)v; (void)o; (void)u; return 0; }
static int shader_overlay_prepare(int s, MTLViewport v, MTLViewport *o, float u[8]) { (void)s; (void)v; (void)o; (void)u; return 0; }
static int load_overlay_prepare(int s, MTLViewport v, MTLViewport *o, float u[8]) { (void)s; (void)v; (void)o; (void)u; return 0; }
// PRODUCTION_FUNCTIONS

int main(void) { @autoreleasepool {
    g_dev = MTLCreateSystemDefaultDevice(); if (!g_dev) return 2;
    const NSUInteger sw = 1600, sh = 900, dw = 1920, dh = 1080;
    const int crop[4] = {160, 90, 1280, 720};
    MTLTextureDescriptor *sd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:sw height:sh mipmapped:NO];
    sd.usage = MTLTextureUsageShaderRead; sd.storageMode = MTLStorageModeShared;
    id<MTLTexture> src = [g_dev newTextureWithDescriptor:sd]; if (!src) return 3;
    uint8_t *pixels = calloc(sw * sh, 4); if (!pixels) return 4;
    for (NSUInteger y = 0; y < sh; y++) for (NSUInteger x = 0; x < sw; x++) {
        uint8_t *p = pixels + 4 * (y * sw + x);
        if (x < 160 || x >= 1440 || y < 90 || y >= 810) { p[0]=255; p[1]=0; p[2]=255; }
        else if (y < 450 && x < 800) { p[0]=10; p[1]=20; p[2]=220; }
        else if (y < 450) { p[0]=10; p[1]=220; p[2]=20; }
        else if (x < 800) { p[0]=220; p[1]=20; p[2]=10; }
        else { p[0]=30; p[1]=200; p[2]=240; }
        p[3]=255;
    }
    [src replaceRegion:MTLRegionMake2D(0, 0, sw, sh) mipmapLevel:0 withBytes:pixels bytesPerRow:sw * 4]; free(pixels);
    MTLTextureDescriptor *dd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:dw height:dh mipmapped:NO];
    dd.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; dd.storageMode = MTLStorageModePrivate;
    id<MTLTexture> dst = [g_dev newTextureWithDescriptor:dd]; if (!dst) return 5;
    id<MTLCommandQueue> q = [g_dev newCommandQueue]; id<MTLCommandBuffer> cb = [q commandBuffer];
    if (!present_encode(cb, src, dst, crop, 0, 0)) return 6;
    if (atomic_load(&g_out_w) != 1280 || atomic_load(&g_out_h) != 720 || atomic_load(&g_pic_big)) return 7;
    id<MTLBuffer> readback = [g_dev newBufferWithLength:dw * dh * 4 options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    [blit copyFromTexture:dst sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(dw,dh,1)
        toBuffer:readback destinationOffset:0 destinationBytesPerRow:dw*4 destinationBytesPerImage:dw*dh*4];
    [blit endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (cb.error) { fprintf(stderr, "GPU error: %s\n", cb.error.localizedDescription.UTF8String); return 8; }
    const uint8_t *out = readback.contents;
    const int xy[4][2] = {{0,0},{dw-1,0},{0,dh-1},{dw-1,dh-1}};
    const int want[4][3] = {{10,20,220},{10,220,20},{220,20,10},{30,200,240}};
    for (int k=0; k<4; k++) for (int c=0; c<3; c++) {
        uint8_t got = out[4 * (xy[k][1] * dw + xy[k][0]) + c];
        if (abs((int)got - want[k][c]) > 3) { fprintf(stderr,"crop corner %d channel %d: got %u expected %d\n",k,c,got,want[k][c]); return 9; }
    }
    const int bad_crop[4] = {321, 90, 1280, 720};
    if (present_encode([q commandBuffer], src, dst, bad_crop, 0, 0)) return 10;
    puts("PASS production present_encode crop: 1280x720 -> 1920x1080");
    return 0;
} }
'''


class PresentEncoder(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
    def test_production_encoder_crops_720p_to_1080p(self):
        source = (NVN / 'nvn_mtl.m').read_text()
        functions = '\n'.join(production_function(source, signature) for signature in (
            'static void present_pipeline(MTLPixelFormat format, BOOL hdrSource) {',
            'static MTLViewport present_viewport(double dw, double dh, double cw, double ch) {',
            'static int present_encode(id<MTLCommandBuffer> cb, id<MTLTexture> src, id<MTLTexture> dst, const int crop[4], int overlay_slot, int fps_overlay) {',
        ))
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'present.m'
            probe.write_text(PROBE.replace('// PRODUCTION_FUNCTIONS', functions))
            binary = work / 'present'
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-I', str(NVN), str(probe), '-framework', 'Foundation',
                            '-framework', 'Metal', '-o', str(binary)], check=True)
            out = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=30).stdout
            self.assertIn('PASS production present_encode crop: 1280x720 -> 1920x1080', out)


if __name__ == '__main__':
    unittest.main()
