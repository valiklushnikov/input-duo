import FileProvider
import Foundation

/// Shares one in-flight materialized-set snapshot across all cleanup jobs that
/// ask concurrently. A completed snapshot is never cached: the first caller of
/// the next wave always starts a fresh physical query.
final class MaterializedSetQueryCoalescer {
    typealias Completion = (Set<String>) -> Void
    typealias StartQuery = (@escaping Completion) -> Void

    private let startQuery: StartQuery
    private let perf: PerfTrace
    private let lock = NSLock()
    private var waiters: [Completion] = []
    private var queryInFlight = false

    init(perf: PerfTrace = .live, startQuery: @escaping StartQuery) {
        self.perf = perf
        self.startQuery = startQuery
    }

    func query(_ completion: @escaping Completion) {
        lock.lock()
        waiters.append(completion)
        if queryInFlight {
            let pendingCallers = waiters.count
            lock.unlock()
            perf.mark("materialized_query_coalesced", fields: [
                ("pending_callers", String(pendingCallers))
            ])
            return
        }
        queryInFlight = true
        lock.unlock()

        perf.mark("materialized_query_started", fields: [("pending_callers", "1")])
        startQuery { [weak self] materialized in
            self?.complete(materialized)
        }
    }

    private func complete(_ materialized: Set<String>) {
        lock.lock()
        let completions = waiters
        waiters.removeAll(keepingCapacity: true)
        queryInFlight = false
        lock.unlock()

        perf.mark("materialized_query_completed", fields: [
            ("callers", String(completions.count)),
            ("item_count", String(materialized.count))
        ])
        completions.forEach { $0(materialized) }
    }
}

/// Production `EvictionEnvironment` over a real `NSFileProviderManager`:
/// materialized truth via `enumeratorForMaterializedItems()` (paged), the
/// public `evictItem`, and a real deferred timer.
final class ManagerEvictionEnvironment: EvictionEnvironment {
    private let manager: NSFileProviderManager
    private let perf: PerfTrace
    private let timerQueue = DispatchQueue(label: "com.duoinput.fileprovider.cleanup.timer")
    private lazy var materializedQueries = MaterializedSetQueryCoalescer(perf: perf) { [weak self] completion in
        guard let self else { completion([]); return }
        self.startMaterializedQuery(completion)
    }

    init(manager: NSFileProviderManager, perf: PerfTrace = .live) {
        self.manager = manager
        self.perf = perf
    }

    func queryMaterialized(_ completion: @escaping (Set<String>) -> Void) {
        materializedQueries.query(completion)
    }

    private func startMaterializedQuery(_ completion: @escaping (Set<String>) -> Void) {
        let enumerator = manager.enumeratorForMaterializedItems()
        let observer = AllMaterializedObserver(enumerator: enumerator, completion: completion)
        enumerator.enumerateItems(for: observer,
                                  startingAt: NSFileProviderPage(NSFileProviderPage.initialPageSortedByName as Data))
    }

    func evict(_ itemIdentifier: String, completion: @escaping (Error?) -> Void) {
        manager.evictItem(identifier: NSFileProviderItemIdentifier(itemIdentifier)) { error in
            completion(error)
        }
    }

    func schedule(after seconds: TimeInterval, _ work: @escaping () -> Void) {
        timerQueue.asyncAfter(deadline: .now() + seconds, execute: work)
    }
}

/// Collects every materialized item identifier across all pages.
private final class AllMaterializedObserver: NSObject, NSFileProviderEnumerationObserver {
    private var ids: Set<String> = []
    private let enumerator: NSFileProviderEnumerator
    private let completion: (Set<String>) -> Void
    private var settled = false

    init(enumerator: NSFileProviderEnumerator, completion: @escaping (Set<String>) -> Void) {
        self.enumerator = enumerator
        self.completion = completion
    }

    func didEnumerate(_ updatedItems: [any NSFileProviderItemProtocol]) {
        for item in updatedItems { ids.insert(item.itemIdentifier.rawValue) }
    }

    func finishEnumerating(upTo nextPage: NSFileProviderPage?) {
        if let nextPage {
            enumerator.enumerateItems(for: self, startingAt: nextPage)
        } else {
            settle()
        }
    }

    func finishEnumeratingWithError(_ error: any Error) { settle() }

    private func settle() {
        guard !settled else { return }
        settled = true
        completion(ids)
    }
}
