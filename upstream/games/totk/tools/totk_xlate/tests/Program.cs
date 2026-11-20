using System;
using System.Linq;
using System.Reflection;
using System.Text.Json.Nodes;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.ExceptionServices;

static class ReflectionBindingTests
{
    static void Check(bool ok, string message)
    {
        if (!ok) throw new InvalidOperationException(message);
    }

    static void Main()
    {
        Assembly service = Assembly.Load("totk_xlate");
        Type shaderService = service.GetType("ShaderService", throwOnError: true)!;
        const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
        shaderService.GetField("bindingAbi", flags)!.SetValue(null, "discrete-v1");

        // The comma belongs to the MSL texture template type. Same numeric slot in each Metal class is legal.
        const string msl = "fragment void main0(constant _16& fp_c3 [[buffer(0)]], " +
                           "texture_buffer<float, access::read> fp_t_tcb_8 [[texture(0)]], " +
                           "sampler fp_t_tcb_8Smplr [[sampler(0)]]) { }\n" +
                           "void helper(constant _16& helper_c3 [[buffer(0)]]) { }\n" +
                           "fragment void other(constant _16& other_c3 [[buffer(0)]]) { }";
        var reflection = (JsonArray)shaderService.GetMethod("Reflect", flags)!.Invoke(null,
            new object[] { "ps", msl, new JsonObject { ["sbufs"] = new JsonArray() }, "main0" })!;
        Check(reflection.Count == 3, "expected exactly the entry-point bindings");
        JsonObject buffer = reflection.Single(x => (string?)x!["metal_class"] == "buffer")!.AsObject();
        JsonObject texture = reflection.Single(x => (string?)x!["metal_class"] == "texture")!.AsObject();
        JsonObject sampler = reflection.Single(x => (string?)x!["metal_class"] == "sampler")!.AsObject();
        Check((string?)buffer["kind"] == "cbuf" && (long)buffer["reg"]! == 0, "buffer semantic mapping lost");
        Check((string?)texture["msl_type"] == "texture_buffer<float, access::read>", "comma-bearing MSL type was truncated");
        Check((string?)texture["kind"] == "tbuf" && (long)texture["reg"]! == 0, "texture-buffer semantic mapping lost");
        Check((string?)sampler["kind"] == "samp_tex" && (long)sampler["reg"]! == 0, "sampler semantic mapping lost");

        void ExpectReflectionError(string badMsl, string expected)
        {
            try
            {
                shaderService.GetMethod("Reflect", flags)!.Invoke(null,
                    new object[] { "ps", badMsl, new JsonObject { ["sbufs"] = new JsonArray() }, "main0" });
            }
            catch (TargetInvocationException ex) when (ex.InnerException is not null)
            {
                if (!ex.InnerException.Message.Contains(expected, StringComparison.Ordinal))
                    throw new InvalidOperationException($"expected reflection error containing '{expected}', got '{ex.InnerException.Message}'");
                return;
            }
            throw new InvalidOperationException($"expected reflection error containing '{expected}'");
        }

        ExpectReflectionError("fragment void main0(constant _16& fp_c3 [[buffer(0)]], texture2d<float> fp_t_tcb_8 [[buffer(1)]]) {}",
            "cannot bind as Metal buffer");
        ExpectReflectionError("fragment void main0(texture2d<float> fp_t_tcb_8) {}",
            "entry resource is not recognized");
        ExpectReflectionError("fragment void main0(constant _16& fp_c3 [[buffer]]) {}",
            "entry resource is not recognized");

        // Direct-member MSL (discrete) and dereferenced argument-buffer MSL share one driver-read reflection path.
        var reads = (JsonObject)shaderService.GetMethod("DrvReads", flags)!.Invoke(null,
            new object[] { "auto a = fp_c0._m0[2]; auto b = fp_c2._m0[5];", "ps" })!;
        Check((long)reads["0"]!["vec4_offsets"]![0]! == 32, "discrete c0 read was not recorded");
        Check((long)reads["2"]!["vec4_offsets"]![0]! == 80, "discrete c2 read was not recorded");

        MethodInfo configure = shaderService.GetMethod("Configure", flags | BindingFlags.Public)!;
        MethodInfo configureAbi = shaderService.GetMethod("ConfigureAbi", flags | BindingFlags.Public)!;
        var abiField = shaderService.GetField("bindingAbi", flags)!;
        var outField = shaderService.GetField("outDir", flags)!;
        string root = Path.Combine(Path.GetTempPath(), "totk-xlate-abi-" + Guid.NewGuid().ToString("N"));
        string discreteRoot = root + ".discrete-v1";
        try
        {
            configureAbi.Invoke(null, new object[] { root, "chain", 1, "discrete-v1" });
            Check((string?)abiField.GetValue(null) == "discrete-v1" && (string?)outField.GetValue(null) == discreteRoot,
                "discrete service did not select its isolated output namespace");
            configureAbi.Invoke(null, new object[] { discreteRoot, "chain", 1, "discrete-v1" });
            Check((string?)outField.GetValue(null) == discreteRoot, "discrete service double-suffixed its output namespace");
            configure.Invoke(null, new object[] { root, "chain", 1 });
            Check((string?)abiField.GetValue(null) == "ab" && (string?)outField.GetValue(null) == root,
                "legacy service configuration no longer selects AB");
            try
            {
                configureAbi.Invoke(null, new object[] { discreteRoot, "chain", 1, "ab" });
                throw new InvalidOperationException("AB service accepted a discrete-v1 output root");
            }
            catch (TargetInvocationException ex) when (ex.InnerException is not null && ex.InnerException.Message.Contains("reserved for the discrete-v1 namespace", StringComparison.Ordinal)) { }
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
            if (Directory.Exists(discreteRoot)) Directory.Delete(discreteRoot, recursive: true);
        }

        Type exports = service.GetType("LibExports", throwOnError: true)!;
        var legacyInit = exports.GetMethod("Init", flags)!;
        var abiInit = exports.GetMethod("InitAbi", flags)!;
        Check(legacyInit.GetParameters().Length == 3 &&
              legacyInit.GetCustomAttribute<UnmanagedCallersOnlyAttribute>()?.EntryPoint == "totk_xlate_init",
            "legacy NativeAOT initializer export or signature changed");
        Check(abiInit.GetParameters().Length == 4 &&
              abiInit.GetCustomAttribute<UnmanagedCallersOnlyAttribute>()?.EntryPoint == "totk_xlate_init_abi",
            "ABI-aware NativeAOT initializer is missing or reused the legacy export");
        Console.WriteLine("discrete reflection fixture passed");
    }
}
