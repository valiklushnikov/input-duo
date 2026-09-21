import Foundation
import FileProvider

/// Signals the File Provider system to pull namespace changes. Injected so the
/// control service is unit-testable without a live `NSFileProviderManager`.
/// A successful signal is NOT a deletion acknowledgement - it only asks the
/// system to enumerate changes; whether/when it applies them is observed later
/// through `enumerateChanges` (see the design doc).
protocol EnumerationSignaling: AnyObject {
    func signalEnumerator(for container: NSFileProviderItemIdentifier)
}

/// Production adapter over `NSFileProviderManager.signalEnumerator(for:)`.
final class ManagerEnumerationSignal: EnumerationSignaling {
    private let manager: NSFileProviderManager
    init(manager: NSFileProviderManager) { self.manager = manager }
    func signalEnumerator(for container: NSFileProviderItemIdentifier) {
        manager.signalEnumerator(for: container) { _ in }
    }
}

/// Wraps a `ReplicaStore` + `ChangeJournal` and implements the
/// `DuoExtensionControl` XPC contract (`DuoFPProto.h`) - the object the host's
/// `publishGeneration:` / `retireGeneration:` / `deleteGeneration:` calls reach.
///
/// Kept separate from `FileProviderExtension` (the unrelated
/// `NSFileProviderReplicatedExtension`/`NSFileProviderServicing` surface) so the
/// two protocol surfaces never entangle.
///
/// ## ACK-ordering + durable-before-signal guarantee
/// Every method performs its durable writes FIRST (record write, then the
/// journal change/tombstone), replies exactly once strictly after those writes
/// succeed, and only THEN signals the enumerators. If any durable write throws,
/// the reply is `(false, error)` and NO signal is emitted - File Provider is
/// never told about a change that did not reach stable storage. `reply(true,
/// ...)` is unreachable from a catch block.
///
/// `deleteGeneration` TOMBSTONES rather than physically deleting the backing
/// record: this phase keeps generation metadata durable indefinitely
/// (PHYSICAL_REPLICA_DELETE = DISABLED). The deletion is carried to the daemon
/// through the working-set change journal.
final class DuoExtensionControlService: NSObject, DuoExtensionControl {
    private let store: ReplicaStore
    private let journal: ChangeJournal
    private let signal: EnumerationSignaling?

    init(store: ReplicaStore, journal: ChangeJournal, signal: EnumerationSignaling? = nil) {
        self.store = store
        self.journal = journal
        self.signal = signal
        super.init()
    }

    func publishGeneration(_ recordJSON: Data, reply: @escaping (Bool, Error?) -> Void) {
        do {
            let record = try store.publish(recordJSON: recordJSON)
            try journal.append(kind: .update, itemIdentifiers: DuoItemModel.namespaceIdentifiers(of: record))
            reply(true, nil)
            signalWorkingSetAndRoot()
        } catch {
            reply(false, Self.mapError(error))
        }
    }

    func retireGeneration(_ generationId: String, reply: @escaping (Bool, Error?) -> Void) {
        do {
            try store.retire(generationId)
            reply(true, nil)
            // Retire only removes the generation from the root LIST; it stays in
            // the working-set LIST (ACTIVE and RETIRED both live there), so only
            // the root enumerator is signalled and the journal is untouched.
            signal?.signalEnumerator(for: .rootContainer)
        } catch {
            reply(false, Self.mapError(error))
        }
    }

    func deleteGeneration(_ generationId: String, reply: @escaping (Bool, Error?) -> Void) {
        do {
            guard let record = store.record(for: generationId) else {
                throw ReplicaStoreError.notFound(generationId)
            }
            // Tombstone: durable delete-changes + marker. The backing record is
            // intentionally KEPT (PHYSICAL_REPLICA_DELETE = DISABLED).
            try journal.recordTombstone(
                transferId: generationId,
                itemIdentifiers: DuoItemModel.namespaceIdentifiers(of: record)
            )
            reply(true, nil)
            signalWorkingSetAndRoot()
        } catch {
            reply(false, Self.mapError(error))
        }
    }

    /// Durable journal write already happened before this is called; a signal is
    /// only a request to enumerate, never a deletion ACK.
    private func signalWorkingSetAndRoot() {
        signal?.signalEnumerator(for: .workingSet)
        signal?.signalEnumerator(for: .rootContainer)
    }

    /// Maps a `ReplicaStoreError`/`ChangeJournalError` onto the shared
    /// `DuoFPErrorDomain` (see `Shared/DuoFPErrors.h`) using existing codes only.
    /// `.invalidRecord` -> `DuoFPErrorProtocol` (7); `.notFound` ->
    /// `DuoFPErrorSourceMissing` (1); `.io` (replica or journal durable-write
    /// failure) -> `DuoFPErrorDiskFull` (6). Numeric codes are referenced
    /// directly to avoid depending on NS_ERROR_ENUM's Swift import shape.
    private static func mapError(_ error: Error) -> NSError {
        if let nsError = error as NSError?, nsError.domain == DuoFPErrorDomain {
            return nsError
        }
        let code: Int
        switch error {
        case ReplicaStoreError.notFound:
            code = 1 // DuoFPErrorSourceMissing
        case ReplicaStoreError.io, ChangeJournalError.io:
            code = 6 // DuoFPErrorDiskFull
        default:
            code = 7 // DuoFPErrorProtocol
        }
        return NSError(
            domain: DuoFPErrorDomain,
            code: code,
            userInfo: [NSLocalizedDescriptionKey: "\(error)"]
        )
    }
}
