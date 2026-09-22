import FileProvider
import Foundation

/// Recognizes a multi-item Finder access without making a single Finder open
/// download the whole clipboard generation. The second distinct viewer fetch
/// returns every remaining file exactly once; the extension asks File Provider
/// to materialize those identifiers concurrently.
final class FinderBurstDownloadCoordinator {
    private let lock = NSLock()
    private var viewerIndices: [String: Set<Int>] = [:]
    private var triggeredTransfers: Set<String> = []

    func downloadsAfterFetch(
        transferId: String,
        index: Int,
        entries: [DuoManifestEntry],
        isFileViewerRequest: Bool
    ) -> [NSFileProviderItemIdentifier] {
        guard isFileViewerRequest else { return [] }

        lock.lock()
        defer { lock.unlock() }
        guard !triggeredTransfers.contains(transferId) else { return [] }

        var seen = viewerIndices[transferId, default: []]
        seen.insert(index)
        viewerIndices[transferId] = seen
        guard seen.count >= 2 else { return [] }

        triggeredTransfers.insert(transferId)
        viewerIndices[transferId] = nil
        return entries.indices.compactMap { candidate in
            guard !seen.contains(candidate), !entries[candidate].isDirectory else { return nil }
            return DuoItemModel.entryIdentifier(transferId: transferId, index: candidate)
        }
    }
}
