import FileProvider
import Foundation

/// Recognizes a multi-item Finder access without making a single Finder open
/// download the whole clipboard generation. The second distinct viewer fetch
/// returns every remaining file exactly once; the extension asks File Provider
/// to materialize those identifiers concurrently. Once every file has entered
/// fetchContents, the generation is rearmed for a later paste of the same
/// clipboard contents.
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

        var seen = viewerIndices[transferId, default: []]
        seen.insert(index)
        viewerIndices[transferId] = seen

        if triggeredTransfers.contains(transferId) {
            let fileIndices = Set(entries.indices.filter { !entries[$0].isDirectory })
            if fileIndices.isSubset(of: seen) {
                triggeredTransfers.remove(transferId)
                viewerIndices[transferId] = nil
            }
            return []
        }

        guard seen.count >= 2 else { return [] }

        triggeredTransfers.insert(transferId)
        return entries.indices.compactMap { candidate in
            guard !seen.contains(candidate), !entries[candidate].isDirectory else { return nil }
            return DuoItemModel.entryIdentifier(transferId: transferId, index: candidate)
        }
    }
}
