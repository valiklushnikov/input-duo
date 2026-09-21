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

    private struct WorkingSetCandidate {
        let identifier: String
        let record: GenerationRecord
        let entryIndex: Int?
    }

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

    /// The one KNOWN legacy anchor from before the journal existed (the old
    /// static `currentSyncAnchor`). It is soft-migrated to baseline revision 0,
    /// never expired - see `enumerateChanges`.
    static let legacyV1Anchor = Data("v1".utf8)

    static func isLegacyV1Anchor(_ anchor: NSFileProviderSyncAnchor) -> Bool {
        anchor.rawValue == legacyV1Anchor
    }

    // MARK: - Working-set pagination

    /// Conservative page size: never hand fileproviderd thousands of items in a
    /// single `didEnumerate`. A one-shot full-namespace working set saturated the
    /// extension session and starved getService (real E2E A regression).
    static let workingSetPageSize = 128

    /// Continuation-token prefix. The token is the LAST item identifier yielded
    /// on the previous page; the next page returns identifiers strictly greater
    /// (identifiers are sorted). This is deterministic and restart-safe (it lives
    /// in the page data, not in memory), and an initial/system page - which lacks
    /// this prefix - decodes to nil, i.e. start from the beginning.
    private static let workingSetPagePrefix = "wsp:"

    static func encodeWorkingSetPage(after identifier: String) -> NSFileProviderPage {
        NSFileProviderPage(Data((workingSetPagePrefix + identifier).utf8))
    }

    static func decodeWorkingSetPage(_ page: NSFileProviderPage) -> String? {
        guard let text = String(data: page.rawValue, encoding: .utf8),
              text.hasPrefix(workingSetPagePrefix) else { return nil }
        return String(text.dropFirst(workingSetPagePrefix.count))
    }

    // MARK: - Item enumeration

    func enumerateItems(for observer: NSFileProviderEnumerationObserver, startingAt page: NSFileProviderPage) {
        // The working set can be large (all namespace-live items); paginate it so
        // no single callback overwhelms the session. Root/generation containers
        // are small and enumerated in one page as before.
        if enumeratedItemIdentifier == .workingSet {
            enumerateWorkingSetPage(for: observer, startingAt: page)
            return
        }
        let items = children()
        let parsed = DuoItemModel.parse(enumeratedItemIdentifier)
        enumeratorLog.info("fp_enumerate transfer_id=\(parsed?.transferId ?? "root", privacy: .public) entry_index=\(parsed?.index ?? -1, privacy: .public) count=\(items.count, privacy: .public)")
        observer.didEnumerate(items)
        observer.finishEnumerating(upTo: nil)
    }

    /// Emit one page (<= `workingSetPageSize`) of the working set, following the
    /// identifier cursor in `page`. Membership is unchanged (all ACTIVE+RETIRED,
    /// excluding tombstoned); only the delivery is paginated.
    private func enumerateWorkingSetPage(for observer: NSFileProviderEnumerationObserver, startingAt page: NSFileProviderPage) {
        let all = workingSetCandidates() // lightweight, sorted by identifier
        let startIndex: Int
        if let cursor = DuoEnumerator.decodeWorkingSetPage(page) {
            startIndex = all.firstIndex { $0.identifier > cursor } ?? all.count
        } else {
            startIndex = 0
        }
        let endIndex = min(startIndex + DuoEnumerator.workingSetPageSize, all.count)
        let selected = startIndex < endIndex ? all[startIndex..<endIndex] : all[all.endIndex..<all.endIndex]
        var decodedEntries: [String: [DuoManifestEntry]] = [:]
        let items: [NSFileProviderItem] = selected.compactMap { candidate in
            guard let entryIndex = candidate.entryIndex else {
                return DuoItemFactory.containerItem(for: candidate.record)
            }
            let entries: [DuoManifestEntry]
            if let cached = decodedEntries[candidate.record.transferId] {
                entries = cached
            } else {
                let decoded = DuoItemModel.entries(in: candidate.record)
                decodedEntries[candidate.record.transferId] = decoded
                entries = decoded
            }
            return DuoItemFactory.item(
                for: candidate.record,
                entries: entries,
                index: entryIndex
            )
        }
        enumeratorLog.info("fp_enumerate_workingset start=\(startIndex, privacy: .public) count=\(items.count, privacy: .public) total=\(all.count, privacy: .public)")
        observer.didEnumerate(items)
        if endIndex < all.count, let last = selected.last {
            observer.finishEnumerating(upTo: DuoEnumerator.encodeWorkingSetPage(after: last.identifier))
        } else {
            observer.finishEnumerating(upTo: nil)
        }
    }

    // MARK: - Change enumeration (journal-backed)

    func enumerateChanges(for observer: NSFileProviderChangeObserver, from anchor: NSFileProviderSyncAnchor) {
        let from: UInt64
        if let decoded = DuoEnumerator.decodeAnchor(anchor) {
            from = decoded
        } else if DuoEnumerator.isLegacyV1Anchor(anchor) {
            // SOFT MIGRATION of the KNOWN legacy "v1" anchor: treat it as baseline
            // revision 0, replay the journal, and finish at the current head - do
            // NOT return SyncAnchorExpired. Expiring it forced fileproviderd into
            // a full working-set resync over every persisted orphan identity,
            // saturating the extension session and starving getService (real E2E
            // A evidence). Only the journal (small) is replayed; no full snapshot.
            from = 0
            enumeratorLog.info("fp_legacy_anchor_migrated from=v1 to=\(self.journal.headRevision(), privacy: .public)")
        } else {
            // Malformed/unknown anchor: explicit error per contract. Corruption
            // must never be silently masked as a migration to revision 0.
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

    /// Lightweight working-set index. It preserves the exact namespace-live
    /// membership and deterministic identifier order while deferring expensive
    /// `NSFileProviderItem` construction until after the page has been selected.
    private func workingSetCandidates() -> [WorkingSetCandidate] {
        let tombstoned = journal.tombstonedTransferIds()
        var candidates: [WorkingSetCandidate] = []
        for record in store.allRecords() where !tombstoned.contains(record.transferId) {
            candidates.append(WorkingSetCandidate(
                identifier: DuoItemModel.containerIdentifier(transferId: record.transferId).rawValue,
                record: record,
                entryIndex: nil
            ))
            for index in 0..<DuoItemModel.entryCount(in: record) {
                candidates.append(WorkingSetCandidate(
                    identifier: DuoItemModel.entryIdentifier(
                        transferId: record.transferId,
                        index: index
                    ).rawValue,
                    record: record,
                    entryIndex: index
                ))
            }
        }
        return candidates.sorted { $0.identifier < $1.identifier }
    }
}
