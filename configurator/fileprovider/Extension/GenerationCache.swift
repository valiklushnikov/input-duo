import Foundation

/// A generation record together with its decoded manifest (entries + directory
/// map) - everything needed to build any of its `DuoItem`s.
struct DecodedGeneration {
    let record: GenerationRecord
    let manifest: DuoDecodedManifest
}

/// What the working set and the prefetch gate need about a generation, without
/// keeping its (possibly 40k-entry) manifest alive.
struct GenerationSummary: Equatable {
    let transferId: String
    let isActive: Bool
    let entryCount: Int
}

/// Process-wide memo of decoded generation records, shared by `item(for:)`,
/// `fetchContents`, the prefetch gate and every enumerator.
///
/// fileproviderd resolves items one `item(for:)` at a time; re-reading and
/// re-decoding a 40k-entry record per request made that O(N^2) and pinned the
/// extension at ~100% CPU after a large paste. Entries are keyed by the record
/// file's `RecordFileSignature` (re-checked with one `stat` per lookup), so a
/// publish, retire or GC through any path is observed on the next call - the
/// cache never needs explicit invalidation and never serves stale bytes.
///
/// Decoded generations are LRU-bounded (`capacity`); summaries are tiny and
/// kept for every live record.
final class GenerationCache {
    private let store: ReplicaStore
    private let capacity: Int
    private let lock = NSLock()

    private struct DecodedEntry {
        let signature: RecordFileSignature
        let generation: DecodedGeneration
    }

    private struct SummaryEntry {
        let signature: RecordFileSignature
        let summary: GenerationSummary
    }

    private var decodedById: [String: DecodedEntry] = [:]
    /// Most recently used last.
    private var recency: [String] = []
    private var summaryById: [String: SummaryEntry] = [:]

    init(store: ReplicaStore, capacity: Int = 3) {
        self.store = store
        self.capacity = max(1, capacity)
    }

    /// The decoded record for `transferId` (active or retired), or nil if it
    /// does not exist or fails to parse.
    func decoded(for transferId: String) -> DecodedGeneration? {
        guard let signature = store.recordSignature(for: transferId) else {
            forget(transferId)
            return nil
        }
        lock.lock()
        if let entry = decodedById[transferId], entry.signature == signature {
            touch(transferId)
            lock.unlock()
            return entry.generation
        }
        lock.unlock()

        // Decode outside the lock: other queues keep resolving cached
        // generations meanwhile. The signature was taken BEFORE the read, so a
        // rewrite racing this read is caught by the next lookup's `stat`.
        guard let record = store.record(for: transferId) else {
            forget(transferId)
            return nil
        }
        let generation = DecodedGeneration(record: record, manifest: DuoDecodedManifest(record: record))
        lock.lock()
        decodedById[transferId] = DecodedEntry(signature: signature, generation: generation)
        touch(transferId)
        while recency.count > capacity {
            decodedById.removeValue(forKey: recency.removeFirst())
        }
        summaryById[transferId] = SummaryEntry(signature: signature, summary: Self.summary(of: generation))
        lock.unlock()
        return generation
    }

    func record(for transferId: String) -> GenerationRecord? {
        decoded(for: transferId)?.record
    }

    /// Summary of one generation, decoding nothing when it is already known.
    func summary(for transferId: String) -> GenerationSummary? {
        guard let signature = store.recordSignature(for: transferId) else {
            forget(transferId)
            return nil
        }
        lock.lock()
        if let entry = summaryById[transferId], entry.signature == signature {
            lock.unlock()
            return entry.summary
        }
        lock.unlock()
        guard let record = store.record(for: transferId) else {
            forget(transferId)
            return nil
        }
        let summary = GenerationSummary(
            transferId: record.transferId,
            isActive: record.isActive,
            entryCount: DuoItemModel.entryCount(in: record)
        )
        lock.lock()
        summaryById[transferId] = SummaryEntry(signature: signature, summary: summary)
        lock.unlock()
        return summary
    }

    /// Summaries of every parseable live record (active AND retired), the set
    /// `ReplicaStore.allRecords()` would return.
    func summaries() -> [GenerationSummary] {
        let ids = store.generationIds()
        lock.lock()
        let live = Set(ids)
        summaryById = summaryById.filter { live.contains($0.key) }
        lock.unlock()
        return ids.compactMap { summary(for: $0) }
    }

    private func forget(_ transferId: String) {
        lock.lock()
        decodedById.removeValue(forKey: transferId)
        recency.removeAll { $0 == transferId }
        summaryById.removeValue(forKey: transferId)
        lock.unlock()
    }

    /// Caller holds `lock`.
    private func touch(_ transferId: String) {
        recency.removeAll { $0 == transferId }
        recency.append(transferId)
    }

    private static func summary(of generation: DecodedGeneration) -> GenerationSummary {
        GenerationSummary(
            transferId: generation.record.transferId,
            isActive: generation.record.isActive,
            entryCount: generation.manifest.entries.count
        )
    }
}
