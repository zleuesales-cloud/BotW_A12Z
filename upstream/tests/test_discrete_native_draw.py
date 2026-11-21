"""Exercise native draw helpers with a generated title vertex shader."""
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
VS_KEY = '6073f8d9957a606cdb77c17d5a496c47f79e40d27a9d5b661b20403fb3c08e98-b7530f698e76_b'


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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); abort(); } while (0)
typedef struct { int unused; } tex_t;
static void use_rec(const char *kind, uint64_t addr, uint64_t len, int tid, const tex_t *texture) {
    (void)kind; (void)addr; (void)len; (void)tid; (void)texture;
}
static struct { uint64_t address; id<MTLBuffer> buffer; } mappings[8];
static unsigned mapping_count;
static int mtl_addr(uint64_t address, uint64_t length, void **object, uint64_t *offset) {
    for (unsigned i = 0; i < mapping_count; i++) {
        uint64_t base = mappings[i].address;
        if (address < base || address - base > mappings[i].buffer.length) continue;
        uint64_t off = address - base;
        if (off > mappings[i].buffer.length || length > mappings[i].buffer.length - off) continue;
        *object = (__bridge void *)mappings[i].buffer; *offset = off; return 1;
    }
    return 0;
}
// PRODUCTION_HELPERS

static id<MTLBuffer> shared_buffer(id<MTLDevice> dev, NSUInteger length) {
    id<MTLBuffer> b = [dev newBufferWithLength:length options:MTLResourceStorageModeShared];
    if (!b) MDIE("native draw probe buffer allocation failed: %lu", (unsigned long)length);
    memset(b.contents, 0, length);
    return b;
}
static void map_buffer(uint64_t address, id<MTLBuffer> buffer) {
    if (mapping_count >= sizeof mappings / sizeof mappings[0]) MDIE("test mapping table full");
    mappings[mapping_count++] = (typeof(mappings[0])){address, buffer};
}
static void encode_draw(id<MTLCommandBuffer> cb, id<MTLTexture> target, id<MTLRenderPipelineState> pso,
                        id<MTLBuffer> cbuf, id<MTLBuffer> c1, const nvn_rbind *cbinding,
                        const nvn_rbind *c1binding, int mode) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = target; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> en = [cb renderCommandEncoderWithDescriptor:rp];
    [en setRenderPipelineState:pso]; [en setCullMode:MTLCullModeNone];
    discrete_bind_buffer(en, 0, cbinding, cbuf, 256);
    discrete_bind_buffer(en, 0, c1binding, c1, 256);
    if (mode == 0) {
        classic_bind_vertex_address(en, 16, 0x1010, 64);
        classic_draw_indexed(en, MTLPrimitiveTypeTriangle, 3, 1, 0x2002, 2, 1, 0);
    } else if (mode == 1) {
        classic_bind_vertex_address(en, 16, 0x1010, 64);
        classic_draw_arrays_indirect(en, MTLPrimitiveTypeTriangle, 0x3004);
    } else if (mode == 2) {
        classic_bind_vertex_address(en, 16, 0x1010, 64);
        classic_draw_indexed_indirect(en, MTLPrimitiveTypeTriangle, 1, 0x2000, 0x3020);
    } else if (mode == 3) {
        classic_bind_vertex_address(en, 16, 0x4000, 32);
        classic_draw_arrays(en, MTLPrimitiveTypePoint, 0, 1, 1, 1);
    }
    [en endEncoding];
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 3) return 2;
    NSError *error = nil;
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice(); if (!dev) return 3;
    NSString *vs_source = [NSString stringWithContentsOfFile:[NSString stringWithUTF8String:argv[1]] encoding:NSUTF8StringEncoding error:&error];
    if (!vs_source) MDIE("reading title VS MSL: %s", error.localizedDescription.UTF8String);
    NSString *fs_source = @"\nstruct draw_probe_in { float4 color [[user(locn0)]]; };\nfragment float4 draw_probe_fs(draw_probe_in in [[stage_in]]) { return float4(0.125, 0.25, 0.375, 0.25); }\n";
    id<MTLLibrary> lib = [dev newLibraryWithSource:[vs_source stringByAppendingString:fs_source] options:nil error:&error];
    if (!lib) MDIE("compiling title VS MSL: %s", error.localizedDescription.UTF8String);
    const NSUInteger bind_offset = 256, uniform_length = 65536;
    id<MTLBuffer> cbuf = shared_buffer(dev, bind_offset + uniform_length);
    float *u = (float *)((uint8_t *)cbuf.contents + bind_offset);
    u[4 * 0 + 2] = 1.0f; u[4 * 1 + 0] = 1.0f; u[4 * 2 + 1] = 1.0f; u[4 * 4 + 3] = 1.0f;
    id<MTLBuffer> c1 = shared_buffer(dev, bind_offset + uniform_length);
    size_t c1_len = strlen(argv[2]) / 2;
    if (!c1_len || (strlen(argv[2]) & 1) || c1_len > uniform_length) return 4;
    for (size_t i = 0; i < c1_len; i++) { unsigned byte = 0; if (sscanf(argv[2] + 2 * i, "%2x", &byte) != 1) return 5;
        ((uint8_t *)c1.contents)[bind_offset + i] = (uint8_t)byte; }

    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"main0"]; pd.fragmentFunction = [lib newFunctionWithName:@"draw_probe_fs"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    pd.colorAttachments[0].blendingEnabled = YES;
    pd.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorOne; pd.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOne;
    pd.colorAttachments[0].rgbBlendOperation = MTLBlendOperationAdd;
    pd.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorOne; pd.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorOne;
    pd.colorAttachments[0].alphaBlendOperation = MTLBlendOperationAdd;
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    vd.attributes[0].format = MTLVertexFormatFloat4; vd.attributes[0].offset = 0; vd.attributes[0].bufferIndex = 16;
    vd.layouts[16].stride = sizeof(float) * 4; vd.layouts[16].stepFunction = MTLVertexStepFunctionPerVertex;
    pd.vertexDescriptor = vd;
    id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!pso) MDIE("title VS draw pipeline: %s", error.localizedDescription.UTF8String);
    MTLVertexDescriptor *instance_vd = [MTLVertexDescriptor vertexDescriptor];
    instance_vd.attributes[0].format = MTLVertexFormatFloat4; instance_vd.attributes[0].offset = 0; instance_vd.attributes[0].bufferIndex = 16;
    instance_vd.layouts[16].stride = sizeof(float) * 4; instance_vd.layouts[16].stepFunction = MTLVertexStepFunctionPerInstance;
    MTLRenderPipelineDescriptor *instance_pd = [MTLRenderPipelineDescriptor new];
    instance_pd.vertexFunction = pd.vertexFunction; instance_pd.fragmentFunction = pd.fragmentFunction;
    instance_pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm; instance_pd.vertexDescriptor = instance_vd;
    id<MTLRenderPipelineState> instance_pso = [dev newRenderPipelineStateWithDescriptor:instance_pd error:&error];
    if (!instance_pso) MDIE("title VS instance pipeline: %s", error.localizedDescription.UTF8String);

    id<MTLBuffer> vb = shared_buffer(dev, 80);
    float vertices[20] = {9,9,9,1, 8,8,8,1, -1,-1,0,1, 3,-1,0,1, -1,3,0,1};
    memcpy(vb.contents, vertices, sizeof vertices); map_buffer(0x1000, vb);
    id<MTLBuffer> ib = shared_buffer(dev, 16); uint16_t indices[8] = {0,0,1,2,3,0,0,0};
    memcpy(ib.contents, indices, sizeof indices); map_buffer(0x2000, ib);
    id<MTLBuffer> args = shared_buffer(dev, 64);
    uint32_t arrays_args[4] = {3,1,1,0}; memcpy((uint8_t *)args.contents + 4, arrays_args, sizeof arrays_args);
    uint32_t indexed_args[5] = {3,2,1,1,0}; memcpy((uint8_t *)args.contents + 32, indexed_args, sizeof indexed_args);
    map_buffer(0x3000, args);
    id<MTLBuffer> instance_vb = shared_buffer(dev, 32); float instance_positions[8] = {-0.5f,0,0,1, 0.5f,0,0,1};
    memcpy(instance_vb.contents, instance_positions, sizeof instance_positions); map_buffer(0x4000, instance_vb);

    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
    td.storageMode = MTLStorageModePrivate; td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> targets[4]; for (int i=0;i<4;i++) { targets[i] = [dev newTextureWithDescriptor:td]; if (!targets[i]) MDIE("draw target allocation failed"); }
    id<MTLCommandQueue> q = [dev newCommandQueue]; id<MTLCommandBuffer> cb = [q commandBuffer];
    nvn_rbind cbinding = {.kind=NVN_RB_CBUF,.metal_class=NVN_METAL_CLASS_BUFFER,.metal_slot=0};
    nvn_rbind c1binding = {.kind=NVN_RB_C1,.metal_class=NVN_METAL_CLASS_BUFFER,.metal_slot=1};
    encode_draw(cb, targets[0], pso, cbuf, c1, &cbinding, &c1binding, 0);
    encode_draw(cb, targets[1], pso, cbuf, c1, &cbinding, &c1binding, 1);
    encode_draw(cb, targets[2], pso, cbuf, c1, &cbinding, &c1binding, 2);
    encode_draw(cb, targets[3], instance_pso, cbuf, c1, &cbinding, &c1binding, 3);
    id<MTLBuffer> readback = shared_buffer(dev, 4 * 16 * 16 * 4);
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    for (NSUInteger i=0;i<4;i++) [blit copyFromTexture:targets[i] sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
        toBuffer:readback destinationOffset:i*1024 destinationBytesPerRow:64 destinationBytesPerImage:1024];
    [blit endEncoding];
    for (NSUInteger i=0;i<4;i++) targets[i] = nil;
    for (unsigned i=0;i<mapping_count;i++) mappings[i].buffer = nil; mapping_count = 0;
    vb = nil; ib = nil; args = nil; instance_vb = nil; cbuf = nil; c1 = nil; pso = nil; instance_pso = nil; lib = nil;
    [cb commit]; [cb waitUntilCompleted];
    if (cb.error) MDIE("native draw GPU error: %s", cb.error.localizedDescription.UTF8String);
    const uint8_t expected[3][4] = {{64,128,191,255}, {32,64,96,255}, {64,128,191,255}};
    for (int target=0;target<3;target++) {
        const uint8_t *pixel=(const uint8_t *)readback.contents + (NSUInteger)target*1024 + (8*16+8)*4;
        for(int c=0;c<4;c++) if(abs((int)pixel[c]-(int)expected[target][c])>3)
            MDIE("native draw target %d channel %d got %u expected %u",target,c,pixel[c],expected[target][c]);
    }
    const uint8_t *point_pixels=(const uint8_t *)readback.contents + 3*1024;
    int point_count=0,min_x=16,max_x=-1;
    for(int y=0;y<16;y++)for(int x=0;x<16;x++)if(point_pixels[(y*16+x)*4]){point_count++;if(x<min_x)min_x=x;if(x>max_x)max_x=x;}
    if (!point_count || min_x < 10 || max_x > 14) MDIE("baseInstance point location x=%d..%d count=%d; expected second per-instance position",min_x,max_x,point_count);
    puts("PASS native title VS draw helpers: direct indexed offset/baseVertex/2 instances, arrays indirect, indexed indirect, baseInstance");
    return 0;
} }
'''


COMPUTE_PROBE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "nvn_refl.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); abort(); } while (0)
// PRODUCTION_HELPERS

int main(void) { @autoreleasepool {
    NSError *error = nil;
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice(); if (!dev) return 3;
    NSString *src =
        @"#include <metal_stdlib>\nusing namespace metal;\n"
         "kernel void probe_cs(constant uint4 *drv0 [[buffer(0)]],\n"
         "                     device uint4 *sbuf [[buffer(1)]],\n"
         "                     texture2d<float> tex [[texture(0)]],\n"
         "                     sampler samp [[sampler(0)]],\n"
         "                     uint gid [[thread_position_in_grid]]) {\n"
         "    if (gid != 0) return;\n"
         "    float4 s = tex.sample(samp, float2(0.5, 0.5));\n"
         "    sbuf[0] = uint4(drv0[0].x + 10u, drv0[0].y + 20u, uint(round(s.r * 255.0)), uint(round(s.g * 255.0)));\n"
         "}\n";
    id<MTLLibrary> lib = [dev newLibraryWithSource:src options:nil error:&error];
    if (!lib) MDIE("compute probe MSL compile failed: %s", error.localizedDescription.UTF8String);
    id<MTLComputePipelineState> cps = [dev newComputePipelineStateWithFunction:[lib newFunctionWithName:@"probe_cs"] error:&error];
    if (!cps) MDIE("compute probe PSO failed: %s", error.localizedDescription.UTF8String);

    id<MTLBuffer> drv0 = [dev newBufferWithLength:512 options:MTLResourceStorageModeShared];
    id<MTLBuffer> sbuf = [dev newBufferWithLength:512 options:MTLResourceStorageModeShared];
    memset(drv0.contents, 0, 512); memset(sbuf.contents, 0, 512);
    uint32_t *d = (uint32_t *)((uint8_t *)drv0.contents + 128);
    d[0] = 7; d[1] = 11;

    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:2 height:2 mipmapped:NO];
    td.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> tex = [dev newTextureWithDescriptor:td];
    const uint8_t px[16] = {100,150,200,255, 100,150,200,255, 100,150,200,255, 100,150,200,255};
    [tex replaceRegion:MTLRegionMake2D(0, 0, 2, 2) mipmapLevel:0 withBytes:px bytesPerRow:8];

    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = MTLSamplerMinMagFilterNearest; sd.magFilter = MTLSamplerMinMagFilterNearest;
    id<MTLSamplerState> samp = [dev newSamplerStateWithDescriptor:sd];

    id<MTLCommandQueue> q = [dev newCommandQueue];
    id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    [ce setComputePipelineState:cps];

    nvn_rbind b_drv0 = {.kind = NVN_RB_DRV0, .metal_class = NVN_METAL_CLASS_BUFFER, .metal_slot = 0};
    nvn_rbind b_sbuf = {.kind = NVN_RB_SBUFP, .metal_class = NVN_METAL_CLASS_BUFFER, .metal_slot = 1};
    nvn_rbind b_tex  = {.kind = NVN_RB_TEX,   .metal_class = NVN_METAL_CLASS_TEXTURE, .metal_slot = 0};
    nvn_rbind b_samp = {.kind = NVN_RB_SAMPT, .metal_class = NVN_METAL_CLASS_SAMPLER, .metal_slot = 0};

    discrete_bind_buffer(ce, 5, &b_drv0, drv0, 128);
    discrete_bind_buffer(ce, 5, &b_sbuf, sbuf, 256);
    discrete_bind_texture(ce, 5, &b_tex, tex);
    discrete_bind_sampler(ce, 5, &b_samp, samp);
    [ce dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    [ce endEncoding];
    [cb commit]; [cb waitUntilCompleted];
    if (cb.error) MDIE("compute probe GPU error: %s", cb.error.localizedDescription.UTF8String);

    const uint32_t *out = (const uint32_t *)((const uint8_t *)sbuf.contents + 256);
    if (out[0] != 17 || out[1] != 31 || out[2] != 100 || out[3] != 150)
        MDIE("compute probe mismatch: got (%u, %u, %u, %u) expected (17, 31, 100, 150)", out[0], out[1], out[2], out[3]);
    puts("PASS discrete stage 5 compute binding: DRV0, SBUFP, TEX, SAMPT");
    return 0;
} }
'''


class DiscreteNativeDraw(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
    def test_discrete_compute_ssbo_drvcb_tex_sampler_binding(self):
        source = (NVN / 'nvn_mtl.m').read_text()
        signatures = (
            'static void discrete_bind_buffer(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
            'static void discrete_bind_texture(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
            'static void discrete_bind_sampler(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
        )
        helpers = '\n'.join(production_function(source, signature) for signature in signatures)
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'discrete_compute_probe.m'
            probe.write_text(COMPUTE_PROBE.replace('// PRODUCTION_HELPERS', helpers))
            binary = work / 'discrete_compute_probe'
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-I', str(NVN), str(probe), '-framework', 'Foundation',
                            '-framework', 'Metal', '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], check=False, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS discrete stage 5 compute binding: DRV0, SBUFP, TEX, SAMPT', result.stdout)

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
    def test_native_draw_semantics_with_title_vertex_shader(self):
        shader_dir = os.environ.get('TOTK_DISCRETE_MSL_DIR')
        if not shader_dir:
            self.skipTest('set TOTK_DISCRETE_MSL_DIR to the generated 16-title MSL fixture directory')
        fixture = Path(shader_dir)
        metal = fixture / f'{VS_KEY}.metal'
        metadata_path = fixture / f'{VS_KEY}.json'
        if not metal.is_file() or not metadata_path.is_file():
            self.skipTest(f'missing generated title shader fixture in {fixture}')
        metadata = json.loads(metadata_path.read_text())
        self.assertEqual((metadata.get('stage'), metadata.get('bind_model')), ('vs', 'discrete-v1'))
        bindings = {(b.get('kind'), b.get('metal_class'), b.get('metal_slot')) for b in metadata['bindings']}
        self.assertIn(('cbuf', 'buffer', 0), bindings)
        self.assertIn(('c1', 'buffer', 1), bindings)
        self.assertTrue(metadata.get('c1'))

        source = (NVN / 'nvn_mtl.m').read_text()
        signatures = (
            'static void discrete_bind_buffer(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,',
            'static id<MTLBuffer> classic_resolve_buffer(uint64_t address, uint64_t length, uint64_t *offset, const char *what)',
            'static void classic_set_vertex_buffer(id<MTLRenderCommandEncoder> encoder, NSUInteger slot,',
            'static void classic_bind_vertex_address(id<MTLRenderCommandEncoder> encoder, NSUInteger slot,',
            'static void classic_draw_arrays(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,',
            'static MTLIndexType classic_index_type(int nvn_type)',
            'static void classic_draw_indexed(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,',
            'static void classic_draw_arrays_indirect(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,',
            'static void classic_draw_indexed_indirect(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,',
        )
        helpers = '\n'.join(production_function(source, signature) for signature in signatures)
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'discrete_native_draw.m'
            probe.write_text(PROBE.replace('// PRODUCTION_HELPERS', helpers))
            binary = work / 'discrete_native_draw'
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-I', str(NVN), str(probe), '-framework', 'Foundation',
                            '-framework', 'Metal', '-o', str(binary)], check=True)
            result = subprocess.run([str(binary), str(metal), metadata['c1']], check=False, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS native title VS draw helpers: direct indexed offset/baseVertex/2 instances, arrays indirect, indexed indirect, baseInstance', result.stdout)


if __name__ == '__main__':
    unittest.main()
