import FileProvider
import Foundation

/// Turns genuine multi-item Finder demand into a bounded ROLLING prefetch of the
/// following items of the same generation.
///
/// Why the bounds exist (Phase H, replacing the Finder-gated fixed waves):
/// - Recursion guard (kept): fetch callbacks caused by our own
///   `requestDownloadForItem` are tracked and can never count as Finder demand,
///   so they can neither open prefetch nor extend its demand lease. Otherwise
///   every prefetched item would open more prefetch - a chain reaction that
///   materializes a whole generation nobody asked for.
/// - Opening still requires two distinct genuine Finder requests, so opening a
///   single file (Quick Look / double-click) never starts speculation.
/// - Horizon: at most `prefetchHorizon` coordinator-owned requests are
///   outstanding (scheduled/issued but not yet fetched, or fetching). A
///   completed prefetch replenishes the horizon without waiting for another
///   Finder request - the old fixed waves left the transport idle for one
///   Finder step (~0.67 s) at every wave boundary (Phase G).
/// - Demand lease: Finder's cancel of a copy is invisible to the extension
///   (fileproviderd does not cancel fetchContents), so replenishment is only
///   allowed within `demandLease` seconds of the last genuine Finder request
///   for the generation. After an unobservable cancel, speculative work stops
///   within one lease; during a live paste Finder's next dataless hit renews it.
/// - Observable stops: a cancelled fetch of the generation, a failed
///   coordinator-owned fetch or request, or a generation that is no longer
///   current stop replenishment until new genuine Finder demand.
final class FinderBurstDownloadCoordinator {
    /// Production bound on outstanding coordinator-owned prefetch requests.
    /// Independent of the host FILE_READ budget (8) and per-file window (4).
    static let productionPrefetchHorizon = 8
    /// Kept for callers/telemetry that still name the bound a "wave size".
    static let productionPrefetchWaveSize = productionPrefetchHorizon
    /// Replenishment is allowed only this long after the last genuine Finder
    /// request of the generation (bounds work after an invisible Finder cancel).
    static let productionDemandLeaseSeconds: TimeInterval = 10
    /// An issued request whose fetch never arrives (item already materialized,
    /// or dropped by fileproviderd) releases its horizon slot after this long.
    static let productionIssuedRequestExpirySeconds: TimeInterval = 10

    enum FetchOrigin: String {
        case finder = "FINDER"
        case burst = "BURST"
    }

    enum FetchClassification: String {
        case knownPrefetchRequested = "KNOWN_PREFETCH_REQUESTED"
        case noPrefetchRequestRecorded = "NO_PREFETCH_REQUEST_RECORDED"
        case ambiguous = "AMBIGUOUS"
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
        let classification: FetchClassification
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
        var issuedAt: TimeInterval?
    }

    private struct TransferState {
        var entries: [DuoManifestEntry] = []
        var fileIndices: Set<Int> = []
        var seenIndices: Set<Int> = []
        var genuineIndices: Set<Int> = []
        var assignments: [Int: Assignment] = [:]
        var activeBurst: [Int: Download] = [:]
        /// Issued requests that expired without a fetch: a late fetch for one of
        /// these is still ours (never genuine Finder demand).
        var expiredRequests: [Int: Download] = [:]
        var activeFetchCounts: [Int: Int] = [:]
        var nextWaveId = 1
        var rollingWaveId: Int?
        var rollingPosition = 0
        var frontier = -1
        var lastGenuineDemandAt: TimeInterval?
        var maxOutstanding = 0

        var outstanding: Int { assignments.count + activeBurst.count }
    }

    private let lock = NSLock()
    private let prefetchHorizon: Int
    private let demandLease: TimeInterval
    private let issuedExpiry: TimeInterval
    private let now: () -> TimeInterval
    private let isGenerationCurrent: (String) -> Bool
    private var transfers: [String: TransferState] = [:]

    init(burstWaveSize: Int = FinderBurstDownloadCoordinator.productionPrefetchHorizon,
         demandLease: TimeInterval = FinderBurstDownloadCoordinator.productionDemandLeaseSeconds,
         issuedExpiry: TimeInterval = FinderBurstDownloadCoordinator.productionIssuedRequestExpirySeconds,
         now: @escaping () -> TimeInterval = { Double(DispatchTime.now().uptimeNanoseconds) / 1e9 },
         isGenerationCurrent: @escaping (String) -> Bool = { _ in true }) {
        precondition(burstWaveSize >= 0)
        self.prefetchHorizon = burstWaveSize
        self.demandLease = demandLease
        self.issuedExpiry = issuedExpiry
        self.now = now
        self.isGenerationCurrent = isGenerationCurrent
    }

    func beginFetch(
        transferId: String,
        index: Int,
        entries: [DuoManifestEntry],
        isFileViewerRequest: Bool
    ) -> Decision {
        lock.lock()
        defer { lock.unlock() }

        var state = transfers[transferId, default: TransferState()]
        state.entries = entries
        state.fileIndices = Set(entries.indices.filter { !entries[$0].isDirectory })
        let itemIdentifier = DuoItemModel.entryIdentifier(
            transferId: transferId, index: index
        ).rawValue
        let alreadyActive = (state.activeFetchCounts[index] ?? 0) > 0
        var origin: FetchOrigin = .finder
        var classification: FetchClassification = .noPrefetchRequestRecorded
        var wave: Download?
        var isGenuineDemand = isFileViewerRequest

        if let assignment = state.assignments[index] {
            state.assignments[index] = nil
            if assignment.state == .issued {
                origin = .burst
                classification = .knownPrefetchRequested
                wave = assignment.download
                state.activeBurst[index] = assignment.download
            }
            // Scheduled-but-not-issued: Finder won the race and owns this fetch;
            // the later claim fails, so no duplicate request is issued. Either
            // way the item was already inside our horizon: not new demand.
            isGenuineDemand = false
        } else if let expired = state.expiredRequests.removeValue(forKey: index) {
            // Late fetch of a request whose horizon slot already expired: ours.
            origin = .burst
            classification = .knownPrefetchRequested
            wave = expired
            state.activeBurst[index] = expired
            isGenuineDemand = false
        } else if state.activeBurst[index] != nil {
            // File Provider independently delivered a second callback while the
            // burst fetch is active. It must not count as new demand.
            isGenuineDemand = false
            classification = .ambiguous
        }

        state.seenIndices.insert(index)
        state.activeFetchCounts[index, default: 0] += 1

        var downloads: [Download] = []
        if origin == .finder, isGenuineDemand, !alreadyActive {
            state.genuineIndices.insert(index)
            state.lastGenuineDemandAt = now()
            state.frontier = max(state.frontier, index)
            let mayOpen = state.rollingWaveId != nil || state.genuineIndices.count >= 2
            if mayOpen, isGenerationCurrent(transferId) {
                if state.rollingWaveId == nil {
                    state.rollingWaveId = state.nextWaveId
                    state.nextWaveId += 1
                    state.rollingPosition = 0
                }
                downloads = fill(&state, transferId: transferId, trigger: itemIdentifier)
            }
        } else if origin == .finder, !alreadyActive, mayReplenish(state, transferId: transferId) {
            // Finder took over an item already inside the horizon (race): its
            // slot is free again, so keep the rolling horizon full. This is not
            // new demand - it neither opens prefetch nor renews the lease.
            downloads = fill(&state, transferId: transferId, trigger: itemIdentifier)
        }
        // A coordinator-owned (.burst) callback never produces downloads here:
        // replenishment happens only when a fetch completes (completeFetch).

        transfers[transferId] = state
        return Decision(
            context: FetchContext(
                transferId: transferId,
                itemIdentifier: itemIdentifier,
                index: index,
                origin: origin,
                classification: classification,
                waveId: wave?.waveId,
                wavePosition: wave?.wavePosition,
                triggerItem: wave?.triggerItem,
                duplicateOfActiveFetch: alreadyActive
            ),
            downloads: downloads
        )
    }

    /// Claim immediately before requestDownloadForItem. False means genuine
    /// Finder demand already took ownership (or prefetch was stopped), so a
    /// speculative request would be duplicate or unwanted work.
    func markBurstRequestIssued(_ download: Download) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let parsed = DuoItemModel.parse(download.identifier),
              var state = transfers[parsed.transferId],
              var assignment = state.assignments[download.index],
              assignment.download == download,
              assignment.state == .scheduled else { return false }
        assignment.state = .issued
        assignment.issuedAt = now()
        state.assignments[download.index] = assignment
        transfers[parsed.transferId] = state
        return true
    }

    /// requestDownloadForItem failed (or could not be issued): release the slot
    /// and stop replenishing - a failing request must never become a loop.
    func burstRequestFailed(_ download: Download) {
        lock.lock()
        defer { lock.unlock() }
        guard let parsed = DuoItemModel.parse(download.identifier),
              var state = transfers[parsed.transferId],
              state.assignments[download.index]?.download == download else { return }
        state.assignments[download.index] = nil
        stop(&state)
        transfers[parsed.transferId] = state
    }

    /// Settle one fetch. Returns the downloads that replenish the horizon (to be
    /// issued asynchronously by the caller); empty when nothing may be issued.
    @discardableResult
    func completeFetch(_ context: FetchContext, error: Error? = nil) -> [Download] {
        lock.lock()
        defer { lock.unlock() }
        guard var state = transfers[context.transferId] else { return [] }
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
        if let error {
            let ns = error as NSError
            let cancelled = ns.domain == NSCocoaErrorDomain && ns.code == NSUserCancelledError
            if cancelled || context.origin == .burst {
                stop(&state)
            }
        }
        var downloads: [Download] = []
        if error == nil, mayReplenish(state, transferId: context.transferId) {
            downloads = fill(&state, transferId: context.transferId, trigger: context.itemIdentifier)
        }
        if state.assignments.isEmpty,
           state.activeBurst.isEmpty,
           state.activeFetchCounts.isEmpty,
           state.fileIndices.isSubset(of: state.seenIndices) {
            transfers[context.transferId] = nil
        } else {
            transfers[context.transferId] = state
        }
        return downloads
    }

    /// Test/telemetry: current coordinator-owned outstanding requests.
    func outstandingCount(transferId: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return transfers[transferId]?.outstanding ?? 0
    }

    /// Test/telemetry: highest outstanding count ever observed for a transfer.
    func maxOutstanding(transferId: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return transfers[transferId]?.maxOutstanding ?? 0
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

    // MARK: - private (lock held)

    private func stop(_ state: inout TransferState) {
        state.rollingWaveId = nil
        // Not-yet-issued slots are dropped: nothing was requested for them.
        state.assignments = state.assignments.filter { $0.value.state == .issued }
    }

    private func mayReplenish(_ state: TransferState, transferId: String) -> Bool {
        guard state.rollingWaveId != nil,
              let last = state.lastGenuineDemandAt,
              now() - last <= demandLease else { return false }
        return isGenerationCurrent(transferId)
    }

    private func fill(_ state: inout TransferState, transferId: String, trigger: String) -> [Download] {
        guard let waveId = state.rollingWaveId else { return [] }
        let t = now()
        for (index, assignment) in state.assignments
        where assignment.state == .issued && t - (assignment.issuedAt ?? t) > issuedExpiry {
            state.assignments[index] = nil
            state.expiredRequests[index] = assignment.download
        }
        let free = prefetchHorizon - state.outstanding
        guard free > 0 else { return [] }
        var candidates: [Int] = []
        for candidate in state.entries.indices where candidates.count < free {
            if candidate > state.frontier
                && !state.entries[candidate].isDirectory
                && !state.seenIndices.contains(candidate)
                && state.assignments[candidate] == nil
                && state.activeBurst[candidate] == nil
                && state.expiredRequests[candidate] == nil {
                candidates.append(candidate)
            }
        }
        var downloads: [Download] = []
        for candidate in candidates {
            state.rollingPosition += 1
            let download = Download(
                identifier: DuoItemModel.entryIdentifier(transferId: transferId, index: candidate),
                index: candidate,
                waveId: waveId,
                wavePosition: state.rollingPosition,
                triggerItem: trigger
            )
            state.assignments[candidate] = Assignment(download: download, state: .scheduled, issuedAt: nil)
            downloads.append(download)
        }
        state.maxOutstanding = max(state.maxOutstanding, state.outstanding)
        return downloads
    }
}
