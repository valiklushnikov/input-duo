import FileProvider
import Foundation

/// Turns genuine multi-item Finder demand into small, non-recursive
/// materialization waves. Requests created by a wave are explicitly tracked so
/// their fetch callbacks can never be mistaken for new Finder demand.
final class FinderBurstDownloadCoordinator {
    static let burstWaveSize = 8

    enum FetchOrigin: String {
        case finder = "FINDER"
        case burst = "BURST"
    }

    struct Download: Equatable {
        let identifier: NSFileProviderItemIdentifier
        let index: Int
        let waveId: Int
        let wavePosition: Int
        let triggerItem: String
    }

    struct FetchContext {
        let transferId: String
        let itemIdentifier: String
        let index: Int
        let origin: FetchOrigin
        let waveId: Int?
        let wavePosition: Int?
        let triggerItem: String?
        let duplicateOfActiveFetch: Bool
    }

    struct Decision {
        let context: FetchContext
        let downloads: [Download]
    }

    private enum RequestState { case scheduled, issued }

    private struct Assignment {
        let download: Download
        var state: RequestState
    }

    private struct TransferState {
        var fileIndices: Set<Int> = []
        var seenIndices: Set<Int> = []
        var genuineIndices: Set<Int> = []
        var assignments: [Int: Assignment] = [:]
        var activeBurst: [Int: Download] = [:]
        var activeFetchCounts: [Int: Int] = [:]
        var nextWaveId = 1
        var openedWaveCount = 0
    }

    private let lock = NSLock()
    private var transfers: [String: TransferState] = [:]

    func beginFetch(
        transferId: String,
        index: Int,
        entries: [DuoManifestEntry],
        isFileViewerRequest: Bool
    ) -> Decision {
        lock.lock()
        defer { lock.unlock() }

        var state = transfers[transferId, default: TransferState()]
        state.fileIndices = Set(entries.indices.filter { !entries[$0].isDirectory })
        let itemIdentifier = DuoItemModel.entryIdentifier(
            transferId: transferId, index: index
        ).rawValue
        let alreadyActive = (state.activeFetchCounts[index] ?? 0) > 0
        var origin: FetchOrigin = .finder
        var wave: Download?
        var isGenuineDemand = isFileViewerRequest

        if let assignment = state.assignments[index] {
            state.assignments[index] = nil
            if assignment.state == .issued {
                origin = .burst
                wave = assignment.download
                state.activeBurst[index] = assignment.download
                isGenuineDemand = false
            }
            // If only scheduled, no internal requestDownload call existed yet:
            // genuine Finder demand takes ownership and the later claim fails.
        } else if state.activeBurst[index] != nil {
            // File Provider independently delivered a second callback while the
            // burst fetch is active. It must not advance the burst frontier.
            isGenuineDemand = false
        }

        state.seenIndices.insert(index)
        state.activeFetchCounts[index, default: 0] += 1

        var downloads: [Download] = []
        if origin == .finder, isGenuineDemand, !alreadyActive,
           state.assignments.isEmpty, state.activeBurst.isEmpty {
            state.genuineIndices.insert(index)
            let mayOpen = state.openedWaveCount > 0 || state.genuineIndices.count >= 2
            if mayOpen {
                let candidates = entries.indices.filter { candidate in
                    candidate > index
                        && !entries[candidate].isDirectory
                        && !state.seenIndices.contains(candidate)
                        && state.assignments[candidate] == nil
                        && state.activeBurst[candidate] == nil
                }.prefix(Self.burstWaveSize)
                if !candidates.isEmpty {
                    let waveId = state.nextWaveId
                    state.nextWaveId += 1
                    state.openedWaveCount += 1
                    downloads = candidates.enumerated().map { position, candidate in
                        Download(
                            identifier: DuoItemModel.entryIdentifier(
                                transferId: transferId, index: candidate
                            ),
                            index: candidate,
                            waveId: waveId,
                            wavePosition: position + 1,
                            triggerItem: itemIdentifier
                        )
                    }
                    for download in downloads {
                        state.assignments[download.index] = Assignment(
                            download: download, state: .scheduled
                        )
                    }
                }
            }
        }

        transfers[transferId] = state
        return Decision(
            context: FetchContext(
                transferId: transferId,
                itemIdentifier: itemIdentifier,
                index: index,
                origin: origin,
                waveId: wave?.waveId,
                wavePosition: wave?.wavePosition,
                triggerItem: wave?.triggerItem,
                duplicateOfActiveFetch: alreadyActive
            ),
            downloads: downloads
        )
    }

    /// Claim immediately before requestDownloadForItem. False means genuine
    /// Finder demand already took ownership, so a speculative request would be
    /// duplicate work.
    func markBurstRequestIssued(_ download: Download) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let parsed = DuoItemModel.parse(download.identifier),
              var state = transfers[parsed.transferId],
              var assignment = state.assignments[download.index],
              assignment.download == download,
              assignment.state == .scheduled else { return false }
        assignment.state = .issued
        state.assignments[download.index] = assignment
        transfers[parsed.transferId] = state
        return true
    }

    func burstRequestFailed(_ download: Download) {
        lock.lock()
        defer { lock.unlock() }
        guard let parsed = DuoItemModel.parse(download.identifier),
              var state = transfers[parsed.transferId],
              state.assignments[download.index]?.download == download else { return }
        state.assignments[download.index] = nil
        transfers[parsed.transferId] = state
    }

    func completeFetch(_ context: FetchContext) {
        lock.lock()
        defer { lock.unlock() }
        guard var state = transfers[context.transferId] else { return }
        if let count = state.activeFetchCounts[context.index] {
            if count <= 1 {
                state.activeFetchCounts[context.index] = nil
            } else {
                state.activeFetchCounts[context.index] = count - 1
            }
        }
        if context.origin == .burst,
           state.activeBurst[context.index]?.waveId == context.waveId {
            state.activeBurst[context.index] = nil
        }
        if state.assignments.isEmpty,
           state.activeBurst.isEmpty,
           state.activeFetchCounts.isEmpty,
           state.fileIndices.isSubset(of: state.seenIndices) {
            transfers[context.transferId] = nil
        } else {
            transfers[context.transferId] = state
        }
    }

    func waveCount(transferId: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return transfers[transferId]?.openedWaveCount ?? 0
    }

    /// Compatibility wrapper for the previous API. Production uses the
    /// explicit begin/claim/complete lifecycle above.
    func downloadsAfterFetch(
        transferId: String,
        index: Int,
        entries: [DuoManifestEntry],
        isFileViewerRequest: Bool
    ) -> [NSFileProviderItemIdentifier] {
        let decision = beginFetch(
            transferId: transferId, index: index, entries: entries,
            isFileViewerRequest: isFileViewerRequest
        )
        for download in decision.downloads { _ = markBurstRequestIssued(download) }
        completeFetch(decision.context)
        return decision.downloads.map(\.identifier)
    }
}
