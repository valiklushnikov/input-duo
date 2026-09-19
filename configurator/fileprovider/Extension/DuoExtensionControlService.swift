import Foundation

/// Wraps a `ReplicaStore` and implements the `DuoExtensionControl` XPC
/// contract (`DuoFPProto.h`) - the object the host's `publishGeneration:` /
/// `retireGeneration:` / `deleteGeneration:` calls eventually reach.
///
/// Kept separate from `FileProviderExtension` (which conforms to the
/// unrelated `NSFileProviderReplicatedExtension`/`NSFileProviderServicing`
/// protocols) so the two protocol surfaces never entangle. Wiring this object
/// into a live `NSXPCConnection.exportedObject` is Task 7's job; here it is
/// unit-tested directly against a `ReplicaStore`.
///
/// ## ACK-ordering guarantee
/// Every method calls the corresponding `ReplicaStore` operation
/// synchronously and calls `reply` exactly once, strictly after that call
/// returns: `reply(true, nil)` only on the non-throwing path (i.e. only
/// after the durable write/rewrite/removal has completed), `reply(false,
/// error)` on any thrown error. `reply(true, ...)` is never reachable from a
/// catch block, and the store's own durability guarantee (see
/// `ReplicaStore` doc) means "returned without throwing" and "durably
/// persisted" are the same event.
final class DuoExtensionControlService: NSObject, DuoExtensionControl {
    private let store: ReplicaStore

    init(store: ReplicaStore) {
        self.store = store
        super.init()
    }

    func publishGeneration(_ recordJSON: Data, reply: @escaping (Bool, Error?) -> Void) {
        do {
            try store.publish(recordJSON: recordJSON)
            reply(true, nil)
        } catch {
            reply(false, Self.mapError(error))
        }
    }

    func retireGeneration(_ generationId: String, reply: @escaping (Bool, Error?) -> Void) {
        do {
            try store.retire(generationId)
            reply(true, nil)
        } catch {
            reply(false, Self.mapError(error))
        }
    }

    func deleteGeneration(_ generationId: String, reply: @escaping (Bool, Error?) -> Void) {
        do {
            try store.delete(generationId)
            reply(true, nil)
        } catch {
            reply(false, Self.mapError(error))
        }
    }

    /// Maps a `ReplicaStoreError` onto the shared `DuoFPErrorDomain` (see
    /// `Shared/DuoFPErrors.h`) using existing codes only - no new codes are
    /// introduced here. `.invalidRecord` (a bad/unparseable record) maps to
    /// `DuoFPErrorProtocol` (7); `.notFound` (retire/delete of an id with no
    /// replica) maps to `DuoFPErrorSourceMissing` (1), since the thing that's
    /// missing is precisely the durable source record; `.io` (create/write/
    /// fsync/rename failure - see `ReplicaStore.durableWrite`/`delete`) maps
    /// to `DuoFPErrorDiskFull` (6) - task-14 brief ruling #3(b) fixes this
    /// from the `DuoFPErrorProtocol` (7) fallback it used to fall through to:
    /// a durable-write failure on this store is overwhelmingly a full disk in
    /// the extension's own container, not a protocol violation, and Python's
    /// receiving end (`_xpc_error`) has a specific, more useful code for
    /// exactly that. The numeric codes are referenced directly (not through
    /// the Swift-bridged `DuoFPError` enum case names) to avoid depending on
    /// NS_ERROR_ENUM's exact Swift import shape.
    private static func mapError(_ error: Error) -> NSError {
        if let nsError = error as NSError?, nsError.domain == DuoFPErrorDomain {
            return nsError
        }
        let code: Int
        switch error {
        case ReplicaStoreError.notFound:
            code = 1 // DuoFPErrorSourceMissing
        case ReplicaStoreError.io:
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
