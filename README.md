# BotW A12Z

Target: BOTW 1.9.0 + DLC on iPad8,9. 1280x720 internal, 1920x1080 output, real 30 FPS without interpolation.

Reuse the mature runtime and complete NVN renderer. Replace only unsupported A12Z mechanisms. Preserve resource lifetime, synchronization, rendering semantics and input/save support.

Current: source-base audit. No source base selected and no third-version App built. Existing game resources stay in their original location. Hardware evidence is preserved in evidence/APPLE5_CAPABILITIES.json; argument_buffer_tier is the raw Metal enum (0 means Tier 1).

Next gate: recover historical build provenance, audit the traditional Metal candidate, pin the chosen source and dependencies, then build and run on the connected iPad.
