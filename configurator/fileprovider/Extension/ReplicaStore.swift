import Darwin
import Foundation

/// One durable generation record, replicated from the Python-authoritative
/// source over `publishGeneration` (see `DuoFPProto.h`). Metadata only - no
/// bytes, no FILE_READ/FILE_CHUNK state, no fetch-scheduler state, no
/// peer/TLS state, no SnapshotRegistry state, no authorization state. The
/// Python builder that produces byte-identical records lives in
/// `fileprovider_replica.py`; both sides load the same golden fixture
/// (`tests/transfer/fixtures/generation_record.json`) to prove it.
struct GenerationRecord {
    static let activeState = "active"
    static let retiredState = "retired"

    let schema: Int
    let transferId: String
    let state: String
    let createdNs: Int64
    let leaseDeadlineNs: Int64
    /// Opaque manifest payload (`manifest.to_dict()` on the Python side).
    /// Kept as a raw parsed JSON object - this store never needs to
    /// understand its shape, only durably persist it.
    let manifest: [String: Any]

    var isActive: Bool { state == GenerationRecord.activeState }
}

enum ReplicaStoreError: Error, Equatable {
    case invalidRecord(String)
    case notFound(String)
    case io(String)
}

/// Extension-private durable replica of generation records.
///
/// Lives in the extension's OWN sandbox container (no App Group, no shared
/// container - `APP_GROUP = NO`). Serialization is one JSON file per
/// generation under `<base>/generations/<transfer_id>.json`; there is no
/// cross-file consistency to maintain, so every mutation is a single
/// temp-file-then-rename.
///
/// ## Atomic-write guarantee
/// `publish`/`retire` both go through `durableWrite`, which:
///   1. Writes the full record bytes to a temp file in the SAME directory as
///      the target (`generations/.tmp-<id>-<uuid>.json`) - same filesystem,
///      so the rename in step 3 is atomic.
///   2. `fsync`s the temp file (`FileHandle.synchronize()`), so the bytes are
///      durable on stable storage before the file is ever visible under the
///      live name.
///   3. Atomically renames the temp file onto the final path via the POSIX
///      `rename(2)` syscall, which POSIX guarantees is atomic on the same
///      filesystem: a concurrent reader (or a process restarting after a
///      crash) can never observe a partially-written file at the live path -
///      it sees either the previous record (rename hasn't happened yet) or
///      the fully-written new one (rename completed). A torn temp file is
///      never the thing named at the live path, because rename only ever
///      publishes a name once the temp file's contents are complete and
///      fsync'd.
///   4. Best-effort `fsync`s the containing directory, since POSIX does not
///      guarantee the directory-entry update from step 3 is durable merely
///      because the file's own fsync (step 2) happened.
///
/// `publish` ACKs (returns without throwing) only after this whole sequence
/// completes; any failure at any step throws before anything is renamed into
/// place, so the caller's ACK-after-durable-write invariant holds trivially.
final class ReplicaStore {
    private let fileManager: FileManager
    let baseDirectory: URL
    let generationsDir: URL
    let quarantineDir: URL

    /// Production base: the sandboxed extension's own container Data
    /// directory. `NSHomeDirectory()` inside the appex resolves to the
    /// container, never the real user home - but the (non-sandboxed) unit
    /// test bundle must never rely on that, which is why this is injectable
    /// and defaulted, not hardcoded into every call site.
    static func defaultBaseDirectory() -> URL {
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/DuoReplica", isDirectory: true)
    }

    init(baseDirectory: URL = ReplicaStore.defaultBaseDirectory(), fileManager: FileManager = .default) {
        self.fileManager = fileManager
        self.baseDirectory = baseDirectory
        self.generationsDir = baseDirectory.appendingPathComponent("generations", isDirectory: true)
        self.quarantineDir = generationsDir.appendingPathComponent("quarantine", isDirectory: true)
        try? fileManager.createDirectory(at: generationsDir, withIntermediateDirectories: true)
        recover()
    }

    // MARK: - Public API

    /// Durably persists `recordJSON` verbatim (after validating it parses as
    /// a well-formed schema-v1 record) at `generations/<transfer_id>.json`,
    /// overwriting any existing record for that id. Returns only after the
    /// durable write (see type doc) completes; throws on any failure and
    /// never leaves a torn file at the live path.
    @discardableResult
    func publish(recordJSON: Data) throws -> GenerationRecord {
        let (_, record) = try parseRecord(recordJSON)
        try durableWrite(recordJSON, transferId: record.transferId)
        return record
    }

    /// Rewrites the SAME record with `state="retired"`. The record is KEPT -
    /// still readable via `record(for:)` - but excluded from `allActive()`.
    /// Never deletes; retired is a distinct, durable, observable state.
    func retire(_ generationId: String) throws {
        try requireSafeId(generationId)
        let url = fileURL(for: generationId)
        guard let existing = try? Data(contentsOf: url) else {
            throw ReplicaStoreError.notFound(generationId)
        }
        let (dict, _) = try parseRecord(existing)
        var mutable = dict
        mutable["state"] = GenerationRecord.retiredState
        let newData = try JSONSerialization.data(
            withJSONObject: mutable,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        try durableWrite(newData, transferId: generationId)
    }

    /// GC step: permanently removes the record file. After this,
    /// `record(for:)` returns nil. Never called implicitly by `retire` -
    /// retire-then-delete is always two explicit calls (Task 15 owns GC).
    func delete(_ generationId: String) throws {
        try requireSafeId(generationId)
        let url = fileURL(for: generationId)
        guard fileManager.fileExists(atPath: url.path) else {
            throw ReplicaStoreError.notFound(generationId)
        }
        do {
            try fileManager.removeItem(at: url)
        } catch {
            throw ReplicaStoreError.io("не удалось удалить запись: \(error)")
        }
        fsyncDirectory(generationsDir)
    }

    /// Returns the record for `generationId`, active or retired, or nil if
    /// it does not exist or fails to parse.
    func record(for generationId: String) -> GenerationRecord? {
        guard (try? requireSafeId(generationId)) != nil else { return nil }
        let url = fileURL(for: generationId)
        guard let data = try? Data(contentsOf: url) else { return nil }
        guard let (_, record) = try? parseRecord(data) else { return nil }
        return record
    }

    /// All records with `state == "active"`. Excludes retired records and
    /// anything that fails to parse (recover() is what relocates those).
    func allActive() -> [GenerationRecord] {
        listGenerationFiles().compactMap { url in
            guard let data = try? Data(contentsOf: url) else { return nil }
            guard let (_, record) = try? parseRecord(data) else { return nil }
            return record.isActive ? record : nil
        }
    }

    /// Every durable record, active OR retired (parse failures excluded). The
    /// working-set LIST is built from this minus the journal's tombstoned ids -
    /// a retired generation is still namespace-live and must appear in the
    /// working set, unlike in the root LIST (`allActive()`).
    func allRecords() -> [GenerationRecord] {
        listGenerationFiles().compactMap { url in
            guard let data = try? Data(contentsOf: url) else { return nil }
            guard let (_, record) = try? parseRecord(data) else { return nil }
            return record
        }
    }

    /// Scans the live generations directory and moves any file that fails to
    /// parse as a well-formed schema-v1 record into `generations/quarantine/`,
    /// so it stops shadowing `allActive()`/`record(for:)` without silently
    /// destroying the evidence. Runs automatically at startup (`init`) and is
    /// safe to call again at any time (idempotent).
    @discardableResult
    func recover() -> [URL] {
        var quarantined: [URL] = []
        for url in listGenerationFiles() {
            let isValid: Bool
            if let data = try? Data(contentsOf: url) {
                isValid = (try? parseRecord(data)) != nil
            } else {
                isValid = false
            }
            if !isValid {
                quarantined.append(quarantineFile(at: url))
            }
        }
        return quarantined
    }

    // MARK: - Internals

    private func requireSafeId(_ generationId: String) throws {
        // transfer_id is a short ASCII token on the Python side
        // (`model._TRANSFER_ID`); this is defense-in-depth against a
        // malformed id being used to escape `generationsDir`.
        if generationId.isEmpty || generationId.contains("/") {
            throw ReplicaStoreError.invalidRecord("недопустимый transfer_id: \(generationId)")
        }
    }

    private func fileURL(for generationId: String) -> URL {
        generationsDir.appendingPathComponent("\(generationId).json")
    }

    private func listGenerationFiles() -> [URL] {
        guard let items = try? fileManager.contentsOfDirectory(
            at: generationsDir, includingPropertiesForKeys: nil
        ) else { return [] }
        return items.filter { $0.pathExtension == "json" && !$0.lastPathComponent.hasPrefix(".") }
    }

    private func quarantineFile(at url: URL) -> URL {
        try? fileManager.createDirectory(at: quarantineDir, withIntermediateDirectories: true)
        let dest = quarantineDir.appendingPathComponent(url.lastPathComponent)
        try? fileManager.removeItem(at: dest)
        try? fileManager.moveItem(at: url, to: dest)
        return dest
    }

    /// Parses + validates a schema-v1 record. Returns both the raw dict
    /// (needed by `retire` to rewrite only the `state` field) and the typed
    /// `GenerationRecord`.
    private func parseRecord(_ data: Data) throws -> (dict: [String: Any], record: GenerationRecord) {
        guard let obj = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]),
              let dict = obj as? [String: Any] else {
            throw ReplicaStoreError.invalidRecord("не является JSON-объектом")
        }
        guard let schema = dict["schema"] as? Int, schema == 1 else {
            throw ReplicaStoreError.invalidRecord("schema должен быть 1")
        }
        guard let transferId = dict["transfer_id"] as? String, !transferId.isEmpty else {
            throw ReplicaStoreError.invalidRecord("transfer_id должен быть непустой строкой")
        }
        guard let state = dict["state"] as? String,
              state == GenerationRecord.activeState || state == GenerationRecord.retiredState else {
            throw ReplicaStoreError.invalidRecord("state должен быть active|retired")
        }
        guard let createdNs = dict["created_ns"] as? NSNumber else {
            throw ReplicaStoreError.invalidRecord("created_ns должен быть числом")
        }
        guard let leaseDeadlineNs = dict["lease_deadline_ns"] as? NSNumber else {
            throw ReplicaStoreError.invalidRecord("lease_deadline_ns должен быть числом")
        }
        guard let manifest = dict["manifest"] as? [String: Any] else {
            throw ReplicaStoreError.invalidRecord("manifest должен быть объектом")
        }
        let record = GenerationRecord(
            schema: schema,
            transferId: transferId,
            state: state,
            createdNs: createdNs.int64Value,
            leaseDeadlineNs: leaseDeadlineNs.int64Value,
            manifest: manifest
        )
        return (dict, record)
    }

    private func durableWrite(_ data: Data, transferId: String) throws {
        try requireSafeId(transferId)
        try? fileManager.createDirectory(at: generationsDir, withIntermediateDirectories: true)
        let finalURL = fileURL(for: transferId)
        let tmpURL = generationsDir.appendingPathComponent(".tmp-\(transferId)-\(UUID().uuidString).json")

        guard fileManager.createFile(atPath: tmpURL.path, contents: nil) else {
            throw ReplicaStoreError.io("не удалось создать временный файл")
        }

        let handle: FileHandle
        do {
            handle = try FileHandle(forWritingTo: tmpURL)
        } catch {
            try? fileManager.removeItem(at: tmpURL)
            throw ReplicaStoreError.io("не удалось открыть временный файл: \(error)")
        }
        do {
            try handle.write(contentsOf: data)
            // fsync: in-core data reaches stable storage before the temp
            // file is ever named at the live path.
            try handle.synchronize()
            try handle.close()
        } catch {
            try? handle.close()
            try? fileManager.removeItem(at: tmpURL)
            throw ReplicaStoreError.io("не удалось записать/сбросить временный файл: \(error)")
        }

        let renameResult = tmpURL.path.withCString { tmpCString in
            finalURL.path.withCString { finalCString in
                rename(tmpCString, finalCString)
            }
        }
        if renameResult != 0 {
            let savedErrno = errno
            try? fileManager.removeItem(at: tmpURL)
            throw ReplicaStoreError.io("rename не удался: \(String(cString: strerror(savedErrno)))")
        }

        fsyncDirectory(generationsDir)
    }

    private func fsyncDirectory(_ url: URL) {
        let fd = open(url.path, O_RDONLY)
        guard fd >= 0 else { return }
        fsync(fd)
        close(fd)
    }
}
