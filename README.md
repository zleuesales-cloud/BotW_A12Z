# BotW A12Z

BOTW 1.9.0 + DLC for iPad8,9: 1280x720 internal, 1920x1080 output, real 30 FPS, no interpolation, 120-minute stability target.

Source: ly05010419/MacNativeGames at f770289b5d006a539b58698c88e41b80d8116e56. Runtime, complete NVN renderer and required shared source are in upstream/. Dependency versions and audit results are recorded in project.json. Licenses are retained.

Current: the iOS CPU target is apple-a12. Upstream ARM64 timing and pad-repeat checks pass against the existing BotW 1.9.0 image. GPU submission and resource binding still require A12Z adaptation; no third-version playable App has been built.

Existing game data stays outside this repository. The connected iPad already holds the game data under org.macnativegames.botw.a12z; do not uninstall it or replace saves. Hardware evidence is in evidence/APPLE5_CAPABILITIES.json (argument_buffer_tier uses the raw Metal enum: 0 is Tier 1).

The rejected traditional Metal candidate is retained in commit 20b67c2: it lacks BotW version support and uses raw argument-buffer resource layout. Historical Apple5 source is missing; its log contains skipped opcodes, so its successful launch is not gameplay validation.
