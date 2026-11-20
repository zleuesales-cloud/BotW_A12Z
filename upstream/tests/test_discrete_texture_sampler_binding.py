"""Run the production discrete texture/sampler setters with generated title MSL."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NVN = ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn'
VS_KEY = '17dccd27ad609e8b7948c3ef48f0b108e404b9df6bfee34662d0fc6a0e4b744d-b7530f698e76_b'
PS_KEY = 'ecb4242efaf4042be5db42db3b06b93d16e1087c47db79882dc534df9e646f73-b7530f698e76_b'


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
#include "nvn_refl.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <string.h>

#define GAME_BOTW 1
#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); abort(); } while (0)
#define RED_ONCE(_id, ...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
static void die(const char *format, ...) { (void)format; abort(); }
typedef struct { uint8_t minf, magf, ws, wt, wr, cmp_mode, cmp_func, reduction; float lod_min, lod_max, lod_bias, aniso, border[4], lod_snap; } samp_par;
static int g_present_apple5 = 1;
static id<MTLDevice> g_dev;
static _Atomic int g_shadow_cmp_off;
static samp_par *g_sreg; static uint8_t *g_sreg_ok; static void **g_sview; static uint64_t *g_srid; static int g_nsreg;
static float tup_mip_bias(void) { return 0.0f; }
int dm_gfx_aniso(void) { return 1; }
typedef struct { void *view; } test_treg;
static test_treg g_treg[16]; static int g_ntreg=16; static void *g_dsnap_tex;
static int dsnap_hit(int tid) { (void)tid; return 0; }
static int tex_view_valid(int tid) { return tid > 0 && tid < g_ntreg && g_treg[tid].view != NULL; }
static void *tex_by_id_p(int tid) { return tex_view_valid(tid) ? g_treg[tid].view : NULL; }
static id<MTLTexture> hdr_shadow_sample(id<MTLTexture> texture) { (void)texture; return nil; }
// PRODUCTION_HELPERS

static id<MTLBuffer> shared_buffer(id<MTLDevice> dev, NSUInteger length) {
    id<MTLBuffer> b = [dev newBufferWithLength:length options:MTLResourceStorageModeShared];
    if (!b) MDIE("native discrete texture probe buffer allocation failed: %lu", (unsigned long)length);
    memset(b.contents, 0, length);
    return b;
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 5) return 2;
    NSError *error = nil;
    g_dev = MTLCreateSystemDefaultDevice(); if (!g_dev) return 3;
    NSString *vs_source = [NSString stringWithContentsOfFile:[NSString stringWithUTF8String:argv[1]] encoding:NSUTF8StringEncoding error:&error];
    if (!vs_source) MDIE("reading title VS MSL: %s", error.localizedDescription.UTF8String);
    NSString *ps_source = [NSString stringWithContentsOfFile:[NSString stringWithUTF8String:argv[2]] encoding:NSUTF8StringEncoding error:&error];
    if (!ps_source) MDIE("reading title PS MSL: %s", error.localizedDescription.UTF8String);
    id<MTLLibrary> vs_lib = [g_dev newLibraryWithSource:vs_source options:nil error:&error];
    if (!vs_lib) MDIE("compiling title VS MSL: %s", error.localizedDescription.UTF8String);
    id<MTLLibrary> ps_lib = [g_dev newLibraryWithSource:ps_source options:nil error:&error];
    if (!ps_lib) MDIE("compiling title PS MSL: %s", error.localizedDescription.UTF8String);

    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [vs_lib newFunctionWithName:@"main0"];
    pd.fragmentFunction = [ps_lib newFunctionWithName:@"main0"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    vd.attributes[0].format = MTLVertexFormatFloat4; vd.attributes[0].offset = 0; vd.attributes[0].bufferIndex = 16;
    vd.layouts[16].stride = sizeof(float) * 4; vd.layouts[16].stepFunction = MTLVertexStepFunctionPerVertex;
    pd.vertexDescriptor = vd;
    id<MTLRenderPipelineState> pso = [g_dev newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!pso) MDIE("title VS/PS pipeline: %s", error.localizedDescription.UTF8String);

    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm_sRGB width:2 height:2 mipmapped:NO];
    td.storageMode = MTLStorageModeShared; td.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> base = [g_dev newTextureWithDescriptor:td]; if (!base) MDIE("sRGB texture allocation failed");
    const uint8_t texels[16] = {255,0,0,255, 0,255,0,255, 0,0,255,255, 128,128,128,255};
    [base replaceRegion:MTLRegionMake2D(0,0,2,2) mipmapLevel:0 withBytes:texels bytesPerRow:8];
    MTLTextureSwizzleChannels swizzle = {MTLTextureSwizzleBlue, MTLTextureSwizzleRed, MTLTextureSwizzleGreen, MTLTextureSwizzleAlpha};
    id<MTLTexture> view = [base newTextureViewWithPixelFormat:base.pixelFormat textureType:MTLTextureType2D levels:NSMakeRange(0,1) slices:NSMakeRange(0,1) swizzle:swizzle];
    if (!view || view.pixelFormat != MTLPixelFormatRGBA8Unorm_sRGB || view.textureType != MTLTextureType2D) MDIE("sRGB swizzled view creation failed");

    // Resolve the title's combined NVN handle through the production texture/sampler object helpers.
    g_treg[3].view = (__bridge void *)view;
    g_sreg = calloc(16, sizeof *g_sreg); g_sreg_ok = calloc(16, 1); g_sview = calloc(16, sizeof *g_sview); g_srid = calloc(16, sizeof *g_srid); g_nsreg = 16;
    if (!g_sreg || !g_sreg_ok || !g_sview || !g_srid) MDIE("sampler test registry allocation failed");
    g_sreg[7] = (samp_par){.minf=0,.magf=0,.ws=0,.wt=0,.wr=0,.lod_max=1,.aniso=1}; g_sreg_ok[7] = 1;
    g_sreg[8] = (samp_par){.minf=1,.magf=1,.ws=0,.wt=0,.wr=0,.lod_max=1,.aniso=1}; g_sreg_ok[8] = 1;
    uint64_t nearest_handle = ((uint64_t)7 << 20) | 3, linear_handle = ((uint64_t)8 << 20) | 3;
    id<MTLTexture> sampled = (__bridge id<MTLTexture>)tex_fast((int)(nearest_handle & 0xfffff));
    id<MTLSamplerState> nearest = (__bridge id<MTLSamplerState>)samp_fast((int)(nearest_handle >> 20));
    id<MTLSamplerState> linear = (__bridge id<MTLSamplerState>)samp_fast((int)(linear_handle >> 20));
    if (sampled != view || !nearest || !linear || nearest == linear || g_srid[7] || g_srid[8]) MDIE("native object resolution or Apple5 no-resource-ID invariant failed");

    const NSUInteger cbuf_slot = (NSUInteger)strtoul(argv[3], NULL, 10);
    const NSUInteger texture_slot = (NSUInteger)strtoul(argv[4], NULL, 10);
    id<MTLBuffer> cbuf = shared_buffer(g_dev, 65536);
    float *u = (float *)cbuf.contents;
    u[4 * 1 + 0] = 1.0f; u[4 * 2 + 1] = 1.0f; u[4 * 4 + 3] = 1.0f;
    u[4 * 6 + 0] = 1.0f; u[4 * 6 + 1] = 1.0f;
    float vertices[12] = {-1,-1,0,1, 3,-1,0,1, -1,3,0,1};
    id<MTLBuffer> vb = shared_buffer(g_dev, sizeof vertices); memcpy(vb.contents, vertices, sizeof vertices);

    MTLTextureDescriptor *outd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
    outd.storageMode = MTLStorageModePrivate; outd.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> targets[2] = {[g_dev newTextureWithDescriptor:outd], [g_dev newTextureWithDescriptor:outd]};
    if (!targets[0] || !targets[1]) MDIE("render target allocation failed");
    id<MTLBuffer> readback = shared_buffer(g_dev, 16 * 16 * 4 * 2);
    id<MTLCommandQueue> q = [g_dev newCommandQueue]; id<MTLCommandBuffer> cb = [q commandBuffer];
    nvn_rbind cbinding = {.kind=NVN_RB_CBUF,.metal_class=NVN_METAL_CLASS_BUFFER,.metal_slot=(uint8_t)cbuf_slot};
    nvn_rbind vtex = {.kind=NVN_RB_TEX,.metal_class=NVN_METAL_CLASS_TEXTURE,.metal_slot=(uint8_t)texture_slot};
    nvn_rbind vsamp = {.kind=NVN_RB_SAMPT,.metal_class=NVN_METAL_CLASS_SAMPLER,.metal_slot=0};
    nvn_rbind ptex = {.kind=NVN_RB_TEX,.metal_class=NVN_METAL_CLASS_TEXTURE,.metal_slot=(uint8_t)texture_slot};
    nvn_rbind psamp = {.kind=NVN_RB_SAMPT,.metal_class=NVN_METAL_CLASS_SAMPLER,.metal_slot=0};
    id<MTLSamplerState> samplers[2] = {nearest, linear};
    for (int i=0; i<2; i++) {
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = targets[i]; rp.colorAttachments[0].loadAction = MTLLoadActionClear; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> en = [cb renderCommandEncoderWithDescriptor:rp];
        [en setRenderPipelineState:pso]; [en setCullMode:MTLCullModeNone];
        discrete_bind_buffer(en, 0, &cbinding, cbuf, 0);
        [en setVertexBuffer:vb offset:0 atIndex:16];
        // Same numeric slot in the three native namespaces is legal; only fragment resources are sampled by this title PS.
        discrete_bind_texture(en, 0, &vtex, sampled); discrete_bind_sampler(en, 0, &vsamp, nearest);
        discrete_bind_texture(en, 1, &ptex, sampled); discrete_bind_sampler(en, 1, &psamp, samplers[i]);
        [en drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [en endEncoding];
    }
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    for (int i=0; i<2; i++) [blit copyFromTexture:targets[i] sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
        toBuffer:readback destinationOffset:(NSUInteger)i*1024 destinationBytesPerRow:64 destinationBytesPerImage:1024];
    [blit endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (cb.error) MDIE("GPU error: %s", cb.error.localizedDescription.UTF8String);
    const uint8_t *nearest_pixel = (const uint8_t *)readback.contents + (1 * 16 + 14) * 4;
    const uint8_t *linear_pixel = (const uint8_t *)readback.contents + 1024 + (1 * 16 + 14) * 4;
    const int nearest_expected[4] = {255,0,0,255}, linear_expected[4] = {103,75,55,255};
    for (int c=0;c<4;c++) {
        if (abs((int)nearest_pixel[c]-nearest_expected[c]) > 4) MDIE("nearest/sRGB/swizzle output channel %d: got %u expected %d",c,nearest_pixel[c],nearest_expected[c]);
        if (abs((int)linear_pixel[c]-linear_expected[c]) > 5) MDIE("linear/sRGB/swizzle output channel %d: got %u expected %d",c,linear_pixel[c],linear_expected[c]);
    }
    puts("PASS production discrete TEX/SAMPT: title VS+PS, independent slot-0 namespaces, sRGB swizzle and nearest/linear sampling");
    return 0;
} }
'''


class DiscreteTextureSamplerBinding(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
    def test_title_texture_sampler_bindings_and_sampling(self):
        shader_dir = os.environ.get('TOTK_DISCRETE_MSL_DIR')
        if not shader_dir:
            self.skipTest('set TOTK_DISCRETE_MSL_DIR to the generated 16-title MSL fixture directory')
        fixture = Path(shader_dir)
        vs_metal, vs_json = fixture / f'{VS_KEY}.metal', fixture / f'{VS_KEY}.json'
        ps_metal, ps_json = fixture / f'{PS_KEY}.metal', fixture / f'{PS_KEY}.json'
        if not all(path.is_file() for path in (vs_metal, vs_json, ps_metal, ps_json)):
            self.skipTest(f'missing generated title shader fixtures in {fixture}')
        vs = json.loads(vs_json.read_text())
        ps = json.loads(ps_json.read_text())
        self.assertEqual((vs.get('stage'), vs.get('bind_model')), ('vs', 'discrete-v1'))
        self.assertEqual((ps.get('stage'), ps.get('bind_model')), ('ps', 'discrete-v1'))
        vb = {(b.get('kind'), b.get('metal_class')): b.get('metal_slot') for b in vs['bindings']}
        pb = {(b.get('kind'), b.get('metal_class')): b.get('metal_slot') for b in ps['bindings']}
        self.assertEqual(vb.get(('cbuf', 'buffer')), 0)
        self.assertEqual(pb.get(('tex', 'texture')), 0)
        self.assertEqual(pb.get(('samp_tex', 'sampler')), 0)

        source = (NVN / 'nvn_mtl.m').read_text()
        signatures = (
            'static void discrete_bind_buffer(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
            'static void discrete_bind_texture(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
            'static void discrete_bind_sampler(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
            'static inline __attribute__((always_inline)) void *tex_fast(int tid)',
            'static MTLSamplerDescriptor *samp_desc(samp_par p)',
            'static id<MTLSamplerState> samp_by_id(int sid)',
            'static inline __attribute__((always_inline)) void *samp_fast(int sid)',
        )
        helpers = '\n'.join(production_function(source, signature) for signature in signatures)
        helpers = production_function(source, 'static int samp_border_custom(const samp_par *p)') + '\n' + helpers
        helpers = production_function(source, 'static MTLSamplerAddressMode wrapm(int w)') + '\n' + helpers
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'discrete_texture_sampler.m'
            probe.write_text(PROBE.replace('// PRODUCTION_HELPERS', helpers))
            binary = work / 'discrete_texture_sampler'
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-I', str(NVN), str(probe), '-framework', 'Foundation',
                            '-framework', 'Metal', '-o', str(binary)], check=True)
            result = subprocess.run([str(binary), str(vs_metal), str(ps_metal), str(vb[('cbuf', 'buffer')]),
                                     str(pb[('tex', 'texture')])], check=False, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            out = result.stdout
            self.assertIn('PASS production discrete TEX/SAMPT: title VS+PS, independent slot-0 namespaces, sRGB swizzle and nearest/linear sampling', out)


if __name__ == '__main__':
    unittest.main()
