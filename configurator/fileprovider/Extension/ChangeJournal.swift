import Darwin
import Foundation

/// One durable namespace-change batch. A batch is a single append event: it
/// carries exactly one `kind` and one or more item identifiers, and owns one
/// monotonic `revision`. This maps directly onto the File Provider change
/// observer (`didUpdateItems` / `didDeleteItemsWithIdentifiers`) - a batch of
/// updates becomes one `didUpdate`, a batch of deletes one `didDelete`.
struct JournalChange: Equatable {
    enum Kind: String { case update, delete }
    let revision: UInt64
    let kind: Kind
    let itemIdentifiers: [String]
}

enum ChangeJournalError: Error, Equatable {
    case io(String)
}

/// Extension-private, durable, monotonic change log backing the working-set
/// sync anchor and namespace-deletion reconciliation (design:
/// 2026-09-21-fileprovider-generation-lifetime-tombstone-design.md).
///
/// Metadata only - no bytes, no item content. Lives beside `generations/` in
/// the extension's own sandbox container:
///
///   <base>/journal/head.json           { "revision": <UInt64> }   monotonic
///   <base>/journal/observed.json        { "revision": <UInt64> }   telemetry
///   <base>/journal/changes/<rev>.json   one JournalChange per append
///   <base>/journal/tombstones/<id>.json durable tombstone marker
///
/// Every mutation is a single temp-file-then-`rename(2)` (POSIX-atomic on the
/// same filesystem), fsync'd before it is named at the live path - the same
/// durability primitive `ReplicaStore.durableWrite` uses, so a crash/restart
/// never observes a torn file and the head is never rolled back.
///
/// ## Restart invariant
/// The head, the change files, the observed high-water mark, and the tombstone
/// markers all live on disk. A fresh `ChangeJournal` on the same directory
/// resumes from the persisted head (revisions never reset to zero) and can
/// still replay every not-yet-consumed change - this is what keeps a File
/// Provider item identity from ever outliving the metadata needed to resolve
/// or delete it across an extension restart, app restart, or reboot.
final class ChangeJournal {
    private let fileManager: FileManager
    private let journalDir: URL
    private let changesDir: URL
    private let tombstonesDir: URL
    private let headURL: URL
    private let observedURL: URL

    /// Serializes head reads/increments so concurrent appends can't collide on
    /// a revision number.
    private let lock = NSLock()

    init(baseDirectory: URL, fileManager: FileManager = .default) {
        self.fileManager = fileManager
        self.journalDir = baseDirectory.appendingPathComponent("journal", isDirectory: true)
        self.changesDir = journalDir.appendingPathComponent("changes", isDirectory: true)
        self.tombstonesDir = journalDir.appendingPathComponent("tombstones", isDirectory: true)
        self.headURL = journalDir.appendingPathComponent("head.json")
        self.observedURL = journalDir.appendingPathComponent("observed.json")
        try? fileManager.createDirectory(at: changesDir, withIntermediateDirectories: true)
        try? fileManager.createDirectory(at: tombstonesDir, withIntermediateDirectories: true)
    }

    // MARK: - Head / anchor

    /// Current head revision - the sync-anchor value. 0 on an empty journal.
    func headRevision() -> UInt64 {
        lock.lock(); defer { lock.unlock() }
        return readRevision(headURL)
    }

    // MARK: - Append

    /// Durably appends one change batch of `kind` for `itemIdentifiers`, bumps
    /// the head exactly once, and returns the new head revision. The change
    /// file is written and fsync'd before the head is advanced, so a crash
    /// between the two leaves a persisted change the next head simply re-covers
    /// on the following append (the head is the authority for "latest").
    ///
    /// Throws `ChangeJournalError.io` if either durable write fails - the caller
    /// (control path) must treat that as a failed operation and NOT signal the
    /// enumerators, so File Provider is never told about a change that did not
    /// reach stable storage.
    @discardableResult
    func append(kind: JournalChange.Kind, itemIdentifiers: [String]) throws -> UInt64 {
        lock.lock(); defer { lock.unlock() }
        let next = readRevision(headURL) + 1
        let change = JournalChange(revision: next, kind: kind, itemIdentifiers: itemIdentifiers)
        try writeChange(change)
        try writeRevision(next, to: headURL)
        return next
    }

    // MARK: - Tombstone

    /// Durably records that `transferId`'s items have left the namespace: writes
    /// a tombstone marker AND appends the `delete` change batch, returning the
    /// batch revision. The marker persists which identifiers were deleted at
    /// which revision, independent of the (still-kept) generation record - this
    /// phase never physically deletes the backing metadata.
    @discardableResult
    func recordTombstone(transferId: String, itemIdentifiers: [String]) throws -> UInt64 {
        let revision = try append(kind: .delete, itemIdentifiers: itemIdentifiers)
        let marker: [String: Any] = [
            "transfer_id": transferId,
            "tombstone_revision": revision,
            "item_identifiers": itemIdentifiers,
        ]
        let data = try JSONSerialization.data(withJSONObject: marker, options: [.sortedKeys])
        try durableWrite(data, to: tombstonesDir.appendingPathComponent("\(safeName(transferId)).json"))
        return revision
    }

    /// Transfer ids that have a durable tombstone marker.
    func tombstonedTransferIds() -> Set<String> {
        guard let files = try? fileManager.contentsOfDirectory(
            at: tombstonesDir, includingPropertiesForKeys: nil
        ) else { return [] }
        var ids: Set<String> = []
        for url in files where url.pathExtension == "json" && !url.lastPathComponent.hasPrefix(".") {
            guard let data = try? Data(contentsOf: url),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let id = obj["transfer_id"] as? String else { continue }
            ids.insert(id)
        }
        return ids
    }

    // MARK: - Replay

    /// All change batches with `revision > afterRevision`, ascending by
    /// revision. Reconstructed from the durable change files, so it survives
    /// restart and drives `enumerateChanges`.
    func changes(after afterRevision: UInt64) -> [JournalChange] {
        guard let files = try? fileManager.contentsOfDirectory(
            at: changesDir, includingPropertiesForKeys: nil
        ) else { return [] }
        var result: [JournalChange] = []
        for url in files where url.pathExtension == "json" && !url.lastPathComponent.hasPrefix(".") {
            guard let change = readChange(url), change.revision > afterRevision else { continue }
            result.append(change)
        }
        return result.sorted { $0.revision < $1.revision }
    }

    // MARK: - Observed high-water mark (telemetry only this phase)

    /// The max `from:` anchor the system has ever passed to `enumerateChanges`.
    /// Monotonic, durable. This phase it is telemetry / future-compaction
    /// research only - NEVER a permission to physically delete metadata.
    func observedRevision() -> UInt64 {
        lock.lock(); defer { lock.unlock() }
        return readRevision(observedURL)
    }

    func noteObserved(_ revision: UInt64) {
        lock.lock(); defer { lock.unlock() }
        guard revision > readRevision(observedURL) else { return }
        // Telemetry only - a failed write is not worth failing an enumeration.
        try? writeRevision(revision, to: observedURL)
    }

    // MARK: - Internals

    private func safeName(_ id: String) -> String {
        // transfer_id is a short ASCII token; defend against a stray separator.
        id.replacingOccurrences(of: "/", with: "_")
    }

    private func changeURL(for revision: UInt64) -> URL {
        // Zero-padded so a lexical directory listing is also revision order (the
        // explicit sort in `changes(after:)` does not rely on it, but it keeps
        // the on-disk layout human-readable).
        changesDir.appendingPathComponent(String(format: "%020llu.json", revision))
    }

    private func writeChange(_ change: JournalChange) throws {
        let obj: [String: Any] = [
            "revision": change.revision,
            "kind": change.kind.rawValue,
            "item_identifiers": change.itemIdentifiers,
        ]
        let data = try JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
        try durableWrite(data, to: changeURL(for: change.revision))
    }

    private func readChange(_ url: URL) -> JournalChange? {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let revision = (obj["revision"] as? NSNumber)?.uint64Value,
              let kindRaw = obj["kind"] as? String,
              let kind = JournalChange.Kind(rawValue: kindRaw),
              let ids = obj["item_identifiers"] as? [String] else { return nil }
        return JournalChange(revision: revision, kind: kind, itemIdentifiers: ids)
    }

    private func readRevision(_ url: URL) -> UInt64 {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let revision = (obj["revision"] as? NSNumber)?.uint64Value else { return 0 }
        return revision
    }

    private func writeRevision(_ revision: UInt64, to url: URL) throws {
        let data = try JSONSerialization.data(
            withJSONObject: ["revision": revision], options: [.sortedKeys]
        )
        try durableWrite(data, to: url)
    }

    /// Atomic temp-file-then-`rename(2)`, fsync'd before the rename and the
    /// directory fsync'd after - the same guarantee documented on
    /// `ReplicaStore.durableWrite`. Throws `ChangeJournalError.io` on any
    /// failure so the caller never advances the namespace on a write that did
    /// not reach stable storage.
    private func durableWrite(_ data: Data, to finalURL: URL) throws {
        let dir = finalURL.deletingLastPathComponent()
        try? fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        let tmpURL = dir.appendingPathComponent(".tmp-\(UUID().uuidString)")
        guard fileManager.createFile(atPath: tmpURL.path, contents: nil),
              let handle = try? FileHandle(forWritingTo: tmpURL) else {
            try? fileManager.removeItem(at: tmpURL)
            throw ChangeJournalError.io("не удалось создать временный файл журнала")
        }
        do {
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()
        } catch {
            try? handle.close()
            try? fileManager.removeItem(at: tmpURL)
            throw ChangeJournalError.io("не удалось записать/сбросить журнал: \(error)")
        }
        let renamed = tmpURL.path.withCString { t in
            finalURL.path.withCString { f in rename(t, f) }
        }
        if renamed != 0 {
            let savedErrno = errno
            try? fileManager.removeItem(at: tmpURL)
            throw ChangeJournalError.io("rename журнала не удался: \(String(cString: strerror(savedErrno)))")
        }
        fsyncDirectory(dir)
    }

    private func fsyncDirectory(_ url: URL) {
        let fd = open(url.path, O_RDONLY)
        guard fd >= 0 else { return }
        fsync(fd)
        close(fd)
    }
}
