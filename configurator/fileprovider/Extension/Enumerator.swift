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
    private let generations: GenerationCache

    private struct WorkingSetCandidate {
        let identifier: String
        let transferId: String
        let entryIndex: Int?
    }

    /// `generations` is the extension's shared cache; standalone construction
    /// (tests) gets a private one over the same store.
    init(
        enumeratedItemIdentifier: NSFileProviderItemIdentifier,
        store: ReplicaStore,
        journal: ChangeJournal,
        generations: GenerationCache? = nil
    ) {
        self.enumeratedItemIdentifier = enumeratedItemIdentifier
        self.store = store
        self.journal = journal
        self.generations = generations ?? GenerationCache(store: store)
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

    /// Mid-change cursor: when a batch boundary falls inside one journal
    /// change, the anchor is 16 bytes - the last FULLY delivered revision plus
    /// how many identifiers of the next change were already delivered. A
    /// boundary on a change edge stays the plain 8-byte revision anchor, so a
    /// finished walk always ends at the plain head anchor.
    static func encodeChangeCursor(revision: UInt64, offset: Int) -> NSFileProviderSyncAnchor {
        guard offset > 0 else { return encodeAnchor(revision) }
        var be = (revision.bigEndian, UInt64(offset).bigEndian)
        return withUnsafeBytes(of: &be) { NSFileProviderSyncAnchor(Data($0)) }
    }

    static func decodeChangeCursor(_ anchor: NSFileProviderSyncAnchor) -> (revision: UInt64, offset: Int)? {
        let data = anchor.rawValue
        if let revision = decodeAnchor(anchor) { return (revision, 0) }
        guard data.count == 16 else { return nil }
        let (revision, offset) = data.withUnsafeBytes {
            ($0.loadUnaligned(fromByteOffset: 0, as: UInt64.self).bigEndian,
             $0.loadUnaligned(fromByteOffset: 8, as: UInt64.self).bigEndian)
        }
        guard offset > 0, offset <= UInt64(Int.max) else { return nil }
        return (revision, Int(offset))
    }

    /// Batch bound when the observer does not advertise `suggestedBatchSize`.
    /// fileproviderd ABORTS the extension (`__FILEPROVIDER_OBSERVER_TOO_MANY_ITEMS__`)
    /// on an oversized batch, so a change is never replayed in one shot.
    static let defaultChangeBatchSize = 200

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
        var cache = ResolvedRecordCache()
        let items: [NSFileProviderItem] = selected.compactMap { candidate in
            guard let generation = cache.resolve(candidate.transferId, generations: generations) else { return nil }
            guard let entryIndex = candidate.entryIndex else {
                return DuoItemFactory.containerItem(for: generation.record)
            }
            return DuoItemFactory.item(
                for: generation.record,
                manifest: generation.manifest,
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
        var skip = 0
        if let decoded = DuoEnumerator.decodeChangeCursor(anchor) {
            from = decoded.revision
            skip = decoded.offset
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
        let batchSize = max(1, observer.suggestedBatchSize ?? DuoEnumerator.defaultChangeBatchSize)
        // A publish journals every identifier of its generation in one change,
        // so the record and its manifest are decoded once per generation for
        // the whole replay - never once per identifier (that was O(N^2) and
        // piled autoreleased bridging garbage on the serial FPX queue).
        var cache = ResolvedRecordCache()
        var budget = batchSize
        var delivered = from
        var stoppedAt: (revision: UInt64, offset: Int)?
        for (position, change) in changes.enumerated() {
            let start = position == 0 ? min(skip, change.itemIdentifiers.count) : 0
            let end = min(change.itemIdentifiers.count, start + budget)
            let slice = change.itemIdentifiers[start..<end]
            autoreleasepool {
                switch change.kind {
                case .update:
                    let items = slice.compactMap { resolveItem(for: $0, cache: &cache) }
                    if !items.isEmpty { observer.didUpdate(items) }
                case .delete:
                    if !slice.isEmpty {
                        observer.didDeleteItems(withIdentifiers: slice.map { NSFileProviderItemIdentifier($0) })
                    }
                }
            }
            budget -= slice.count
            if end < change.itemIdentifiers.count {
                stoppedAt = (delivered, end)
                break
            }
            delivered = change.revision
            if budget == 0 {
                if position < changes.count - 1 { stoppedAt = (delivered, 0) }
                break
            }
        }
        let head = journal.headRevision()
        enumeratorLog.info("fp_enumerate_changes from=\(from, privacy: .public) skip=\(skip, privacy: .public) head=\(head, privacy: .public) count=\(changes.count, privacy: .public) batch=\(batchSize, privacy: .public) more=\(stoppedAt != nil, privacy: .public)")
        if let stop = stoppedAt {
            observer.finishEnumeratingChanges(
                upTo: DuoEnumerator.encodeChangeCursor(revision: stop.revision, offset: stop.offset),
                moreComing: true
            )
        } else {
            // Finish at the last revision actually replayed, not a head read
            // after listing: a change appended in between must not be skipped.
            let finishedAt = changes.isEmpty ? max(from, head) : delivered
            observer.finishEnumeratingChanges(upTo: DuoEnumerator.encodeAnchor(finishedAt), moreComing: false)
        }
    }

    func currentSyncAnchor(completionHandler: @escaping (NSFileProviderSyncAnchor?) -> Void) {
        completionHandler(DuoEnumerator.encodeAnchor(journal.headRevision()))
    }

    // MARK: - Internals

    /// Resolves a `DuoItem` for a journal `update` change's identifier from the
    /// current record (nil if the record is gone or the identifier is a bare
    /// container - a container update carries no fetchable item here).
    private func resolveItem(for rawIdentifier: String, cache: inout ResolvedRecordCache) -> NSFileProviderItem? {
        guard let parsed = DuoItemModel.parse(NSFileProviderItemIdentifier(rawIdentifier)),
              let resolved = cache.resolve(parsed.transferId, generations: generations) else { return nil }
        if let index = parsed.index {
            return DuoItemFactory.item(for: resolved.record, manifest: resolved.manifest, index: index)
        }
        return DuoItemFactory.containerItem(for: resolved.record)
    }

    /// Per-call memo over the shared `GenerationCache`, keyed by transfer id,
    /// so one batch pins one consistent decoded generation and does not `stat`
    /// the record file per identifier. A missing record is memoized too, so a
    /// change naming thousands of ids of a vanished generation is not re-read
    /// per id.
    private struct ResolvedRecordCache {
        private var byTransferId: [String: DecodedGeneration?] = [:]

        mutating func resolve(_ transferId: String, generations: GenerationCache) -> DecodedGeneration? {
            if let cached = byTransferId[transferId] { return cached }
            let resolved = generations.decoded(for: transferId)
            byTransferId[transferId] = .some(resolved)
            return resolved
        }
    }

    private func children() -> [NSFileProviderItem] {
        if enumeratedItemIdentifier == .rootContainer {
            return store.allActive().map { DuoItemFactory.containerItem(for: $0) }
        }

        guard let parsed = DuoItemModel.parse(enumeratedItemIdentifier),
              let generation = generations.decoded(for: parsed.transferId), generation.record.isActive else {
            return []
        }

        let record = generation.record
        let manifest = generation.manifest
        let entries = manifest.entries
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
            .compactMap { DuoItemFactory.item(for: record, manifest: manifest, index: $0) }
    }

    /// Lightweight working-set index. It preserves the exact namespace-live
    /// membership and deterministic identifier order while deferring expensive
    /// `NSFileProviderItem` construction until after the page has been selected.
    private func workingSetCandidates() -> [WorkingSetCandidate] {
        let tombstoned = journal.tombstonedTransferIds()
        var candidates: [WorkingSetCandidate] = []
        for summary in generations.summaries() where !tombstoned.contains(summary.transferId) {
            candidates.append(WorkingSetCandidate(
                identifier: DuoItemModel.containerIdentifier(transferId: summary.transferId).rawValue,
                transferId: summary.transferId,
                entryIndex: nil
            ))
            for index in 0..<summary.entryCount {
                candidates.append(WorkingSetCandidate(
                    identifier: DuoItemModel.entryIdentifier(
                        transferId: summary.transferId,
                        index: index
                    ).rawValue,
                    transferId: summary.transferId,
                    entryIndex: index
                ))
            }
        }
        return candidates.sorted { $0.identifier < $1.identifier }
    }
}
