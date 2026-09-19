# Duo Input File Provider extension

Native macOS File Provider `.appex` for lazy Windows→macOS file transfer. See the
production design (`docs/superpowers/specs/2026-09-19-macos-file-provider-production-design.md`)
and the implementation plan (`docs/superpowers/plans/2026-09-19-macos-file-provider-production-implementation.md`).

## Bundle identifiers (unique — must not collide with spike builds)

| Role | Identifier |
|---|---|
| extension (`.appex`) | `com.duoinput.configurator.fileprovider` |
| test bundle | `com.duoinput.configurator.fileprovider.tests` |
| File Provider domain | `DuoInput` (set by the host) |
| host app | `Duo Input.app` (Nuitka bundle; embeds this `.appex` in `Contents/PlugIns`) |

Spike identifiers that must **not** be reused: `com.duoinput.DuoEnumSpike*`,
`com.duoinput.DuoNuitka*`, `com.duoinput.FileProviderPasteSpike*`,
`com.duoinput.FileProviderIPCSpike*`.

## Build & test (Task 1 gate)

```bash
cd configurator/fileprovider
xcodegen generate                      # produces DuoInputFileProvider.xcodeproj
xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=4YKVN22BMX
```

Signing uses the free Personal Team identity (Team ID `4YKVN22BMX`). No App Group,
no named Mach service, no temporary-exception entitlement — only `app-sandbox`
(+ `get-task-allow` for dev).

The `xcodeproj`, `build/`, and `DerivedData/` are build artifacts and are gitignored.

## Task 1 scope

Skeleton only: conforms to `NSFileProviderReplicatedExtension` +
`NSFileProviderServicing`, vends an anonymous `NSXPCListener` endpoint via
`DuoServiceSource`. Empty enumeration, unsupported `fetchContents`, no XPC
contract yet. The conformance regression guard lives in
`Tests/ServicingConformanceTests.swift`.
