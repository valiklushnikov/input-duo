import FileProvider
import Foundation

/// Production `EvictionEnvironment` over a real `NSFileProviderManager`:
/// materialized truth via `enumeratorForMaterializedItems()` (paged), the
/// public `evictItem`, and a real deferred timer.
final class ManagerEvictionEnvironment: EvictionEnvironment {
    private let manager: NSFileProviderManager
    private let timerQueue = DispatchQueue(label: "com.duoinput.fileprovider.cleanup.timer")

    init(manager: NSFileProviderManager) {
        self.manager = manager
    }

    func queryMaterialized(_ completion: @escaping (Set<String>) -> Void) {
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
