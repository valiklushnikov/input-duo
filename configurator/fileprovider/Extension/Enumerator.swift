import FileProvider
import os

/// Task 17: correlation-id log markers for enumeration. Only ids/counts are
/// logged - never a filename/path and never manifest content - mirroring the
/// privacy invariant `FetchController.swift`'s `fetchLog` documents.
private let enumeratorLog = Logger(subsystem: "com.duoinput.configurator.fileprovider", category: "enumerate")

/// Enumerates one container's children, reconstructed purely from
/// `ReplicaStore` (Task 4), and drives change/anchor tracking from the durable
/// `ChangeJournal` (generation-lifetime fix). One instance per identifier the
/// framework asks about, per `NSFileProviderReplicatedExtension.enumerator(for:)`:
///   - `.rootContainer` -> one item per ACTIVE generation (the generation
///     containers). Retired/tombstoned generations are hidden from the root LIST.
///   - `.workingSet` -> all namespace-live items (ACTIVE + RETIRED, minus
///     tombstoned): container + entries. This is the sole carrier of the
///     journal-backed change enumeration through which deletions reach the
///     daemon (`didDeleteItemsWithIdentifiers`).
///   - a generation container id (`"<transfer_id>"`) -> that generation's
///     top-level manifest entries (`parentPath == ""`).
///   - a directory entry id (`"<transfer_id>:<index>"` where the entry is a
///     directory) -> that directory's immediate children.
///
/// The sync anchor is the journal head encoded as a big-endian `UInt64`;
/// `enumerateChanges(from:)` replays the durable journal since that revision.
final class DuoEnumerator: NSObject, NSFileProviderEnumerator {
    private let enumeratedItemIdentifier: NSFileProviderItemIdentifier
    private let store: ReplicaStore
    private let journal: ChangeJournal

    init(enumeratedItemIdentifier: NSFileProviderItemIdentifier, store: ReplicaStore, journal: ChangeJournal) {
        self.enumeratedItemIdentifier = enumeratedItemIdentifier
        self.store = store
        self.journal = journal
        super.init()
    }

    func invalidate() {}

    // MARK: - Sync anchor codec

    /// The sync anchor is an 8-byte big-endian journal revision. Anything that
    /// is not exactly 8 bytes (notably the legacy static `"v1"` anchor) decodes
    /// to nil and is treated as expired.
    static func encodeAnchor(_ revision: UInt64) -> NSFileProviderSyncAnchor {
        var be = revision.bigEndian
        return withUnsafeBytes(of: &be) { NSFileProviderSyncAnchor(Data($0)) }
    }

    static func decodeAnchor(_ anchor: NSFileProviderSyncAnchor) -> UInt64? {
        let data = anchor.rawValue
        guard data.count == 8 else { return nil }
        return data.withUnsafeBytes { $0.loadUnaligned(as: UInt64.self).bigEndian }
    }

    // MARK: - Item enumeration

    func enumerateItems(for observer: NSFileProviderEnumerationObserver, startingAt page: NSFileProviderPage) {
        let items = children()
        let parsed = DuoItemModel.parse(enumeratedItemIdentifier)
        enumeratorLog.info("fp_enumerate transfer_id=\(parsed?.transferId ?? "root", privacy: .public) entry_index=\(parsed?.index ?? -1, privacy: .public) count=\(items.count, privacy: .public)")
        observer.didEnumerate(items)
        observer.finishEnumerating(upTo: nil)
    }

    // MARK: - Change enumeration (journal-backed)

    func enumerateChanges(for observer: NSFileProviderChangeObserver, from anchor: NSFileProviderSyncAnchor) {
        guard let from = DuoEnumerator.decodeAnchor(anchor) else {
            // Legacy/unknown anchor (e.g. the old "v1"): force one clean full
            // resync via enumerateItems. No changes are applied against it.
            observer.finishEnumeratingWithError(
                NSError(domain: NSFileProviderErrorDomain, code: NSFileProviderError.syncAnchorExpired.rawValue)
            )
            return
        }
        journal.noteObserved(from)
        let changes = journal.changes(after: from)
        for change in changes {
            switch change.kind {
            case .update:
                let items = change.itemIdentifiers.compactMap { resolveItem(for: $0) }
                if !items.isEmpty { observer.didUpdate(items) }
            case .delete:
                observer.didDeleteItems(
                    withIdentifiers: change.itemIdentifiers.map { NSFileProviderItemIdentifier($0) }
                )
            }
        }
        let head = journal.headRevision()
        enumeratorLog.info("fp_enumerate_changes from=\(from, privacy: .public) head=\(head, privacy: .public) count=\(changes.count, privacy: .public)")
        observer.finishEnumeratingChanges(upTo: DuoEnumerator.encodeAnchor(head), moreComing: false)
    }

    func currentSyncAnchor(completionHandler: @escaping (NSFileProviderSyncAnchor?) -> Void) {
        completionHandler(DuoEnumerator.encodeAnchor(journal.headRevision()))
    }

    // MARK: - Internals

    /// Resolves a `DuoItem` for a journal `update` change's identifier from the
    /// current record (nil if the record is gone or the identifier is a bare
    /// container - a container update carries no fetchable item here).
    private func resolveItem(for rawIdentifier: String) -> NSFileProviderItem? {
        guard let parsed = DuoItemModel.parse(NSFileProviderItemIdentifier(rawIdentifier)),
              let record = store.record(for: parsed.transferId) else { return nil }
        if let index = parsed.index {
            return DuoItemFactory.item(for: record, index: index)
        }
        return DuoItemFactory.containerItem(for: record)
    }

    private func children() -> [NSFileProviderItem] {
        if enumeratedItemIdentifier == .rootContainer {
            return store.allActive().map { DuoItemFactory.containerItem(for: $0) }
        }

        if enumeratedItemIdentifier == .workingSet {
            return workingSetItems()
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

    /// Working-set LIST: every namespace-live item = ACTIVE + RETIRED
    /// generations (minus tombstoned), flattened to container + all entries.
    /// Small volume by design (metadata only), so no materialized-only
    /// optimisation.
    private func workingSetItems() -> [NSFileProviderItem] {
        let tombstoned = journal.tombstonedTransferIds()
        var items: [NSFileProviderItem] = []
        for record in store.allRecords() where !tombstoned.contains(record.transferId) {
            items.append(DuoItemFactory.containerItem(for: record))
            let entries = DuoItemModel.entries(in: record)
            for index in entries.indices {
                if let item = DuoItemFactory.item(for: record, index: index) {
                    items.append(item)
                }
            }
        }
        return items
    }
}
