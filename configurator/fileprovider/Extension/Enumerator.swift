import FileProvider
import os

/// Task 17: correlation-id log markers for enumeration. Only ids/counts are
/// logged - never a filename/path and never manifest content - mirroring the
/// privacy invariant `FetchController.swift`'s `fetchLog` documents.
private let enumeratorLog = Logger(subsystem: "com.duoinput.configurator.fileprovider", category: "enumerate")

/// Enumerates one container's children, reconstructed purely from
/// `ReplicaStore` (Task 4) - no network, no host, no XPC. One instance per
/// identifier the framework asks about, per
/// `NSFileProviderReplicatedExtension.enumerator(for:)`:
///   - `.rootContainer` -> one item per active generation (the generation
///     containers).
///   - a generation container id (`"<transfer_id>"`) -> that generation's
///     top-level manifest entries (`parentPath == ""`).
///   - a directory entry id (`"<transfer_id>:<index>"` where the entry is a
///     directory) -> that directory's immediate children.
///
/// The whole tree is reconstructed in memory per call, so there is nothing
/// to page: everything is returned in a single page and
/// `enumerateChanges` reports no changes (Task 4's replica has no
/// generation/version history to diff against yet; a later task drives real
/// change tracking off `publish`/`retire`).
final class DuoEnumerator: NSObject, NSFileProviderEnumerator {
    private let enumeratedItemIdentifier: NSFileProviderItemIdentifier
    private let store: ReplicaStore

    init(enumeratedItemIdentifier: NSFileProviderItemIdentifier, store: ReplicaStore) {
        self.enumeratedItemIdentifier = enumeratedItemIdentifier
        self.store = store
        super.init()
    }

    func invalidate() {}

    func enumerateItems(for observer: NSFileProviderEnumerationObserver, startingAt page: NSFileProviderPage) {
        let items = children()
        let parsed = DuoItemModel.parse(enumeratedItemIdentifier)
        enumeratorLog.info("fp_enumerate transfer_id=\(parsed?.transferId ?? "root", privacy: .public) entry_index=\(parsed?.index ?? -1, privacy: .public) count=\(items.count, privacy: .public)")
        observer.didEnumerate(items)
        observer.finishEnumerating(upTo: nil)
    }

    func enumerateChanges(for observer: NSFileProviderChangeObserver, from anchor: NSFileProviderSyncAnchor) {
        observer.finishEnumeratingChanges(upTo: anchor, moreComing: false)
    }

    func currentSyncAnchor(completionHandler: @escaping (NSFileProviderSyncAnchor?) -> Void) {
        completionHandler(NSFileProviderSyncAnchor(Data("v1".utf8)))
    }

    // MARK: - Internals

    private func children() -> [NSFileProviderItem] {
        if enumeratedItemIdentifier == .rootContainer {
            return store.allActive().map { DuoItemFactory.containerItem(for: $0) }
        }

        guard let parsed = DuoItemModel.parse(enumeratedItemIdentifier),
              let record = store.record(for: parsed.transferId), record.isActive else {
            return []
        }

        let entries = DuoItemModel.entries(in: record)
        let parentPath: String
        if let index = parsed.index {
            guard entries.indices.contains(index), entries[index].isDirectory else { return [] }
            parentPath = entries[index].path
        } else {
            // Enumerating the generation container itself: its children are
            // the manifest's top-level entries.
            parentPath = ""
        }

        return entries.indices
            .filter { entries[$0].parentPath == parentPath }
            .compactMap { DuoItemFactory.item(for: record, index: $0) }
    }
}
