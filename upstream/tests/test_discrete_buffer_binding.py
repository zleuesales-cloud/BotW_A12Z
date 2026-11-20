"""Exercise the production discrete buffer binder with a real generated title VS."""
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

#define MLOG(...) fprintf(stderr, __VA_ARGS__)
#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); abort(); } while (0)
// PRODUCTION_HELPER

static id<MTLBuffer> shared_buffer(id<MTLDevice> dev, NSUInteger length) {
    id<MTLBuffer> b = [dev newBufferWithLength:length options:MTLResourceStorageModeShared];
    if (!b) MDIE("native discrete binding probe buffer allocation failed: %lu", (unsigned long)length);
    memset(b.contents, 0, length);
    return b;
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 3) return 2;
    NSError *error = nil;
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice(); if (!dev) return 3;
    NSString *vs_path = [NSString stringWithUTF8String:argv[1]];
    NSString *vs_source = [NSString stringWithContentsOfFile:vs_path encoding:NSUTF8StringEncoding error:&error];
    if (!vs_source) MDIE("reading title MSL: %s", error.localizedDescription.UTF8String);
    NSString *fs_source = @"\nstruct bind_probe_in { float4 color [[user(locn0)]]; };\nfragment float4 bind_probe_fs(bind_probe_in in [[stage_in]]) { return float4(in.color.y, in.color.z, 0.25, 1.0); }\n";
    id<MTLLibrary> lib = [dev newLibraryWithSource:[vs_source stringByAppendingString:fs_source] options:nil error:&error];
    if (!lib) MDIE("compiling title VS MSL: %s", error.localizedDescription.UTF8String);

    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"main0"];
    pd.fragmentFunction = [lib newFunctionWithName:@"bind_probe_fs"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    vd.attributes[0].format = MTLVertexFormatFloat4; vd.attributes[0].offset = 0; vd.attributes[0].bufferIndex = 16;
    vd.layouts[16].stride = sizeof(float) * 4; vd.layouts[16].stepFunction = MTLVertexStepFunctionPerVertex;
    pd.vertexDescriptor = vd;
    id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!pso) MDIE("title VS pipeline: %s", error.localizedDescription.UTF8String);

    const NSUInteger bind_offset = 256, uniform_length = 65536;
    id<MTLBuffer> cbuf = shared_buffer(dev, bind_offset + uniform_length);
    float *u = (float *)((uint8_t *)cbuf.contents + bind_offset);
    u[4 * 0 + 2] = 1.0f;  // CBUF m0[0].z: makes the varying depend on the C1 value.
    u[4 * 1 + 0] = 1.0f;  // CBUF m0[1].x: preserve clip-space x.
    u[4 * 2 + 1] = 1.0f;  // CBUF m0[2].y: preserve clip-space y.
    u[4 * 4 + 3] = 1.0f;  // CBUF m0[4].w: homogeneous w.
    id<MTLBuffer> c1 = shared_buffer(dev, bind_offset + uniform_length);
    size_t c1_len = strlen(argv[2]) / 2;
    if (!c1_len || (strlen(argv[2]) & 1) || c1_len > uniform_length) return 4;
    for (size_t i = 0; i < c1_len; i++) {
        unsigned byte = 0;
        if (sscanf(argv[2] + 2 * i, "%2x", &byte) != 1) return 5;
        ((uint8_t *)c1.contents)[bind_offset + i] = (uint8_t)byte;
    }
    float vertices[12] = {-1,-1,0,1, 3,-1,0,1, -1,3,0,1};
    id<MTLBuffer> vb = shared_buffer(dev, sizeof vertices); memcpy(vb.contents, vertices, sizeof vertices);

    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
    td.storageMode = MTLStorageModePrivate; td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> target = [dev newTextureWithDescriptor:td]; if (!target) return 6;
    id<MTLCommandQueue> q = [dev newCommandQueue]; id<MTLCommandBuffer> cb = [q commandBuffer];
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = target; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> en = [cb renderCommandEncoderWithDescriptor:rp];
    [en setRenderPipelineState:pso]; [en setCullMode:MTLCullModeNone];
    [en setVertexBuffer:vb offset:0 atIndex:16];
    nvn_rbind cbinding = {.kind=NVN_RB_CBUF,.metal_class=NVN_METAL_CLASS_BUFFER,.metal_slot=0};
    nvn_rbind c1binding = {.kind=NVN_RB_C1,.metal_class=NVN_METAL_CLASS_BUFFER,.metal_slot=1};
    discrete_bind_buffer(en, 0, &cbinding, cbuf, bind_offset);
    discrete_bind_buffer(en, 0, &c1binding, c1, bind_offset);
    [en drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [en endEncoding];

    id<MTLBuffer> readback = shared_buffer(dev, 16 * 16 * 4);
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    [blit copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
        toBuffer:readback destinationOffset:0 destinationBytesPerRow:64 destinationBytesPerImage:1024];
    [blit endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (cb.error) MDIE("GPU error: %s", cb.error.localizedDescription.UTF8String);
    const uint8_t *pixel = (const uint8_t *)readback.contents + (8 * 16 + 8) * 4;
    const int expected[4] = {0,255,64,255};
    for (int i=0;i<4;i++) if (abs((int)pixel[i]-expected[i]) > 2)
        MDIE("typed title VS buffer slot output channel %d: got %u expected %d", i, pixel[i], expected[i]);
    puts("PASS production discrete CBUF/C1 binding: title VS MSL rendered through slots 0/1 at offset 256");
    return 0;
} }
'''


class DiscreteBufferBinding(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Metal')
    def test_production_binder_with_title_vertex_shader(self):
        shader_dir = os.environ.get('TOTK_DISCRETE_MSL_DIR')
        if not shader_dir:
            self.skipTest('set TOTK_DISCRETE_MSL_DIR to the generated 16-title MSL fixture directory')
        fixture = Path(shader_dir)
        metal = fixture / f'{VS_KEY}.metal'
        metadata_path = fixture / f'{VS_KEY}.json'
        if not metal.is_file() or not metadata_path.is_file():
            self.skipTest(f'missing generated title shader fixture in {fixture}')
        metadata = json.loads(metadata_path.read_text())
        self.assertEqual(metadata.get('stage'), 'vs')
        self.assertEqual(metadata.get('bind_model'), 'discrete-v1')
        bindings = {(b.get('kind'), b.get('metal_class'), b.get('metal_slot')) for b in metadata['bindings']}
        self.assertIn(('cbuf', 'buffer', 0), bindings)
        self.assertIn(('c1', 'buffer', 1), bindings)
        self.assertTrue(metadata.get('c1'))

        source = (NVN / 'nvn_mtl.m').read_text()
        helper = production_function(source, 'static void discrete_bind_buffer(id<MTLCommandEncoder> en, int nvn_stage, const nvn_rbind *binding,')
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = work / 'discrete_buffer.m'
            probe.write_text(PROBE.replace('// PRODUCTION_HELPER', helper))
            binary = work / 'discrete_buffer'
            subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-I', str(NVN), str(probe), '-framework', 'Foundation',
                            '-framework', 'Metal', '-o', str(binary)], check=True)
            out = subprocess.run([str(binary), str(metal), metadata['c1']], check=True, capture_output=True, text=True, timeout=30).stdout
            self.assertIn('PASS production discrete CBUF/C1 binding: title VS MSL rendered through slots 0/1 at offset 256', out)


if __name__ == '__main__':
    unittest.main()
