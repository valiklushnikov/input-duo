# macOS lazy File Provider transfer — Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is an **implementation plan**, not a design document — the architecture is already decided (see Spec). Do **not** re-open closed decisions (§3 of this plan). Do **not** start a new research spike; if you hit a true blocker, record it under `IMPLEMENTATION_BLOCKERS` and stop.

**Goal:** Add lazy Windows→macOS file transfer through a native macOS File Provider extension so that `Ctrl+C` on Windows publishes dataless items and a `file://` clipboard entry, and bytes flow **only** on Finder `Cmd+V` (`fetchContents`), reusing the existing Python `FILE_*` runtime unchanged.

**Architecture:** A thin native Swift `.appex` (`NSFileProviderReplicatedExtension` + `NSFileProviderServicing`) owns an anonymous `NSXPCListener` vended through `NSFileProviderServiceSource`, an extension-private durable metadata replica, enumeration/`item(for:)`, and `fetchContents` (writes bytes to its own temp). The existing Python/PySide6 host is the NSXPC **client**: it discovers the service, exports a callback object, publishes authorized generations into the replica over XPC, and — on `fetchContents` — pulls bytes from the existing `SnapshotRegistry` over the unchanged `files/2` wire and returns them to the extension. Staging remains a per-offer fallback backend of the same receive flow.

**Tech Stack:** Swift 6 / Xcode 27 / `FileProvider.framework` (`.appex`); Python 3.12 (production build) / PySide6 6.10.1 / PyObjC (`pyobjc-framework-Cocoa` + **new** `pyobjc-framework-FileProvider`); an Objective-C XPC protocol shim compiled by clang into a dylib (PyObjC cannot use `objc.formal_protocol` for `NSXPCInterface`); Nuitka 4.1.3 `--standalone --macos-create-app-bundle`; xcodegen for the extension target; `codesign` Personal Team (Team ID `4YKVN22BMX`).

**Spec:** `docs/superpowers/specs/2026-09-19-macos-file-provider-production-design.md` (`DESIGN_STATUS = READY`, `RESEARCH_PHASE = COMPLETE`, `NATIVE_FP_IPC_GATE = PASS`). Supporting records: `docs/superpowers/records/2026-09-19-macos-file-provider-ipc-packaging-spike.md` (Phase 10.1/10.2), `docs/superpowers/records/2026-09-17-macos-lazy-file-provider-spike.md` (Phase 9/9.6). Spike code (reference-only, **do not copy blindly**): `scratchpad/FileProviderPasteSpike/`, `scratchpad/DuoNuitkaHost/`, `scratchpad/pyobjc_xpc_selftest.py`, `scratchpad/libduoproto.dylib`.

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec/code.

- **Wire frozen:** `files/2` unchanged. `PROTOCOL_MAJOR = 1`, `PROTOCOL_MINOR = 1`. `MessageType` FILE_* = `{FILE_OFFER=10, TRANSFER_BEGIN=11, FILE_READ=12, FILE_CHUNK=13, FILE_ERROR=14, TRANSFER_END=15}` only. **No `FILE_CANCEL`.** `FILES2_PROTOCOL_CHANGE_REQUIRED = NO`.
- **Chunk ceiling:** `MAX_FILE_CHUNK_BYTES = 1_048_576` (1 MiB). Do not change.
- **Sender frozen:** Windows sender + `transfer/service.py::FileTransferService._answer_read` + `transfer/source.py::SnapshotRegistry` (`RETENTION = 4`) are **not** modified. No `evictItem`/eviction content-policy.
- **Staging preserved:** `transfer/macos_files.py::MacFileReceiver`, `transfer/staging.py`, `transfer/macos_pasteboard.py::arm` stay working. File Provider is a **second** backend of the same receive flow, selected **per offer**, never mid-generation.
- **Packaging invariants:** `APP_GROUP_REQUIRED = NO`. No App Group, no named Mach service, no `temporary-exception` entitlement in production. Host prod entitlements empty (dev: `get-task-allow`); appex: `com.apple.security.app-sandbox = true` (dev: `+ get-task-allow`).
- **IPC invariants:** `IPC_MODEL = NSFileProviderServiceSource`. `XPC_LISTENER_OWNER = Swift extension` (anonymous `NSXPCListener`). `HOST_ROLE = PyObjC NSXPC client + exported callback`. Extension **MUST** conform to `NSFileProviderServicing` (else `getService = nil`). `NSXPCInterface` requires a **clang-compiled** protocol; `objc.formal_protocol` is not usable.
- **Storage invariants:** `MANIFEST_STORAGE = extension-private durable replica` (owner: extension, in its sandbox container; metadata only — no bytes). `TEMP_FILE_OWNER = Swift extension`. Publish is atomic (temp+rename) and generation is `publishable` only **after** XPC ACK.
- **Identity:** `itemIdentifier = f"{transfer_id}:{entry_index}"`; generation container id = `transfer_id`. `itemVersion` deterministic from immutable manifest (`size:mtime_ns`). `capabilities = [.allowsReading, .allowsWriting]`; `fileSystemFlags = [.userReadable, .userWritable]` → mode `0600`, no `uchg`; **no `chmod`/`chflags`**. No POSIX exec bit (manifest carries none).
- **Concurrency:** per-fetch **one** outstanding `FILE_READ`; global `MAX_ACTIVE_FETCHES` (default 4), `MAX_TOTAL_BUFFERED_BYTES` (default 8 MiB) — tuning constants. **Never** reuse the sequential `_cursor/_offset/_read_id` single-state from `MacFileReceiver`.
- **Privacy invariant (File Provider backend):** **No `FILE_READ` before Finder `Cmd+V` / `fetchContents`.** No pasteable URL and no replica publication before authorization (Ask Accept / Auto), and **none for a stale/superseded authorization**. **Staging fallback is intentionally eager** — it downloads after authorization and *before* Finder paste; that is existing, unchanged behavior, **not** a violation. The zero-read invariant is asserted on the **File Provider** path only.
- **Signing:** Personal Team (Team ID `4YKVN22BMX`), automatic signing, hardened runtime. Bundle IDs unique and consistent across host/appex/domain; must not collide with spike builds (`com.duoinput.DuoNuitka`, `com.duoinput.FileProviderPasteSpike`, etc.).

---

## CURRENT_CODE_MAP (read before planning — verified against real code, 2026-09-19)

Integration points, exact symbols:

| File | Symbol | Role for this plan |
|---|---|---|
| `configurator/src/duo_input/clipboard/wire.py` | `MessageType` (10–15), `Message`, `MAX_FILE_CHUNK_BYTES=1_048_576`, `CAPABILITY_FILES="files/2"` | Wire, **unchanged**. Reused by new backend. |
| `configurator/src/duo_input/transfer/model.py` | `TransferManifest{transfer_id, entries, skipped, total_bytes}`, `TransferEntry{path, kind, size, mtime_ns}`, `ENTRY_FILE/ENTRY_DIRECTORY`, `encode_manifest`, `decode_manifest`, `require_transfer_id`, `MAX_TRANSFER_ID_CHARS=64` | Manifest model. Replica payload derives from `encode_manifest`. **Shared/Qt-free — do not add Qt.** |
| `configurator/src/duo_input/transfer/paths.py` | `sanitize_manifest`, `UnsafePath`, `MAX_ENTRIES=65536` | Reused verbatim on offer. |
| `configurator/src/duo_input/transfer/source.py` | `SnapshotRegistry.read(transfer_id, entry_index, offset, length)`, `SourceChanged`, `SourceMissing`, `RETENTION=4` | Sender snapshot, **unchanged**. Answered by `FileTransferService._answer_read`. |
| `configurator/src/duo_input/transfer/service.py` | `FileTransferService` — `offer_received` Signal(manifest), `_answer_read`, `handle_message`, `attach_link`, `set_peer_capabilities`, `_send`, `_reads: dict[read_id]` | Owns the link + sender role. On darwin its receiver role is dormant. New backend mirrors `MacFileReceiver`'s slot. |
| `configurator/src/duo_input/transfer/macos_files.py` | `MacFileReceiver(staging, pasteboard_arm, parent)` — Signals `authorization_needed`, `transfer_started/progress/completed/cancelled/failed`; methods `attach_link`, `set_peer_capabilities`, `handle_offer`, `authorize(bool)`, `handle_message`, `cancel`, `stop`. **Sequential**: single `_cursor/_offset/_read_id`. | Staging receiver. **Interface template** for `FileProviderBackend`. Do **not** reuse its sequential state. |
| `configurator/src/duo_input/transfer/macos_pasteboard.py` | `arm(paths: Sequence[Path|str]) -> int` (host-only `writeObjects`), `PasteboardArmError` | Reused for FP URLs (Task 8). Currently rebuilds `NSURL` from a path string. |
| `configurator/src/duo_input/transfer/staging.py` | `StagingArea{has_room_for, begin, recover, gc}`, `StagingSession{write, finish, abort}`, TTL `86_400`, budget `8*2**30` | Fallback + GC idiom source. |
| `configurator/src/duo_input/transfer/platform_files.py` | `create_file_backend(parent)`; darwin branch builds `StagingArea` + `MacFileReceiver`; `UnsupportedPlatformError` | **Backend selection seam** — extended for per-offer FP vs staging (Task 16). |
| `configurator/src/duo_input/app.py` | `_ClipboardRuntime._start_files` (lines ~378–518), `_on_file_authorization_needed`, `_attach_file_link`, `_prompt_file_authorization`, `_file_receiver`; settings keys `clipboard/files_enabled`, `clipboard/incoming_files ∈ {ask,auto}` | Wiring: `transfer.offer_received → receiver.handle_offer`; `receiver.authorization_needed → _on_file_authorization_needed`; `link.message_received → receiver.handle_message`. New backend wires here behind a flag. |
| `configurator/src/duo_input/clipboard/coordinator.py` | `.link -> PeerLink|None`, `.peer_capabilities -> frozenset`, `capabilities_known` Signal(object) | Link + capabilities source. |
| `configurator/src/duo_input/clipboard/peer.py` | `PeerLink` — `message_received` Signal(Message), `disconnected` Signal(str), `send(Message) -> bool` | Transport. |
| `configurator/packaging/nuitka-build-macos.sh` | Nuitka invocation; `--include-module=duo_input.transfer.macos_*`, `--nofollow-import-to` windows; produces **unsigned** `dist/DuoInput.app` | Extended for FP modules, shim dylib, `.appex` embed, signing (Task 18). |
| `configurator/requirements-build.txt` | `PySide6==6.10.1`, `cryptography==46.0.3`, `Nuitka==4.1.3`, `pyobjc-framework-Cocoa==12.2.2; sys_platform=="darwin"` | **`pyobjc-framework-FileProvider` missing — add it (Task 3/18).** |
| `configurator/tests/transfer/` | `test_macos_receiver.py`, `test_staging.py`, `test_privacy.py`, `test_boundary_shared_modules_stay_qt_free.py`, `conftest.py` | Test patterns to mirror. |

**Test-facts:** `test_macos_receiver.py` drives the receiver with a fake link + captured messages; `test_boundary_shared_modules_stay_qt_free.py` enforces `SHARED_MODULES = [duo_input.transfer.model, .paths, ...]` import no Qt/Windows — new **backend** is Qt (platform-specific, allowed), new **shared payload builder** (if any) must stay Qt-free.

### Divergences from the design (plan against real code)

1. **Python version:** production build is **3.12** (`pyproject` `>=3.12,<3.13`, `nuitka-build-macos.sh` forces `python3.12`, Nuitka `4.1.3`). Phase 10.2 proved the mechanism on **3.14 / Nuitka 4.2.1** in `.venv-mac`. → **Decision needed in Task 3/18:** either (a) keep 3.12 and re-verify PyObjC FileProvider + clang-dylib XPC on 3.12, or (b) bump the prod build venv to 3.14. Default: **(a) keep 3.12**, add a Task-3 verification gate. Recorded as risk R2.
2. **`pyobjc-framework-FileProvider` absent** from build reqs; only Cocoa is present. `NSFileProviderManager.addDomain`/`getServiceWithName` need it. → add in Task 3/18.
3. **No Swift/Xcode target in-repo.** Everything native lives only in `scratchpad/` spikes. → Task 1 creates `configurator/fileprovider/` target.
4. **Bundle is unsigned ad-hoc** today. Phase 10.2 requires Personal-Team signing, nested-first. → Task 18 adds `xcodebuild` + embed + `codesign`.
5. **`arm()` takes paths, rebuilds `NSURL`.** FP user-visible URLs are `file://` under `~/Library/CloudStorage/…`; passing their `.path` works, but Task 8 adds an explicit `arm_urls(urls)` entry to avoid lossy round-tripping.

---

## File Structure (new + modified)

**Native extension target — new dir `configurator/fileprovider/`:**
- `project.yml` — xcodegen project (host-less: builds only the `.appex`; embedded into the Nuitka `.app` by the build script).
- `Shared/DuoFPProto.h` — Objective-C `@protocol` declarations (`DuoHostCallback`, `DuoExtensionControl`) — the shared XPC contract.
- `Shared/DuoFPProto.m` — `@protocol` anchor + a `DuoFPProtoAnchor` symbol so the dylib exports it.
- `Shared/DuoFPErrors.h` — error domain + `NS_ERROR_ENUM` codes shared by both sides.
- `Extension/FileProviderExtension.swift` — `class FileProviderExtension: NSObject, NSFileProviderReplicatedExtension, NSFileProviderServicing`.
- `Extension/DuoServiceSource.swift` — `NSFileProviderServiceSource` + anonymous `NSXPCListener` + `listener:shouldAcceptNewConnection:` peer-auth.
- `Extension/ReplicaStore.swift` — extension-private durable metadata replica (read/write/atomic/retire/recover/quarantine).
- `Extension/ItemModel.swift` — `struct DuoItem: NSFileProviderItem` mapping.
- `Extension/Enumerator.swift` — `NSFileProviderEnumerator` from the replica.
- `Extension/FetchController.swift` — `fetchContents`, temp file, `Progress`, cancel; drives XPC `openFetch/pullChunk/cancelFetch`.
- `Extension/Info.plist`, `Extension/Extension.entitlements`.
- `Tests/…Tests.swift` — XCTest bundles (item model, enumerator, replica store, XPC interface, error mapping).

**Python — new modules under `configurator/src/duo_input/transfer/`:**
- `fileprovider_proto.py` — loads the clang dylib, exposes `host_interface()`/`extension_interface()` (`NSXPCInterface`). Darwin-only.
- `fileprovider_client.py` — `FileProviderServiceClient` (PyObjC): discovery, connection, `exportedObject`, invalidation/reconnect, Qt-thread marshalling. Darwin-only.
- `fileprovider_domain.py` — `FileProviderDomainManager` (PyObjC): add/remove domain, readiness state machine. Darwin-only.
- `fileprovider_backend.py` — `FileProviderBackend` (Qt `QObject`): concurrency-safe producer; mirrors `MacFileReceiver` signal/link interface.
- `fileprovider_replica.py` — `build_generation_record(manifest) -> bytes` (Qt-free; the `publishGeneration` payload). Shared-ish, stays Qt-free.

**Python — modified:**
- `transfer/platform_files.py` — per-offer backend selection + FP availability probe.
- `transfer/macos_pasteboard.py` — add `arm_urls(urls)`.
- `app.py` — wire `FileProviderBackend` behind `clipboard/fileprovider_enabled` flag; fallback to `MacFileReceiver`.

**Python tests — new under `configurator/tests/transfer/`:**
- `test_fileprovider_backend.py`, `test_fileprovider_scheduler.py`, `test_fileprovider_cancel.py`, `test_fileprovider_client.py`, `test_fileprovider_domain.py`, `test_fileprovider_replica.py`, `test_fileprovider_privacy.py`, `test_fileprovider_backend_selection.py`, `test_fileprovider_errors.py`.
- Shared golden fixture `tests/transfer/fixtures/generation_record.json` (contract vector consumed by both Python and Swift tests).

**Packaging — modified:** `configurator/packaging/nuitka-build-macos.sh`, `configurator/requirements-build.txt`, `configurator/tests/packaging/test_dist.py`.

---

## Test taxonomy & tags

Every test/gate below is tagged:
- **Unit** — pure Python or pure Swift logic, no macOS services.
- **Contract** — XPC interface construction, serialization, identity/version determinism (golden vectors).
- **Integration** — Python ↔ extension over real XPC, or `FILE_*` backend against a fake link/sender.
- **System** — real File Provider / Finder on macOS.
- **E2E** — Windows sender ↔ macOS receiver, two machines.

Runner tags: `AUTOMATABLE` (headless CI-able), `MANUAL` (human-in-the-loop), `CLEAN_MACHINE_REQUIRED` (must run on a fresh macOS user/machine — polluted dev `fileproviderd` is out of scope; see R1).

**Do not unit-test macOS-proven behavior** (Finder laziness, real domain enablement, XPC sandbox boundary, codesign, `NSFileProviderServicing` routing). These are proven by Phase 10.2 or verified only by System/E2E gates. Unit tests verify **our** logic.

---

## Tasks

### Task 1: Native File Provider extension target (skeleton, no streaming)

**Goal:** A production Swift `.appex` that builds, embeds, code-signs on Personal Team, conforms to `NSFileProviderReplicatedExtension` + `NSFileProviderServicing`, and vends an anonymous `NSXPCListener` through `NSFileProviderServiceSource` — with stub enumeration and no remote streaming yet.

**Files:**
- Create: `configurator/fileprovider/project.yml`
- Create: `configurator/fileprovider/Extension/FileProviderExtension.swift`
- Create: `configurator/fileprovider/Extension/DuoServiceSource.swift`
- Create: `configurator/fileprovider/Extension/Info.plist`
- Create: `configurator/fileprovider/Extension/Extension.entitlements`
- Create: `configurator/fileprovider/Tests/ServicingConformanceTests.swift`
- Create: `configurator/fileprovider/README.md` (build/run notes, bundle IDs)

**Interfaces:**
- Produces: bundle id `com.duoinput.configurator.fileprovider` (unique; not a spike id). Extension principal class `FileProviderExtension`. Service name constant `DuoFPServiceName = "com.duoinput.configurator.fileprovider.xpc"` (an NSXPC service *name* label for `getServiceWithName:`, not a Mach service registration).
- Consumes: nothing (root of the native tree).

- [ ] **Step 1: Write the failing conformance test.** `ServicingConformanceTests.swift`:
```swift
import XCTest
import FileProvider
@testable import DuoInputFileProvider

final class ServicingConformanceTests: XCTestCase {
    func testExtensionConformsToServicing() {
        XCTAssertTrue((FileProviderExtension.self as Any) is NSFileProviderServicing.Type)
    }
    func testExtensionConformsToReplicated() {
        XCTAssertTrue((FileProviderExtension.self as Any) is NSFileProviderReplicatedExtension.Type)
    }
}
```
- [ ] **Step 2: Generate the project and run the test — expect FAIL (target/type missing).** Run: `cd configurator/fileprovider && xcodegen generate && xcodebuild test -scheme DuoInputFileProviderTests -destination 'platform=macOS'`. Expected: compile failure (`FileProviderExtension` undefined).
- [ ] **Step 3: Implement the minimal extension.** `FileProviderExtension.swift`: declare `class FileProviderExtension: NSObject, NSFileProviderReplicatedExtension, NSFileProviderServicing`. Implement the required `init(domain:)`, `invalidate()`, `item(for:request:completionHandler:)` (return a stub root item), `fetchContents(...)` (return `NSFeatureUnsupportedError` placeholder for now), `enumerator(for:request:)` (empty enumerator), and `supportedServiceSources(for:completionHandler:)` returning `[DuoServiceSource(...)]`. `DuoServiceSource.swift`: `NSFileProviderServiceSource` with `serviceName`, `makeListenerEndpoint...` returning an anonymous `NSXPCListener` endpoint, and `listener(_:shouldAcceptNewConnection:)` returning `true` (peer-auth added in Task 3/deferred). `Info.plist`: `NSExtensionPointIdentifier = com.apple.fileprovider-nonui`, principal class. `Extension.entitlements`: `com.apple.security.app-sandbox = true` + (dev) `get-task-allow`.
- [ ] **Step 4: Run the test — expect PASS.** Run: `xcodebuild test -scheme DuoInputFileProviderTests -destination 'platform=macOS'`. Expected: PASS.
- [ ] **Step 5: Build + embed + sign smoke gate (System, MANUAL).** Build the `.appex`, embed into a throwaway copy of `dist/DuoInput.app/Contents/PlugIns/`, `codesign` nested-first with Team `4YKVN22BMX`, then `codesign --verify --deep --strict`. Confirm `pluginkit -m -A | grep com.duoinput.configurator.fileprovider` lists it. Record output in the commit message. (No domain add yet.)
- [ ] **Step 6: Commit.**
```bash
git add configurator/fileprovider
git commit -m "feat(fileprovider): native appex skeleton conforming NSFileProviderServicing"
```

**Expected observable result:** `.appex` builds, XCTest green, embedded + signed + `pluginkit`-registered; regression guard fails loudly if `NSFileProviderServicing` conformance is ever removed.

**Failure/rollback condition:** If `codesign --verify --deep --strict` fails on Personal Team, stop and record under `IMPLEMENTATION_BLOCKERS` (contradicts Phase 10.2 `CODESIGN_VERIFY = PASS`). Rollback = delete `configurator/fileprovider/`.

**Dependencies:** none.

---

### Task 2: Shared native XPC protocol (clang shim + interfaces)

**Goal:** Define the production XPC contract once in Objective-C (so `NSXPCInterface` accepts it on both Swift and PyObjC), compile it to a dylib, and prove both sides can construct the interface and encode/decode the payload types.

**Files:**
- Create: `configurator/fileprovider/Shared/DuoFPProto.h`
- Create: `configurator/fileprovider/Shared/DuoFPProto.m`
- Create: `configurator/fileprovider/Shared/DuoFPErrors.h`
- Create: `configurator/fileprovider/Tests/XPCInterfaceTests.swift`
- Modify: `configurator/fileprovider/project.yml` (add a `libduofpproto.dylib` target + `Shared/` to the extension target's sources)
- Create: `configurator/tests/transfer/test_fileprovider_proto.py`
- Create: `configurator/src/duo_input/transfer/fileprovider_proto.py`

**Interfaces:**
- Produces (the XPC contract — explicit NSXPC secure-coding types, **not** ad-hoc JSON-in-Data except where noted):
```objc
// DuoHostCallback — implemented by the HOST (Python), called BY the extension.
@protocol DuoHostCallback <NSObject>
- (void)openFetch:(NSString *)generationId
          entryId:(NSNumber *)entryIndex          // wire entry_index
            reply:(void (^)(NSString * _Nullable fetchToken,
                            NSNumber * _Nullable totalSize,
                            NSError * _Nullable error))reply;
- (void)pullChunk:(NSString *)fetchToken
            reply:(void (^)(NSData * _Nullable chunk,
                            BOOL eof,
                            NSError * _Nullable error))reply;
- (void)cancelFetch:(NSString *)fetchToken;
@end

// DuoExtensionControl — implemented by the EXTENSION, called BY the host.
@protocol DuoExtensionControl <NSObject>
- (void)publishGeneration:(NSData *)recordJSON     // canonical generation record (see Task 4)
                    reply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
- (void)retireGeneration:(NSString *)generationId  // persist state="retired"; record is NOT removed
                    reply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
- (void)deleteGeneration:(NSString *)generationId  // GC only: permanently remove the replica record
                    reply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
@end
```
Retire and delete are **distinct** operations: `retireGeneration` flips durable state to `"retired"` (the record stays enumerable/servable while in use); `deleteGeneration` is the GC step that permanently removes the record. This split is fixed in the contract now (before Tasks 4/15) so the semantics are never ambiguous.
  `recordJSON` is `NSData` (UTF-8 canonical JSON) **by explicit choice**: the metadata tree is already JSON (`encode_manifest`), the extension re-validates on receipt, and a whitelisted struct type would duplicate `model.py`. All scalars use `NSString`/`NSNumber`; bytes use `NSData`; failures use `NSError` in domain `DuoFPErrorDomain` (`DuoFPErrors.h`). Reply-block arg classes are whitelisted via `setClasses:forSelector:argumentIndex:ofReply:` in the interface builders.
- Consumes: Task 1 target.

- [ ] **Step 1: Write the failing Swift interface test.** `XPCInterfaceTests.swift`:
```swift
func testHostInterfaceBuilds() {
    let iface = DuoXPC.hostCallbackInterface()   // NSXPCInterface(with: DuoHostCallback.self) + class whitelists
    XCTAssertNotNil(iface)
}
func testExtensionInterfaceBuilds() {
    XCTAssertNotNil(DuoXPC.extensionControlInterface())
}
```
- [ ] **Step 2: Run — expect FAIL.** Run: `xcodebuild test -scheme DuoInputFileProviderTests -destination 'platform=macOS'`. Expected: `DuoXPC` / `DuoHostCallback` undefined.
- [ ] **Step 3: Implement the Objective-C protocol + Swift builders.** Write `DuoFPProto.h/.m` with the two `@protocol`s and a `DuoFPProtoAnchor` exported symbol (referencing `@protocol(DuoHostCallback)` and `@protocol(DuoExtensionControl)` so they survive dead-strip). `DuoFPErrors.h`: `extern NSErrorDomain const DuoFPErrorDomain;` + `NS_ERROR_ENUM(DuoFPErrorDomain, DuoFPError){ DuoFPErrorSourceMissing=1, ...SourceChanged, ...PeerLost, ...Unauthorized, ...Timeout, ...DiskFull, ...Protocol, ...NotConnected }` (mirrors `error mapping` §16 of the spec). Add a `DuoXPC` Swift enum with `hostCallbackInterface()`/`extensionControlInterface()` that build `NSXPCInterface(with:)` and set reply-arg class whitelists. Add a `libduofpproto.dylib` target in `project.yml` that compiles `Shared/*.m`.
- [ ] **Step 4: Run — expect PASS.** Same command. Expected: PASS.
- [ ] **Step 5: Write the failing Python proto-loader test.** `test_fileprovider_proto.py`:
```python
import sys, pytest
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")

def test_host_interface_constructs_from_clang_protocol():
    from duo_input.transfer import fileprovider_proto as p
    iface = p.host_interface()          # NSXPCInterface from objc.protocolNamed("DuoHostCallback")
    assert iface is not None

def test_formal_protocol_is_not_used(monkeypatch):
    # Guard: we must load the clang dylib, never objc.formal_protocol (Phase 10.2 OBSERVED).
    import duo_input.transfer.fileprovider_proto as p
    assert "formal_protocol" not in p.__dict__ and getattr(p, "USES_CLANG_PROTOCOL", False)
```
- [ ] **Step 6: Run — expect FAIL** (`fileprovider_proto` missing). Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_proto.py -q`.
- [ ] **Step 7: Implement `fileprovider_proto.py`.** `ctypes.CDLL(<path to libduofpproto.dylib>)` to load the protocol symbols, then `objc.protocolNamed("DuoHostCallback")` / `"DuoExtensionControl"`, build `NSXPCInterface.interfaceWithProtocol_`, set reply-arg class whitelists to `{NSString, NSNumber, NSData, NSError}`. Set `USES_CLANG_PROTOCOL = True`. Resolve dylib path relative to the bundle (`Contents/Frameworks/` in prod, build dir in dev).
- [ ] **Step 8: Run — expect PASS.** Same command. Expected: 2 passed.
- [ ] **Step 9: Contract round-trip smoke (Contract, AUTOMATABLE).** Add `test_generation_record_vector_shape` asserting the canonical record JSON schema fields exist (fed to Task 4). Commit the schema doc as a comment in `DuoFPProto.h`.
- [ ] **Step 10: Commit.**
```bash
git add configurator/fileprovider/Shared configurator/fileprovider/Tests/XPCInterfaceTests.swift \
        configurator/fileprovider/project.yml configurator/src/duo_input/transfer/fileprovider_proto.py \
        configurator/tests/transfer/test_fileprovider_proto.py
git commit -m "feat(fileprovider): shared clang XPC protocol + interface builders (Swift+PyObjC)"
```

**Expected observable result:** Both languages construct `NSXPCInterface` from the same clang protocol; the version/`formal_protocol` guard is locked in.

**Failure/rollback condition:** If `objc.protocolNamed` fails to find the symbol, the dylib isn't loaded/anchored — fix the anchor/`RTLD` load, do **not** fall back to `formal_protocol`. If unresolvable on Python 3.12, record R2 blocker.

**Dependencies:** Task 1.

---

### Task 3: Python `FileProviderServiceClient` (transport only)

**Goal:** A production PyObjC NSXPC **client** that discovers the File Provider service for the domain, establishes a connection, exports the host callback object, survives invalidation/interruption with reconnect, and marshals extension→host callbacks onto the Qt thread — with **no** `FILE_*` backend logic inside it.

**Files:**
- Create: `configurator/src/duo_input/transfer/fileprovider_client.py`
- Create: `configurator/tests/transfer/test_fileprovider_client.py`
- Modify: `configurator/requirements-build.txt` (add `pyobjc-framework-FileProvider==12.2.2; sys_platform == "darwin"`)
- Modify: `configurator/tests/transfer/conftest.py` (add a fake-connection fixture)

**Interfaces:**
- Produces: `class FileProviderServiceClient(QObject)` with:
  - signals `connected = Signal()`, `disconnected = Signal(str)`
  - `set_domain(domain_identifier: str) -> None`
  - `connect_service() -> None` (idempotent; discovery + connect)
  - `set_callbacks(open_fetch, pull_chunk, cancel_fetch) -> None` — the host-exported object methods (Qt-thread callables); wired to `DuoHostCallback`.
  - `remote() -> object | None` — `remoteObjectProxy` typed to `DuoExtensionControl` (used by Task 7 to `publishGeneration`).
  - `is_connected -> bool`
- Consumes: `fileprovider_proto.host_interface()`/`extension_interface()` (Task 2).

- [ ] **Step 1: Write the failing lifecycle test (mocked connection).** `test_fileprovider_client.py`:
```python
import sys, pytest
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")

def test_invalidation_emits_disconnected_once(fp_fake_service):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient
    client = FileProviderServiceClient()
    seen = []
    client.disconnected.connect(seen.append)
    client.set_domain("DuoInput")
    fp_fake_service.grant_connection(client)     # inject a fake NSXPCConnection
    fp_fake_service.invalidate()
    fp_fake_service.invalidate()                 # second invalidation is a no-op
    assert len(seen) == 1

def test_extension_callback_marshals_to_qt_thread(fp_fake_service):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient
    client = FileProviderServiceClient()
    calls = []
    client.set_callbacks(open_fetch=lambda *a: calls.append(("open", *a)),
                         pull_chunk=lambda *a: calls.append(("pull", *a)),
                         cancel_fetch=lambda *a: calls.append(("cancel", *a)))
    fp_fake_service.grant_connection(client)
    fp_fake_service.simulate_extension_call("open", "abc123", 0)
    assert calls == [("open", "abc123", 0)]
```
- [ ] **Step 2: Run — expect FAIL** (module missing). Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_client.py -q`.
- [ ] **Step 3: Add the FileProvider dependency + verify import on 3.12.** Add the requirement line; in `.venv-mac` (or a fresh 3.12 venv) `pip install pyobjc-framework-FileProvider==12.2.2` and confirm `import FileProvider; FileProvider.NSFileProviderManager` resolves. **If it fails on 3.12, escalate the version decision (Divergence 1 / R2) — do not silently bump.**
- [ ] **Step 4: Implement `FileProviderServiceClient`.** Use `NSFileProviderManager(for: domain)` → `getServiceWithName:itemIdentifier:.rootContainer` (or `NSFileManager.getFileProviderServicesForItemAtURL:` fallback) → `getFileProviderConnection` → `NSXPCConnection`; set `remoteObjectInterface = extension_interface()`, `exportedInterface = host_interface()`, `exportedObject = <PyObjc object wrapping the three callbacks>`; `resume`. Marshal every extension→host callback onto the Qt thread via `QMetaObject.invokeMethod`/a queued signal (mirror `ServiceCallbackGateway` COM→Qt idiom). Handle `invalidationHandler`/`interruptionHandler` → emit `disconnected` once, drop the proxy; `connect_service` re-discovers.
- [ ] **Step 5: Run — expect PASS.** Same command. Expected: 2 passed.
- [ ] **Step 6: Cross-process discovery smoke (Integration, MANUAL, CLEAN_MACHINE_REQUIRED).** Against the Task-1 `.appex` on an enabled domain, confirm `connect_service()` emits `connected` and `remote()` is non-nil. (This is the first real cross-process XPC from production Python; on a polluted `fileproviderd` it may fail with `-2011` — see R1; run on a clean machine.)
- [ ] **Step 7: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_client.py \
        configurator/tests/transfer/test_fileprovider_client.py \
        configurator/tests/transfer/conftest.py configurator/requirements-build.txt
git commit -m "feat(fileprovider): PyObjC NSXPC service client with reconnect + Qt marshalling"
```

**Expected observable result:** Connection lifecycle + callback marshalling verified with a fake; `pyobjc-framework-FileProvider` declared.

**Failure/rollback condition:** Import failure on 3.12 → R2 escalation. Double-`disconnected` → invalidation not idempotent, fix before proceeding.

**Dependencies:** Task 2.

---

### Task 4: Extension-private durable replica (Swift) + shared record contract

**Goal:** The extension owns a crash-safe metadata replica in its sandbox container; `publishGeneration` persists atomically and ACKs **only after** the durable write; retire/startup-recovery/corrupt-quarantine are covered. Metadata only — no bytes.

**Files:**
- Create: `configurator/fileprovider/Extension/ReplicaStore.swift`
- Create: `configurator/fileprovider/Tests/ReplicaStoreTests.swift`
- Create: `configurator/tests/transfer/fixtures/generation_record.json` (shared golden vector)
- Create: `configurator/src/duo_input/transfer/fileprovider_replica.py`
- Create: `configurator/tests/transfer/test_fileprovider_replica.py`

**Interfaces:**
- Produces (serialization decision — **one JSON file per generation**, chosen for crash-safety simplicity over a single index; each write is one atomic temp+rename, no cross-file consistency to maintain):
  - Path: `<containerURL>/Library/Application Support/DuoReplica/generations/<transfer_id>.json`.
  - Record schema v1: `{ "schema": 1, "transfer_id": str, "state": "active|retired", "created_ns": int, "lease_deadline_ns": int, "manifest": <encode_manifest dict> }`.
  - `build_generation_record(manifest, *, state, created_ns, lease_deadline_ns) -> bytes` in `fileprovider_replica.py` (Qt-free).
  - Swift `ReplicaStore`: `publish(recordJSON: Data) throws`, `retire(_ generationId: String) throws` (rewrite the record with `state="retired"`; **record is not removed**), `delete(_ generationId: String) throws` (GC: permanently remove the record file), `record(for: String) -> GenerationRecord?`, `allActive() -> [GenerationRecord]`, `recover()` (quarantines unparseable files into `.../quarantine/`).
- Consumes: `model.encode_manifest` (Python side); Task 2 `publishGeneration` payload.

- [ ] **Step 1: Write the failing Python contract test.** `test_fileprovider_replica.py`:
```python
import json
from pathlib import Path
from duo_input.transfer.model import TransferManifest, TransferEntry
from duo_input.transfer.fileprovider_replica import build_generation_record

def _manifest():
    return TransferManifest("abc123", (TransferEntry("a.txt", "file", 3, 111),))

def test_record_matches_golden_vector():
    rec = json.loads(build_generation_record(_manifest(), state="active",
                                             created_ns=1, lease_deadline_ns=2))
    golden = json.loads(Path("tests/transfer/fixtures/generation_record.json").read_text())
    assert rec == golden

def test_record_is_metadata_only():
    rec = json.loads(build_generation_record(_manifest(), state="active",
                                             created_ns=1, lease_deadline_ns=2))
    assert "manifest" in rec and rec["schema"] == 1
    assert b"bytes" not in json.dumps(rec).encode()  # no content field ever
```
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_replica.py -q`. (`build_generation_record` missing.)
- [ ] **Step 3: Implement `fileprovider_replica.py` (Qt-free) + write the golden fixture.** Build the record dict from `manifest.to_dict()` under `"manifest"`, dump canonical (`sort_keys=True, ensure_ascii=False`) UTF-8 bytes. Write `fixtures/generation_record.json` to match. Add `duo_input.transfer.fileprovider_replica` to the Qt-free boundary test's allow-list only if it stays Qt-free (it must).
- [ ] **Step 4: Run — expect PASS.** Same command. Expected: 2 passed.
- [ ] **Step 5: Write the failing Swift replica test.** `ReplicaStoreTests.swift`: publish the golden vector → `record(for:)` returns it; second publish of same id overwrites atomically; `retire` persists `state="retired"` and `record(for:)` **still returns** the record (not removed) while `allActive()` excludes it; a separate `delete` removes the record file so `record(for:)` returns nil; simulate a half-written file (truncated JSON) → `recover()` quarantines it and `allActive()` skips it; restart (new `ReplicaStore` on same dir) still reads surviving records.
- [ ] **Step 6: Run — expect FAIL, then implement `ReplicaStore.swift`.** Atomic write = write to `…/tmp/<uuid>` then `FileManager.replaceItemAt`. Load the golden fixture from the test bundle. Run `xcodebuild test` — expect PASS.
- [ ] **Step 7: ACK-ordering unit (Swift) for all three control ops.** Add tests that `publishGeneration(reply:)`, `retireGeneration(reply:)`, and `deleteGeneration(reply:)` each call `reply(true, nil)` **only after** the corresponding `ReplicaStore` op returns (no ACK on throw). Implement all three control shims in `FileProviderExtension` (`publish`/`retire`/`delete`) now, so the XPC contract is complete before Tasks 15 uses it — but only `publishGeneration` is exercised end-to-end in Task 7.
- [ ] **Step 8: Commit.**
```bash
git add configurator/fileprovider/Extension/ReplicaStore.swift \
        configurator/fileprovider/Tests/ReplicaStoreTests.swift \
        configurator/tests/transfer/fixtures/generation_record.json \
        configurator/src/duo_input/transfer/fileprovider_replica.py \
        configurator/tests/transfer/test_fileprovider_replica.py
git commit -m "feat(fileprovider): extension-private durable replica + shared record contract"
```

**Expected observable result:** Byte-identical record on both sides (golden vector); atomic publish, retire, restart-recovery, corrupt-quarantine all green; ACK strictly after durable write.

**Failure/rollback condition:** Golden vector mismatch between Python and Swift → freeze the canonical JSON form (sort keys, no trailing spaces) before continuing.

**Dependencies:** Task 2 (payload type), Task 1 (target).

---

### Task 5: Enumerator + `NSFileProviderItem` model from the replica

**Goal:** Reconstruct the domain namespace (root → generation container → manifest tree) from the replica with deterministic identifiers/versions and the exact capabilities/flags that yield `0600` and no `uchg`.

**Files:**
- Create: `configurator/fileprovider/Extension/ItemModel.swift`
- Create: `configurator/fileprovider/Extension/Enumerator.swift`
- Create: `configurator/fileprovider/Tests/ItemModelTests.swift`
- Create: `configurator/fileprovider/Tests/EnumeratorTests.swift`
- Modify: `configurator/fileprovider/Extension/FileProviderExtension.swift` (`item(for:)`, `enumerator(for:)`)

**Interfaces:**
- Produces: `struct DuoItem: NSFileProviderItem` with `itemIdentifier = "<transfer_id>:<entry_index>"`, generation container id `= transfer_id`, `parentItemIdentifier` (ancestor dir's `entry_index` or the generation container or `.rootContainer`), `filename = PurePosixPath(path).name`, `documentSize`, `contentType` (`public.folder` for dir, else by extension / `public.data`), `itemVersion = NSFileProviderItemVersion(contentVersion: "<size>:<mtime_ns>", metadataVersion: "<name>:<size>:<mtime_ns>:<caps_rev>")`, `capabilities = [.allowsReading, .allowsWriting]`, `fileSystemFlags = [.userReadable, .userWritable]`. `caps_rev` constant `= 1`.
- Consumes: `ReplicaStore` (Task 4).

- [ ] **Step 1: Write failing `ItemModelTests`.** Assert: same manifest → identical `itemIdentifier` and `itemVersion` across two constructions (determinism); two entries with the same filename in different dirs get different identifiers and different `parentItemIdentifier`; a file item's `fileSystemFlags` contains `.userReadable|.userWritable` and its `capabilities` contains `.allowsReading|.allowsWriting`; a directory item's `contentType == .folder`.
- [ ] **Step 2: Run `xcodebuild test` — expect FAIL** (`DuoItem` undefined).
- [ ] **Step 3: Implement `ItemModel.swift`.** Pure mapping from a replica `GenerationRecord` + entry index to `DuoItem`; deterministic version strings; parent resolution by walking `PurePosixPath` components against the record's entry set.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Write failing `EnumeratorTests`.** From the golden generation record: root enumeration yields one generation container; container enumeration yields top-level entries; a directory enumeration yields its children; anchor/`enumerateItems` pagination returns the full tree; `item(for:)` on a leaf returns the matching `DuoItem`.
- [ ] **Step 6: Run — expect FAIL, implement `Enumerator.swift` + wire `item(for:)`/`enumerator(for:)`.** Read from `ReplicaStore`; no network, no host. Run `xcodebuild test` — expect PASS.
- [ ] **Step 7: Commit.**
```bash
git add configurator/fileprovider/Extension/ItemModel.swift \
        configurator/fileprovider/Extension/Enumerator.swift \
        configurator/fileprovider/Tests/ItemModelTests.swift \
        configurator/fileprovider/Tests/EnumeratorTests.swift \
        configurator/fileprovider/Extension/FileProviderExtension.swift
git commit -m "feat(fileprovider): enumerator + deterministic item model from replica"
```

**Expected observable result:** Deterministic IDs/versions; correct tree; `0600`/no-`uchg` semantics expressed through API config (never `chmod`/`chflags`). Proven fully from the replica, no host.

**Failure/rollback condition:** Non-deterministic `itemVersion` → OS treats items as changed and re-fetches; must be fixed before Task 10.

**Dependencies:** Task 4.

---

### Task 6: Python `FileProviderDomainManager` + readiness

**Goal:** Production domain lifecycle (`ABSENT → REGISTERING → WAITING_ENABLED → READY`, plus `DEGRADED`/`REMOVING`) with backoff — no blind `sleep` — and a production-safe readiness/fallback that never touches SIP/private state.

**Files:**
- Create: `configurator/src/duo_input/transfer/fileprovider_domain.py`
- Create: `configurator/tests/transfer/test_fileprovider_domain.py`

**Interfaces:**
- Produces: `class FileProviderDomainManager(QObject)` with `state -> DomainState` (StrEnum), signals `state_changed = Signal(str)`, `ready = Signal()`, `degraded = Signal(str)`; methods `ensure_domain() -> None` (add if absent, poll to READY with backoff), `remove_domain() -> None`, `domain_identifier -> str` (`"DuoInput"`), `is_ready -> bool`. Readiness probe = `NSFileProviderManager` state / absence of `-2011` on a probe, not `sleep`.
- Consumes: `pyobjc-framework-FileProvider`.

- [ ] **Step 1: Write failing state-machine test (mocked `NSFileProviderManager`).** Inject a fake manager: `add` succeeds → state goes `REGISTERING → WAITING_ENABLED`; probe returns enabled → `READY` + `ready` emitted once; probe returns `-2011` N times then enabled → backoff retries (assert increasing delays via injected clock, no `time.sleep`) then `READY`; `add` failure → `DEGRADED` + `degraded(reason)`.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_domain.py -q`.
- [ ] **Step 3: Implement `fileprovider_domain.py`.** Backoff schedule reusing the coordinator's `RECONNECT_DELAYS_MS` idiom via `QTimer` (injectable for tests). Never call `killall`/`launchctl`/SIP paths. On persistent `-2011`, transition to `DEGRADED` (Task 16 routes offers to staging) — do **not** attempt to repair `fileproviderd`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Add `VALIDATION_RISK_FP_FRESH_DOMAIN` gate (System, MANUAL, CLEAN_MACHINE_REQUIRED).** A documented manual test `docs/…/validation/fresh-domain.md`: on a clean macOS user, `ensure_domain()` → `add → initial import → root created → enabled` within the enable window (~4 s observed). This gate is **not** run in CI and is **not** a blocker for other tasks.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_domain.py \
        configurator/tests/transfer/test_fileprovider_domain.py
git commit -m "feat(fileprovider): domain manager with backoff readiness, staging on degrade"
```

**Expected observable result:** Deterministic state machine under fakes; no blind sleep; degrade → fallback path exists; fresh-domain risk captured as a clean-machine validation item.

**Failure/rollback condition:** Any code path calling `killall`/`launchctl kickstart`/private-store deletion → reject in review; violates the spec's `VALIDATION_RISK_FP_FRESH_DOMAIN` rule.

**Dependencies:** Task 3 (shares the FileProvider dependency).

---

### Task 7: FILE_OFFER → authorized generation publication (first vertical slice)

**Goal:** On an accepted offer, publish the generation over XPC into the replica (atomic + ACK) so the File Provider namespace becomes visible — while guaranteeing **no** replica, **no** pasteable URL, and **no** `FILE_READ` before authorization. Reuses `sanitize_manifest` and the existing Ask/Auto flow.

**Files:**
- Create: `configurator/src/duo_input/transfer/fileprovider_backend.py` (offer/authorize/publish half only; streaming added in Tasks 9–10)
- Create: `configurator/tests/transfer/test_fileprovider_publish.py`

**Interfaces:**
- Produces: `class FileProviderBackend(QObject)` (initial subset) mirroring `MacFileReceiver`'s interface but with **authorization identity (epoch)** so a stale Accept cannot publish: signals `authorization_needed = Signal(object, int)` (manifest, epoch), `transfer_started/progress/completed/cancelled/failed`; methods `attach_link(link)`, `set_peer_capabilities(caps)`, `handle_offer(manifest) -> int` (bumps `_offer_epoch`, stores the pending `(epoch, manifest)`, returns the epoch), `authorize(accepted: bool, epoch: int)` (**ignores the call when `epoch != _pending_epoch` or no pending — stale/superseded authorization publishes nothing, writes no replica, arms nothing**), `handle_message(msg)`, `cancel()`, `stop()`. Constructor `FileProviderBackend(client: FileProviderServiceClient, domain: FileProviderDomainManager, pasteboard_arm, parent=None)`. New method `_publish_generation(manifest) -> None` calls `client.remote().publishGeneration_reply_(build_generation_record(...), <ack cb>)`. A new `handle_offer` that supersedes a pending `AWAITING_AUTH` offer emits `transfer_cancelled` for the superseded epoch. (App wiring in Task 16 captures the epoch at prompt time and passes it back through `authorize`.)
- Consumes: `FileProviderServiceClient.remote()` (Task 3), `build_generation_record` (Task 4), `fileprovider_domain` (Task 6), `sanitize_manifest`/existing offer path (already applied by `FileTransferService.offer_received`).

- [ ] **Step 1: Write failing publish tests (fake client).** `test_fileprovider_publish.py` (`_manifest(transfer_id="abc123")` builds a manifest with that id):
```python
def test_no_publish_before_accept(fp_backend, fake_remote):
    fp_backend.handle_offer(_manifest())
    assert fake_remote.published == []          # Ask mode, no accept yet
def test_reject_publishes_nothing(fp_backend, fake_remote):
    e = fp_backend.handle_offer(_manifest()); fp_backend.authorize(False, e)
    assert fake_remote.published == []
def test_accept_publishes_generation_and_waits_ack(fp_backend, fake_remote):
    e = fp_backend.handle_offer(_manifest()); fp_backend.authorize(True, e)
    assert fake_remote.published == ["abc123"]  # publishGeneration called
def test_no_arm_before_ack(fp_backend, fake_remote, fake_arm):
    e = fp_backend.handle_offer(_manifest()); fp_backend.authorize(True, e)
    assert fake_arm.calls == []                  # arm only after ACK (Task 8)
def test_no_file_read_on_publish(fp_backend, fake_link):
    e = fp_backend.handle_offer(_manifest()); fp_backend.authorize(True, e)
    assert not any(m.type.name == "FILE_READ" for m in fake_link.sent)
def test_stale_accept_is_ignored(fp_backend, fake_remote, fake_arm, fake_link):
    e_a = fp_backend.handle_offer(_manifest("aaa111"))   # offer A pending
    e_b = fp_backend.handle_offer(_manifest("bbb222"))   # offer B supersedes A
    fp_backend.authorize(True, e_a)                       # late Accept of stale A
    assert fake_remote.published == []                    # NO publishGeneration
    assert fake_arm.calls == []                           # NO clipboard arm
    assert not any(m.type.name == "FILE_READ" for m in fake_link.sent)  # NO read
    fp_backend.authorize(True, e_b)                       # Accept of current B
    assert fake_remote.published == ["bbb222"]            # only the current offer
```
- [ ] **Step 2: Run — expect FAIL** (module missing). Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_publish.py -q`.
- [ ] **Step 3: Implement the offer/authorize/publish half.** `handle_offer` bumps `_offer_epoch`, stores the pending `(epoch, manifest)`, emits `authorization_needed(manifest, epoch)`, returns the epoch; if it supersedes a still-pending `AWAITING_AUTH` offer, emit `transfer_cancelled` for the old epoch. `authorize(accepted, epoch)`: **if there is no pending offer or `epoch != _pending_epoch`, return immediately (stale — publish nothing, no replica, no arm)**. Otherwise `authorize(True, epoch)` builds the record and calls `remote().publishGeneration_reply_`; on ACK mark generation `ACTIVE_CLIPBOARD` and emit an internal `generation_ready(transfer_id, root_ids)` (consumed by Task 8). `authorize(False, epoch)` → clear pending + `transfer_cancelled`. No `FILE_READ`, no `arm` here.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Add supersede + auto-mode tests.** New offer while one is `AWAITING_AUTH` → the superseded epoch emits `transfer_cancelled` and its later Accept publishes/arms nothing (covered by `test_stale_accept_is_ignored`); Auto mode authorizes the **current** epoch and publishes; a re-Accept of an already-consumed epoch is idempotent (no double publish). This **overrides** the spec §22 "soft tradeoff": a stale authorization now publishes **nothing** (no generation, no replica, no arm).
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_publish.py
git commit -m "feat(fileprovider): authorized generation publication with ACK, no early read"
```

**Expected observable result:** Accept publishes; reject/pre-accept publish nothing; no `FILE_READ` or `arm` before ACK.

**Failure/rollback condition:** Any `FILE_READ` or `arm` observed pre-accept, **or any publish/replica/arm for a stale (superseded) authorization epoch** → privacy violation; block.

**Dependencies:** Tasks 3, 4, 6.

---

### Task 8: File Provider URLs → existing pasteboard (privacy regression gate)

**Goal:** After ACK + domain READY, obtain the generation root's user-visible `file://` URLs and arm the existing host-only pasteboard — reusing `macos_pasteboard`, not a second subsystem — and lock in the **copy-without-paste ⇒ zero `FILE_READ`** regression.

**Files:**
- Modify: `configurator/src/duo_input/transfer/macos_pasteboard.py` (add `arm_urls`)
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py` (`_arm_after_ready`)
- Create: `configurator/tests/transfer/test_fileprovider_privacy.py`
- Create: `configurator/tests/transfer/test_macos_pasteboard_urls.py`

**Interfaces:**
- Produces: `macos_pasteboard.arm_urls(urls: Sequence[str | NSURL]) -> int` (host-only `writeObjects`, mirrors `arm`). `FileProviderBackend._arm_after_ready(transfer_id)` calls `NSFileProviderManager.getUserVisibleURLForItemIdentifier:` for each root and passes them to the injected arm.
- Consumes: Task 7 `generation_ready`, Task 6 readiness.

- [ ] **Step 1: Write the failing privacy regression test.** `test_fileprovider_privacy.py`:
```python
def test_arm_then_wait_sends_no_file_read(fp_backend, fake_link, fake_arm, qtbot):
    e = fp_backend.handle_offer(_manifest()); fp_backend.authorize(True, e)
    fp_backend.on_ack("abc123")                 # simulate publish ACK
    fp_backend.on_domain_ready()                # simulate READY
    qtbot.wait(200)
    assert fake_arm.calls == [["abc123-root"]]  # armed
    assert not any(m.type.name == "FILE_READ" for m in fake_link.sent)  # NO read
```
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_privacy.py -q`.
- [ ] **Step 3: Implement `arm_urls` + `_arm_after_ready`.** `arm_urls` prepares host-only contents and `writeObjects_` the URLs. Backend arms only when both ACK and READY hold; if not READY within the window, defer (Task 16 handles the fallback).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Add `test_macos_pasteboard_urls.py` (unit).** `arm_urls` returns an int changeCount; empty list raises/returns 0 per `arm` parity.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/macos_pasteboard.py \
        configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_privacy.py \
        configurator/tests/transfer/test_macos_pasteboard_urls.py
git commit -m "feat(fileprovider): arm FP user-visible URLs via existing pasteboard; privacy gate"
```

**Expected observable result:** Clipboard armed with FP URLs; automated proof that copy-without-paste issues zero `FILE_READ`.

**Failure/rollback condition:** Any `FILE_READ` after arm-and-wait → block (core product invariant).

**Dependencies:** Task 7.

---

### Task 9: Concurrent `FileProviderBackend` scheduler skeleton (fake chunks)

**Goal:** The concurrency-safe producer core: per-fetch independent state, `by_token`/`by_read_id` indexes, `MAX_ACTIVE_FETCHES` scheduler with a queue, one outstanding `FILE_READ` per fetch — testable with fake chunks, **never** the sequential `MacFileReceiver` state.

**Files:**
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py` (add the fetch scheduler)
- Create: `configurator/tests/transfer/test_fileprovider_scheduler.py`

**Interfaces:**
- Produces: `@dataclass Fetch{fetch_token: str, generation_id: str, entry_index: int, offset: int, read_id: int|None, expected: int, size: int, state: FetchState}`; `FetchState` StrEnum (`QUEUED/REQUESTING/RECEIVING/DONE/CANCELLED/FAILED`). Backend methods `open_fetch(generation_id, entry_index) -> (fetch_token, total_size)`, `pull_chunk(fetch_token) -> None` (emits at most one `FILE_READ`), `_admit_from_queue()`, `_on_chunk(msg)`, `_on_file_error(msg)`. Constants `MAX_ACTIVE_FETCHES = 4`, `MAX_TOTAL_BUFFERED_BYTES = 8 * 1024 * 1024`.
- Consumes: manifest of the ACTIVE generation (Task 7).

- [ ] **Step 1: Write failing scheduler tests.** Assert: opening 6 fetches keeps ≤4 in `REQUESTING/RECEIVING`, 2 `QUEUED`; completing one admits one from the queue (FIFO fairness); each fetch has independent `offset`/`read_id`/`expected`; a `pull_chunk` sends exactly one `FILE_READ` with the fetch's own `read_id`; assert there is **no** module-level/global `_cursor`/`_offset`/`_read_id` (introspect the class).
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_scheduler.py -q`.
- [ ] **Step 3: Implement the scheduler.** `by_token: dict[str, Fetch]`, `by_read_id: dict[int, Fetch]`, `_active: set[str]`, `_queue: deque[str]`, `_read_ids = itertools.count(1)`. `open_fetch` validates against the ACTIVE manifest (like `FileTransferService.open_pipe`), creates a `Fetch`, admits or queues. `pull_chunk` sends one `FILE_READ` if a slot is free and none outstanding for that fetch.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_scheduler.py
git commit -m "feat(fileprovider): concurrency-safe fetch scheduler (per-fetch state)"
```

**Expected observable result:** Bounded concurrency, FIFO queue, independent per-fetch state, one read in flight per fetch — proven with fakes.

**Failure/rollback condition:** Any shared mutable cursor across fetches → block (spec §17).

**Dependencies:** Task 7.

---

### Task 10: Real `openFetch → FILE_READ → FILE_CHUNK → temp → completion` (FIRST REAL LAZY PASTE)

**Goal:** Wire the full single-file path end to end — Swift `fetchContents` → XPC `openFetch`/`pullChunk` → Python backend → `FILE_READ` on the unchanged wire → Windows `SnapshotRegistry` → `FILE_CHUNK` → XPC chunk → Swift temp → completion — for **one** regular file, and prove `FILE_READ` happens strictly after `fetchContents`.

**Files:**
- Modify: `configurator/fileprovider/Extension/FetchController.swift`
- Modify: `configurator/fileprovider/Extension/FileProviderExtension.swift` (`fetchContents`)
- Modify: `configurator/src/duo_input/transfer/fileprovider_client.py` (route `open_fetch`/`pull_chunk`/`cancel_fetch` callbacks to the backend)
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py` (chunk → reply plumbing)
- Create: `configurator/tests/transfer/test_fileprovider_end_to_end.py` (Integration, fake sender)
- Create: `configurator/fileprovider/Tests/FetchControllerTests.swift`

**Interfaces:**
- Produces: Swift `FetchController.fetch(item, request, completion)` → calls host `openFetch` → `pullChunk` loop → writes chunks to a temp URL in the extension container → `completion(tempURL, item, nil)`. Python `open_fetch(generation_id, entry_index, reply)` returns `(fetch_token, total_size)`; `pull_chunk(fetch_token, reply)` triggers one `FILE_READ` and, on the matching `FILE_CHUNK`, calls `reply(chunk, eof, None)`.
- Consumes: Tasks 3, 5, 9; `FileTransferService._answer_read` (unchanged sender) via the real link, or a fake sender in the Integration test.

- [ ] **Step 1: Write the failing Integration E2E test (fake sender over a loopback link).** `test_fileprovider_end_to_end.py`: build a fake link where the sender side is the real `FileTransferService` with a `SnapshotRegistry.publish` of a 3-byte file; drive the backend's `open_fetch`+`pull_chunk`; assert the reassembled bytes equal the source, `eof` after `size`, and — critically — record timestamps: `first_FILE_READ_ts > fetchContents_ts`.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_end_to_end.py -q`.
- [ ] **Step 3: Implement the Python chunk→reply plumbing.** In the backend, hold the pending `reply` block per fetch; on `_on_chunk` matching `(read_id, transfer_id, entry_index, offset)` (reuse `FileTransferService._pending_reply` validation shape), call `reply(bytes, eof=offset+len>=size, None)`; advance `offset`; on truncation/oversize → `reply(None, False, DuoFPError.Protocol)`. Route `client` callbacks to backend methods.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Implement Swift `FetchController` + `FetchControllerTests` (temp write, completion).** Unit-test with a stubbed host proxy returning canned chunks: assert temp file has exact bytes, `completion` called with the temp URL, temp removed on error. Run `xcodebuild test` — expect PASS.
- [ ] **Step 6: First real lazy paste (System + E2E, MANUAL, CLEAN_MACHINE_REQUIRED).** Windows `Ctrl+C` a single small file → Mac shows dataless item + armed clipboard → Finder `Cmd+V` → destination bytes correct; capture logs proving `FETCH_ENTER` precedes the first `FILE_READ`. Record in the commit.
- [ ] **Step 7: Commit.**
```bash
git add configurator/fileprovider/Extension/FetchController.swift \
        configurator/fileprovider/Extension/FileProviderExtension.swift \
        configurator/fileprovider/Tests/FetchControllerTests.swift \
        configurator/src/duo_input/transfer/fileprovider_client.py \
        configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_end_to_end.py
git commit -m "feat(fileprovider): first real lazy single-file paste end-to-end"
```

**Expected observable result:** A single file pastes lazily; `FILE_READ` strictly after `fetchContents`. **This is the first point where production lazy transfer really works.**

**Failure/rollback condition:** Bytes wrong / `FILE_READ` before `fetchContents` → block. If real cross-process `fetchContents` fails only on a polluted `fileproviderd` (`-2011`), re-run on a clean machine (R1); do not "fix" `fileproviderd`.

**Dependencies:** Tasks 3, 5, 8, 9. (Task 8 — real File Provider user-visible URLs on the pasteboard — is required for a genuine Finder `Cmd+V` end-to-end.)

---

### Task 11: Progress + streaming/backpressure

**Goal:** Add `NSProgress` accounting, one-outstanding-chunk streaming with a bounded memory footprint, `fsync`/close, and EOF — verified across sizes and error inputs.

**Files:**
- Modify: `configurator/fileprovider/Extension/FetchController.swift`
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py`
- Create: `configurator/tests/transfer/test_fileprovider_streaming.py`
- Modify: `configurator/fileprovider/Tests/FetchControllerTests.swift`

**Interfaces:**
- Produces: Swift `Progress(totalUnitCount: size)`, `completedUnitCount += chunk.count`, returned to Finder from `fetchContents`; temp `FileHandle` with one write per chunk, `synchronize()` + `close()` at EOF. Python enforces `MAX_TOTAL_BUFFERED_BYTES` across active fetches (queue a `pull_chunk` when over budget).
- Consumes: Task 10.

- [ ] **Step 1: Write failing streaming tests (Python).** Multiple chunks reassemble; **zero-byte file: `fetchContents`/`openFetch` is allowed, `FILE_READ` count == 0, an empty temp is returned, and completion succeeds** (EOF immediately, never a read); exactly-1-MiB file = one full chunk then EOF; >1 MiB = multiple 1-MiB reads at increasing offsets; oversized reply → `Protocol` error; truncated reply → `Protocol` error (reuse `MacFileReceiver` truncation semantics); disk-write error surfaced as `DiskFull`/`Protocol`.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_streaming.py -q`.
- [ ] **Step 3: Implement bounded streaming + Swift Progress/fsync.** Enforce ≤1 outstanding read/fetch and the global byte budget; Swift accumulates Progress and fsyncs at EOF. Zero-byte: complete without any read.
- [ ] **Step 4: Run — expect PASS** (Python) and `xcodebuild test` (Swift) — expect PASS.
- [ ] **Step 5: Large-file System gate (MANUAL, CLEAN_MACHINE_REQUIRED).** Paste a ~100 MB and a multi-GB file; observe Finder progress and bounded RSS (≈ chunk × active × small constant). Record.
- [ ] **Step 6: Commit.**
```bash
git add configurator/fileprovider/Extension/FetchController.swift \
        configurator/fileprovider/Tests/FetchControllerTests.swift \
        configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_streaming.py
git commit -m "feat(fileprovider): progress + bounded one-chunk streaming, size/edge coverage"
```

**Expected observable result:** Correct multi-chunk transfer, Finder progress, bounded memory; edge inputs rejected cleanly.

**Failure/rollback condition:** Memory grows with file size → backpressure broken; block.

**Dependencies:** Task 10.

---

### Task 12: Cancellation (no new wire message)

**Goal:** Finder cancel → `Progress.cancellationHandler` → Swift `cancelFetch` → XPC → Python drops the fetch and stops `FILE_READ`; late `FILE_CHUNK` ignored; temp removed; `NSUserCancelledError`. No `FILE_CANCEL`.

**Files:**
- Modify: `configurator/fileprovider/Extension/FetchController.swift`
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py`
- Create: `configurator/tests/transfer/test_fileprovider_cancel.py`

**Interfaces:**
- Produces: Swift sets `progress.cancellationHandler = { host.cancelFetch(token) }` and, on cancel, deletes temp + `completion(nil, nil, NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError))`. Python `cancel_fetch(token)` removes from `by_token`/`by_read_id`, stops sending `FILE_READ`; a late `FILE_CHUNK` whose `read_id` is gone is dropped.
- Consumes: Task 11.

- [ ] **Step 1: Write failing cancel-race tests.** cancel a `QUEUED` fetch → removed from queue, no `FILE_READ` ever; cancel an active fetch → stops further `FILE_READ`; cancel while a chunk is in flight → the arriving `FILE_CHUNK` is dropped (unknown `read_id`), no write; cancel vs completion race → whichever wins, the other is a no-op; assert **no** `FILE_CANCEL` message type is ever constructed (there is none in `MessageType`).
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_cancel.py -q`.
- [ ] **Step 3: Implement cancel plumbing** (Python drop + Swift handler + temp cleanup).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: System cancel gate (MANUAL).** Start a large paste, hit Finder cancel; destination empty, source stays dataless, snapshot still serves a later paste.
- [ ] **Step 6: Commit.**
```bash
git add configurator/fileprovider/Extension/FetchController.swift \
        configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_cancel.py
git commit -m "feat(fileprovider): cancellation via existing wire, no FILE_CANCEL"
```

**Expected observable result:** Clean cancel end-to-end with no wire change; late chunks ignored.

**Failure/rollback condition:** Any new `MessageType` added → block (spec forbids `FILE_CANCEL`).

**Dependencies:** Task 11.

---

### Task 13: Multiple files + directory concurrency

**Goal:** From single-file to a real manifest tree with concurrent child fetches, honoring `MAX_ACTIVE_FETCHES`, queue fairness, and per-fetch isolation.

**Files:**
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py` (if needed)
- Create: `configurator/tests/transfer/test_fileprovider_tree.py`
- Create: `configurator/fileprovider/Tests/EnumeratorTreeTests.swift`

**Interfaces:**
- Produces: no new API; exercises Task 9 scheduler + Task 5 enumerator on a nested manifest.
- Consumes: Tasks 5, 9, 10.

- [ ] **Step 1: Write failing tree tests.** Two flat files fetch concurrently to correct bytes; nested dir → children materialize; same filename in different dirs → distinct fetches/temps; N>4 concurrent children respect `MAX_ACTIVE_FETCHES`; queue fairness (FIFO); cancelling one child does not disturb siblings.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_tree.py -q`.
- [ ] **Step 3: Implement any gaps** (e.g., independent temp per fetch already from Task 10; verify no shared buffer). Add `EnumeratorTreeTests` for deep trees.
- [ ] **Step 4: Run — expect PASS** (Python + `xcodebuild test`).
- [ ] **Step 5: Directory System gate (MANUAL).** Paste a folder with nested files; all materialize, tree reproduced, concurrent child fetches observed in logs.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/tests/transfer/test_fileprovider_tree.py \
        configurator/fileprovider/Tests/EnumeratorTreeTests.swift
git commit -m "feat(fileprovider): multi-file and directory concurrent fetch"
```

**Expected observable result:** Real trees paste with bounded concurrency and per-fetch isolation.

**Failure/rollback condition:** Sibling interference on cancel/error → block.

**Dependencies:** Tasks 5, 9, 10.

---

### Task 14: Disconnect / reconnect / error mapping

**Goal:** Cover peer disconnect, Python restart, extension restart, XPC invalidation, and map each internal failure to a specific `NSFileProviderError`/Foundation error — no generic `NSError` everywhere.

**Files:**
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py`
- Modify: `configurator/fileprovider/Extension/FetchController.swift`
- Create: `configurator/tests/transfer/test_fileprovider_errors.py`
- Create: `configurator/fileprovider/Tests/ErrorMappingTests.swift`

**Interfaces:**
- Produces: mapping (spec §16): `source_missing → NSFileProviderError.noSuchItem`/`cannotSynchronize`; `source_changed → cannotSynchronize`; `peer_lost → serverUnreachable`; `unauthorized → notAuthenticated`; `timeout → serverUnreachable`; `disk_full → POSIX ENOSPC`; `protocol → cannotSynchronize`; `cancelled → NSUserCancelledError`; `not connected (host down) → serverUnreachable` (**retriable, do not delete item**). Python emits `DuoFPError` codes (Task 2); Swift `ErrorMap.toNSFileProviderError(DuoFPError) -> NSError`.
- Consumes: Task 2 error domain, Tasks 10–13.

- [ ] **Step 1: Write failing Python error tests.** `link.disconnected` fails all active fetches with `PeerLost`; host-down → `open_fetch`/`pull_chunk` reply `NotConnected`; `FILE_ERROR source_changed`/`source_missing` → mapped codes; session watchdog → `Timeout`; oversized/truncated → `Protocol`.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_errors.py -q`.
- [ ] **Step 3: Implement Python fail-all + code mapping; Swift `ErrorMap` + `ErrorMappingTests`.** Confirm exact `NSFileProviderError.*` constants against the SDK headers (spec says verify, don't invent).
- [ ] **Step 4: Run — expect PASS** (Python + `xcodebuild test`).
- [ ] **Step 5: Restart System gates (MANUAL, CLEAN_MACHINE_REQUIRED).** Host restart → rediscovery + new round-trip (Phase 10.2 `HOST_RESTART_REDISCOVERY = PASS`); extension restart (system-driven, **not** `kill -9`) → reconnect; Windows disconnect mid-fetch → active fetches fail, enumeration still serves from replica.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/fileprovider/Extension/FetchController.swift \
        configurator/tests/transfer/test_fileprovider_errors.py \
        configurator/fileprovider/Tests/ErrorMappingTests.swift
git commit -m "feat(fileprovider): disconnect/reconnect handling + specific error mapping"
```

**Expected observable result:** Each failure maps to a specific, correct error; host-down is retriable and never deletes items.

**Failure/rollback condition:** Generic `NSError` for a case with a defined mapping → block. Any use of `kill -9` on the extension in a test/gate → reject (R1).

**Dependencies:** Tasks 10–13.

---

### Task 15: Generation lifecycle / retire / GC

**Goal:** Implement `ACTIVE_CLIPBOARD → RETIRED → IN_USE → GC_ELIGIBLE`, so a new clipboard does not delete a still-in-use generation, with TTL/budget and `TRANSFER_END`/`close_descriptors` at the right moment — without changing sender `RETENTION`.

**Files:**
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py`
- Modify: `configurator/fileprovider/Extension/ReplicaStore.swift` (retire application)
- Create: `configurator/tests/transfer/test_fileprovider_lifecycle.py`

**Interfaces:**
- Produces: backend generation states + `_gc()` (TTL 24 h, generation-count budget) reusing the `StagingArea.gc` idiom. On supersede/new-clipboard, `retireGeneration` over XPC persists `state="retired"` (the record is **kept** — still enumerable/servable while referenced). GC (past TTL / over budget, **no in-use ref**) calls `deleteGeneration` over XPC to **permanently remove** the replica record + signal the OS to drop the items. `TRANSFER_END{status}` is sent when a generation quiesces (freeing sender fds via `close_descriptors`, sender unchanged). An active fetch increments an in-use ref that blocks `deleteGeneration` (never `retireGeneration`).
- Consumes: Tasks 7, 10, 14.

- [ ] **Step 1: Write failing lifecycle tests.** Copy A then copy B → A is `retireGeneration`'d to `state="retired"` (record **still present**, not removed) and stays `IN_USE` while an A fetch is active; paste A already active → served; repeated paste of a completed generation → served locally, no new remote fetch; a retired generation with **no in-use ref** past TTL → `GC_ELIGIBLE` → `deleteGeneration` called and the replica record is gone; budget exceeded → oldest retired generation `deleteGeneration`-evicted; an in-use ref blocks `deleteGeneration`; sender `RETENTION` never referenced/modified.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_lifecycle.py -q`.
- [ ] **Step 3: Implement lifecycle + retire (state only) + GC delete.** Retire calls `remote().retireGeneration_reply_` (persist `state="retired"`); GC calls `remote().deleteGeneration_reply_` (permanent removal) only when no in-use ref and TTL/budget say so.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Restart recovery gate (System, MANUAL).** After app restart, stale generations without a live snapshot are purged on startup; the current one refreshes its clipboard.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/fileprovider/Extension/ReplicaStore.swift \
        configurator/tests/transfer/test_fileprovider_lifecycle.py
git commit -m "feat(fileprovider): generation lifecycle, retire, TTL/budget GC"
```

**Expected observable result:** No premature deletion of in-use generations; bounded replica growth; sender untouched.

**Failure/rollback condition:** Any modification/reference to `SnapshotRegistry.RETENTION` → block (out of scope).

**Dependencies:** Tasks 7, 10, 14.

---

### Task 16: Staging fallback integration (per-offer selection)

**Goal:** Make File Provider and staging two backends of one receive flow, selected **once per offer**, with all fallback cases routing to staging — never switching backend mid-generation, never duplicating `FILE_READ` or double-arming the clipboard.

**Files:**
- Modify: `configurator/src/duo_input/transfer/platform_files.py` (per-offer selection + FP availability probe)
- Modify: `configurator/src/duo_input/app.py` (wire both backends; flag)
- Create: `configurator/tests/transfer/test_fileprovider_backend_selection.py`
- Modify: `configurator/tests/transfer/test_platform_files.py`

**Interfaces:**
- Produces: a receive dispatcher `MacReceiveRouter(QObject)` (or an extension of the darwin branch) exposing the same `MacFileReceiver` interface to `app.py` (`handle_offer/authorize/handle_message/cancel/attach_link/set_peer_capabilities/stop` + the transfer signals) and choosing `FileProviderBackend` when `fileprovider_enabled` flag is on AND domain reaches READY within the window AND service is available AND OS supported; otherwise `MacFileReceiver`. The choice is fixed per `transfer_id`.
- Consumes: Tasks 6, 7–15; existing `create_file_backend` darwin branch.

- [ ] **Step 1: Write failing selection tests.** FP available → offer handled by FP backend; FP unavailable (flag off / domain not ready / service unavailable / publish fails before arm / unsupported OS) → same offer handled by staging; the selected backend for a `transfer_id` never changes mid-generation; exactly one backend sends `FILE_READ` for a given offer (no duplicates); clipboard armed exactly once.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_backend_selection.py -q`.
- [ ] **Step 3: Implement the router + probe + `app.py` wiring** behind `clipboard/fileprovider_enabled` (default per Task 20). Keep the existing staging wiring intact as the fallback branch.
- [ ] **Step 4: Run — expect PASS**, and re-run `tests/transfer/test_platform_files.py` and `tests/transfer/test_macos_receiver.py` to confirm staging behavior is unchanged.
- [ ] **Step 5: Fallback System gate (MANUAL).** With FP force-disabled, a paste still works via staging identically to today.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/transfer/platform_files.py configurator/src/duo_input/app.py \
        configurator/tests/transfer/test_fileprovider_backend_selection.py \
        configurator/tests/transfer/test_platform_files.py
git commit -m "feat(fileprovider): per-offer backend selection with staging fallback"
```

**Expected observable result:** Seamless per-offer choice; staging unchanged; no duplicate reads or double arm.

**Failure/rollback condition:** Backend switches mid-generation, or staging regresses → block.

**Dependencies:** Tasks 6–15.

---

### Task 17: Observability

**Goal:** Emit the spec's counters and a correlation-id log chain (`transfer_id`, `entry_index`, `fetch_token`, `read_id`) without logging file contents or full user paths.

**Files:**
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py`
- Modify: `configurator/fileprovider/Extension/FetchController.swift`, `Enumerator.swift`
- Create: `configurator/tests/transfer/test_fileprovider_observability.py`

**Interfaces:**
- Produces: counters `fp_fetch_started/_completed/_cancelled/_failed`, `fp_active_fetches`, `fp_queued_fetches`, `fp_bytes_received`, `fp_domain_not_ready`, `fp_domain_state`, `fp_ipc_connect/_disconnect`, `fp_late_chunk/_oversized_chunk/_truncated`, `fp_gc_generation`, `fp_generation_active`, `fp_backend_selected{file_provider|staging}`. Log records include correlation ids; names only, never paths/content (mirror `_name()` in `source.py`).
- Consumes: all backend tasks.

- [ ] **Step 1: Write failing observability tests.** Assert counters increment on the right transitions; assert log records contain `transfer_id`/`read_id` but never a full path or blob bytes (scan emitted records).
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_observability.py -q`.
- [ ] **Step 3: Implement counters + structured logs** (Python + Swift markers).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit.**
```bash
git add configurator/src/duo_input/transfer/fileprovider_backend.py \
        configurator/fileprovider/Extension/FetchController.swift \
        configurator/fileprovider/Extension/Enumerator.swift \
        configurator/tests/transfer/test_fileprovider_observability.py
git commit -m "feat(fileprovider): counters + correlation-id logging, no content/paths"
```

**Expected observable result:** Full correlation chain; privacy-safe logs.

**Failure/rollback condition:** Any path/content in logs → block (spec §15/§28).

**Dependencies:** Tasks 9–16.

---

### Task 18: Production packaging (Nuitka host + shim + `.appex`, signed)

**Goal:** Integrate the native shim dylib and the `.appex` into the real build: Nuitka host, embed the appex in `Contents/PlugIns`, nested-first signing, outer signing, codesign verification, on Personal Team — with a bundle-ID collision check.

**Files:**
- Modify: `configurator/packaging/nuitka-build-macos.sh`
- Modify: `configurator/requirements-build.txt` (already added FileProvider in Task 3; confirm)
- Modify: `configurator/tests/packaging/test_dist.py`
- Create: `configurator/packaging/fileprovider-build.sh` (xcodebuild the `.appex` + dylib)

**Interfaces:**
- Produces: `dist/DuoInput.app/Contents/PlugIns/DuoInputFileProvider.appex` + `Contents/Frameworks/libduofpproto.dylib`; a signed bundle (`codesign --verify --deep --strict` passes). Nuitka gains `--include-module=duo_input.transfer.fileprovider_backend/_client/_domain/_proto/_replica`, `--include-data-files` for the dylib, and does **not** need App Group entitlements.
- Consumes: Tasks 1–17 artifacts.

- [ ] **Step 1: Write the failing packaging test.** Extend `test_dist.py`: after a build, assert the `.appex` exists at `Contents/PlugIns/DuoInputFileProvider.appex`, the dylib exists in `Contents/Frameworks/`, `codesign -dv` on both nested and outer returns Team `4YKVN22BMX`, host entitlements contain **no** `application-groups`, and appex entitlements contain `app-sandbox=true`. Add a bundle-ID collision guard: assert host/appex/domain ids are unique and are not any known spike id.
- [ ] **Step 2: Run — expect FAIL** (no appex/dylib/signing yet). Run: `cd configurator && python -m pytest tests/packaging/test_dist.py -q` (or the build-gated variant).
- [ ] **Step 3: Implement `fileprovider-build.sh`** (xcodegen + xcodebuild the `.appex` + dylib, Personal Team automatic signing). **Update `nuitka-build-macos.sh`:** add the FP `--include-module`/data-file flags; after Nuitka produces `DuoInput.app`, run `fileprovider-build.sh`, copy the dylib into `Contents/Frameworks`, embed the `.appex` into `Contents/PlugIns`, `codesign` the appex first, then the outer app (`--options runtime`), then `codesign --verify --deep --strict`. Keep the clipboard test gate. Do **not** add any restricted entitlement.
- [ ] **Step 4: Run — expect PASS** (build + `test_dist.py`).
- [ ] **Step 5: Install + domain-add smoke (System, MANUAL, CLEAN_MACHINE_REQUIRED).** Install to `/Applications`, launch, `pluginkit` lists the appex, host adds the domain (`DOMAIN_ADD = SUCCESS`), one lazy paste works (Phase 10.2 Gate 4 parity on the real bundle).
- [ ] **Step 6: Commit.**
```bash
git add configurator/packaging/nuitka-build-macos.sh configurator/packaging/fileprovider-build.sh \
        configurator/requirements-build.txt configurator/tests/packaging/test_dist.py
git commit -m "build(fileprovider): embed + sign appex and shim in the Nuitka bundle"
```

**Expected observable result:** A signed bundle containing the appex + dylib, verified, no App Group; collision guard green.

**Failure/rollback condition:** `codesign --verify --deep --strict` fails, or any `application-groups`/named-Mach/temp-exception entitlement appears → block. Distribution notarization is **out of scope** (future milestone) but the layout must be notarization-ready.

**Dependencies:** Tasks 1–17.

---

### Task 19: Full regression / E2E matrix

**Goal:** A checked-in, executable matrix covering the spec's scenarios, with a separate clean-machine fresh-domain test; do not attempt to reproduce or repair a polluted dev `fileproviderd`.

**Files:**
- Create: `configurator/tests/transfer/test_fileprovider_matrix.py` (the AUTOMATABLE rows)
- Create: `docs/superpowers/records/validation/fileprovider-e2e-matrix.md` (MANUAL/E2E rows + tags)

**Interfaces:**
- Produces: a single parametrized Python test for AUTOMATABLE rows and a documented manual runbook for System/E2E rows, each tagged `AUTOMATABLE`/`MANUAL`/`CLEAN_MACHINE_REQUIRED`.
- Consumes: everything.

- [ ] **Step 1: Write the failing matrix test (AUTOMATABLE rows).** Parametrize over: single small file, zero-byte, multiple flat files, nested directory, cancel (queued/active/in-flight), source changed, source missing, peer disconnect, repeated paste, new clipboard while old fetch active, **stale Accept after supersede → no publish/replica/arm/read (FP)**, Ask deny, Ask accept, Auto, FP-unavailable→staging, unicode names, duplicate names in different dirs, and the two backend-specific privacy rows:
  - **File Provider:** authorized + armed + **no `Cmd+V`** → **zero `FILE_READ`**.
  - **Staging (intentionally eager, semantics unchanged):** authorized → staging transfer **may begin** → after it completes the clipboard is armed. Do **not** assert zero `FILE_READ` for staging.

  Use the fake-link/fake-sender harness from Task 10.
- [ ] **Step 2: Run — expect FAIL, then wire each row to existing behavior.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/transfer/test_fileprovider_matrix.py -q`.
- [ ] **Step 3: Run — expect PASS.**
- [ ] **Step 4: Author the MANUAL/E2E runbook** including: large file, multi-GB, host restart, extension restart, disk full, domain-not-ready, and the **clean-machine fresh-domain** test (`CLEAN_MACHINE_REQUIRED`). Explicitly state polluted-`fileproviderd` reproduction is out of scope.
- [ ] **Step 5: Commit.**
```bash
git add configurator/tests/transfer/test_fileprovider_matrix.py \
        docs/superpowers/records/validation/fileprovider-e2e-matrix.md
git commit -m "test(fileprovider): automated regression matrix + manual E2E runbook"
```

**Expected observable result:** Automated matrix green; manual runbook complete and tagged.

**Failure/rollback condition:** Any AUTOMATABLE row red → block. Do not add a row that requires a polluted `fileproviderd`.

**Dependencies:** Tasks 10–17.

---

### Task 20: Rollout (feature flag + fallback + telemetry)

**Goal:** Ship File Provider behind a config flag with a safe fallback, defined defaults, telemetry, and criteria for keeping/removing staging — staging is **not** removed now.

**Files:**
- Modify: `configurator/src/duo_input/app.py` (flag default + UI/tray hook if needed)
- Create: `docs/superpowers/records/fileprovider-rollout.md`
- Modify: `configurator/tests/ui/test_runtime_wiring.py` (assert flag default + fallback wiring)

**Interfaces:**
- Produces: settings key `clipboard/fileprovider_enabled` (default **False** in the first shipped version — staging remains default; Stage 3 flips to True on supported macOS with a valid domain). Rollback = flip the flag; per-offer selection means no data migration.
- Consumes: Task 16 router.

- [ ] **Step 1: Write the failing rollout wiring test.** Assert: flag default is False on first run; with flag on + FP available, the router selects FP; with flag off, staging; toggling the flag never migrates or breaks an in-flight generation.
- [ ] **Step 2: Run — expect FAIL.** Run: `cd configurator && QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest tests/ui/test_runtime_wiring.py -q`.
- [ ] **Step 3: Implement the flag + defaults + telemetry counters (reuse Task 17).**
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Write `fileprovider-rollout.md`:** Stage 1 flag OFF (staging default); Stage 2 developer opt-in (architecture already proven Phase 10.2); Stage 3 default ON on supported macOS with a valid domain, staging = automatic fallback; Stage 4 staging remains permanent fallback. Criteria to revisit staging removal: N releases of FP default-ON with fallback rate below a threshold.
- [ ] **Step 6: Commit.**
```bash
git add configurator/src/duo_input/app.py docs/superpowers/records/fileprovider-rollout.md \
        configurator/tests/ui/test_runtime_wiring.py
git commit -m "feat(fileprovider): rollout flag with staging fallback and telemetry"
```

**Expected observable result:** Flag-gated rollout with trivial rollback; staging preserved.

**Failure/rollback condition:** Flag default True on first ship, or staging removed → block.

**Dependencies:** Tasks 16, 17.

---

## Dependency review / sequencing

Critical path (matches spec §12; adjusted to real code — first real lazy paste at **Task 10**, well before the end):

```
1 native target
2 shared XPC contract (clang shim)
3 host client ─┐
4 replica      ├─ (3,4 parallelizable after 2)
5 enumerator (needs 4)
6 domain (needs 3's dependency)
7 offer publication (needs 3,4,6)
8 clipboard URL (needs 7)
9 backend scheduler (needs 7)
10 FIRST REAL LAZY PASTE (needs 3,5,8,9) ← earliest end-to-end checkpoint
11 streaming (needs 10)
12 cancel (needs 11)
13 concurrency/tree (needs 5,9,10)
14 disconnect/errors (needs 10–13)
15 lifecycle/GC (needs 7,10,14)
16 staging fallback (needs 6–15)
17 observability (needs 9–16)
18 packaging (needs 1–17)
19 E2E matrix (needs 10–17)
20 rollout (needs 16,17)
```

Parallelizable once Task 2 lands: {3, 4} then {5, 6}. Tasks 8 and 9 both depend only on 7 and can proceed together. Swift-only tasks (1, 4-swift, 5) and Python-only tasks (3, 6, 9) can be split across workers between synchronization points at 7 and 10.

---

## Risk register

| # | Risk | Impact | Detection | Mitigation | Task |
|---|---|---|---|---|---|
| R1 | Fresh-domain `-2011` on a polluted dev `fileproviderd` | System/E2E gates stall on dev machine | `ls` deadlock / `-2011 "sync not enabled (null)"` | Run domain-dependent gates `CLEAN_MACHINE_REQUIRED`; never `kill -9` the extension; do not repair via SIP/private store | 6, 10, 11, 14, 18, 19 |
| R2 | PyObjC↔clang XPC protocol on the **3.12** production build (spike proved 3.14) | Build ships without working XPC | Task 2/3 import + interface tests on 3.12 | Verify on 3.12 first; if it fails, escalate the 3.12-vs-3.14 decision (do not silently bump); protocol from clang dylib, never `formal_protocol` | 2, 3, 18 |
| R3 | Qt-thread ↔ NSXPC callback marshalling races | Corrupt state, crashes | Threading asserts in backend; fake-connection tests | Marshal every extension→host callback onto the Qt thread (mirror `ServiceCallbackGateway`); backend state touched only on Qt thread | 3, 9 |
| R4 | Concurrent `FILE_CHUNK` routed to the wrong fetch | Wrong bytes in a file | `_pending_reply`-style `(read_id,transfer_id,entry_index,offset)` match test; tree tests | Per-fetch `read_id`; drop non-matching chunks (reuse sender's validation shape) | 9, 10, 13 |
| R5 | Extension restart during a fetch | Orphaned fetch / stuck UI | Restart System gate; watchdog | Watchdog fails orphaned fetches; connection invalidation clears `by_token`; host rediscovers | 12, 14 |
| R6 | Generation GC vs OS-held URLs | Deleting an in-use item | Lifecycle tests (IN_USE ref) | In-use ref blocks GC; retire only when quiesced; keep `ACTIVE_CLIPBOARD` | 15 |
| R7 | Nuitka packaging misses the shim/`.appex` | "works in spike, broken in package" | `test_dist.py` asserts appex+dylib present & signed | Explicit `--include`/embed/sign steps + packaging test | 18 |
| R8 | File Provider backend behaves eagerly (reads before `Cmd+V`) | Bytes leave source pre-paste on the **FP** path | FP privacy test: authorized + armed + **no `Cmd+V`** → zero `FILE_READ` | Assert the zero-read invariant on the **FP backend only**; staging fallback is **intentionally eager** (downloads after authorization) and its semantics are **unchanged**; per-offer selection is fixed once and never switched mid-generation | 8, 16, 19 |

---

## Self-review (run against the spec)

- **Spec coverage:** IPC §7 → Tasks 1–3,10; replica §25/§33 → Task 4; identity/version §9/§30 → Task 5; domain §10 → Task 6; publication §11/§22 → Task 7; clipboard §13 → Task 8; fetch state machine §14 → Tasks 9–10; streaming §16 → Task 11; cancel §18 → Task 12; concurrency §17 → Tasks 9,13; directories §31 → Task 13; restart/errors §16/§24 → Task 14; lifecycle/GC §20/§25 → Task 15; fallback §27 → Task 16; observability §28 → Task 17; packaging §26 → Task 18; test strategy → Task 19; rollout → Task 20; superseded/App-Group-removed/servicing-invariant → Global Constraints + Tasks 1,18. No uncovered spec section found.
- **Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" left; every code step shows concrete test code or exact interface signatures.
- **Type consistency:** `FileProviderBackend` interface (signals/methods) is defined once in Task 7 and extended in 8–17 with the same names; the authorization-epoch signatures are consistent — `handle_offer(manifest) -> int`, `authorize(accepted, epoch)`, `authorization_needed = Signal(object, int)` (Task 7), with the epoch captured and passed through by the app wiring in Task 16; `Fetch`/`FetchState` defined in Task 9; `build_generation_record` signature identical in Tasks 4 and 7; `DuoExtensionControl` has exactly `publishGeneration`/`retireGeneration`/`deleteGeneration` (Task 2) and `ReplicaStore` exposes matching `publish`/`retire`/`delete` (Task 4), used consistently in Task 15 (`retireGeneration` = persist retired, `deleteGeneration` = GC removal — no ambiguous "retire then remove"); `DuoHostCallback` selectors match between Tasks 2, 3, 10; `arm_urls` signature consistent Task 8. `itemIdentifier = "<transfer_id>:<entry_index>"` used identically in Tasks 5, 9, 10.
- **Correction consistency (this revision):** (1) Task 10 depends on 3,5,**8**,9 (graph + header agree); (2) stale-authorization epoch guard in Task 7 + matrix row in Task 19; (3) retire/delete split in Tasks 2/4/15; (4) staging-is-eager reflected in the Global Constraints privacy invariant, R8, and Task 19 (no `staging → zero FILE_READ` assertion anywhere), and Task 11 zero-byte states `openFetch` allowed / `FILE_READ == 0` / empty temp.

---

## Final output

```
PLAN_STATUS                 = READY
TASK_COUNT                  = 20
FIRST_LAZY_E2E_TASK         = Task 10 (openFetch → FILE_READ → FILE_CHUNK → temp → completion)
FILES2_CHANGE               = NO
WINDOWS_SENDER_CHANGE       = NO
APP_GROUP                   = NO
STAGING                     = PRESERVED
IMPLEMENTATION_BLOCKERS     = NONE
  (open decision, non-blocking: production build Python 3.12 vs spike 3.14 — resolve in Task 3 by
   verifying pyobjc-framework-FileProvider + clang-dylib XPC on 3.12 before Task 18; default keep 3.12)
NEXT_ACTION                 = execute Task 1
```

STOP. Do not execute Task 1 automatically.
