using System;
using System.Linq;
using System.Reflection;
using System.Text.Json.Nodes;
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
        Console.WriteLine("discrete reflection fixture passed");
    }
}
