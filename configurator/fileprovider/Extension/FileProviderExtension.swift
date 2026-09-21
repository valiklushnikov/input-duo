import FileProvider
import UniformTypeIdentifiers

/// Production File Provider extension for Duo Input (lazy Windows→macOS transfer).
///
/// Conforms to `NSFileProviderReplicatedExtension` **and**
/// `NSFileProviderServicing` (the latter is mandatory — without it the framework
/// never routes `getService` to `supportedServiceSources(...)` and returns nil),
/// and vends an anonymous `NSXPCListener` through `DuoServiceSource`.
/// Enumeration and authenticated publication share one replica; contents are
/// fetched lazily through the host's per-fetch scheduler.
final class FileProviderExtension: NSObject, NSFileProviderReplicatedExtension, NSFileProviderServicing {
    private let domain: NSFileProviderDomain
    private let manager: NSFileProviderManager?
    private let replicaStore: ReplicaStore
    /// Durable namespace-change log, co-located with the replica store. Drives
    /// the working-set sync anchor and deletion reconciliation.
    private let changeJournal: ChangeJournal
    /// Strong reference: the service source owns the anonymous listener.
    private let serviceSource: DuoServiceSource
    private let fetchController: FetchController
    /// Post-fetch cache cleanup (materialize→grace→evict→verify). nil only when
    /// no manager is available (tests / degraded host).
    private let cleanup: EvictionCoordinator?

    required convenience init(domain: NSFileProviderDomain) {
        self.init(domain: domain, replicaStore: ReplicaStore())
    }

    /// Test seam: production always goes through `init(domain:)` above,
    /// which uses `ReplicaStore`'s real sandboxed default base directory.
    /// Unit tests inject a temp-directory-backed store instead, so
    /// constructing this class in-process never touches the real
    /// `~/Library` (the test bundle itself is not sandboxed).
    init(domain: NSFileProviderDomain, replicaStore: ReplicaStore,
         hostProvider: FetchController.HostProvider? = nil,
         temporaryDirectory: URL? = nil) {
        self.domain = domain
        let manager = NSFileProviderManager(for: domain)
        self.manager = manager
        self.replicaStore = replicaStore
        self.changeJournal = ChangeJournal(baseDirectory: replicaStore.baseDirectory)
        let source = DuoServiceSource(store: replicaStore)
        self.serviceSource = source
        // The URL handed to fetchContents's completion MUST live on the same
        // volume as the manager's temporaryDirectoryURL(), so the system can
        // CLONE it into the dataless item instead of copying the whole file - a
        // second full-size pass (e.g. 4 GB) that otherwise stretches the Finder
        // "Preparing to copy" phase. Fall back to an injected dir (tests) or the
        // process temp dir only when the manager has none.
        let tempDir = temporaryDirectory
            ?? (try? manager?.temporaryDirectoryURL())
            ?? FileManager.default.temporaryDirectory
        self.fetchController = FetchController(hostProvider: hostProvider ?? { source.hostProxy(errorHandler: $0) },
                                               temporaryDirectory: tempDir)
        self.cleanup = manager.map { EvictionCoordinator(environment: ManagerEvictionEnvironment(manager: $0)) }
        super.init()
    }

    func invalidate() {
        cleanup?.shutdown()
        serviceSource.invalidate()
    }

    // MARK: - NSFileProviderServicing (mandatory)

    func supportedServiceSources(
        for itemIdentifier: NSFileProviderItemIdentifier,
        completionHandler: @escaping ([NSFileProviderServiceSource]?, Error?) -> Void
    ) -> Progress {
        completionHandler([serviceSource], nil)
        return Progress()
    }

    // MARK: - NSFileProviderReplicatedExtension

    func item(
        for identifier: NSFileProviderItemIdentifier,
        request: NSFileProviderRequest,
        completionHandler: @escaping (NSFileProviderItem?, Error?) -> Void
    ) -> Progress {
        let noSuchItem = NSError(domain: NSFileProviderErrorDomain, code: NSFileProviderError.noSuchItem.rawValue)

        if identifier == .rootContainer {
            completionHandler(RootItem(), nil)
            return Progress()
        }

        // Resolve on record EXISTENCE, not on `isActive`. A retired (and, later,
        // tombstoned) generation stays resolvable by itemIdentifier - the record
        // is still durable, so serving it is strictly safer than a spurious
        // noSuchItem (-1005) that the daemon turns into Finder -36. Namespace
        // deletion is propagated through the working-set change channel, never by
        // failing a direct request. The ONLY -1005 is a genuinely absent record.
        guard let parsed = DuoItemModel.parse(identifier),
              let record = replicaStore.record(for: parsed.transferId) else {
            completionHandler(nil, noSuchItem)
            return Progress()
        }

        if let index = parsed.index {
            if let item = DuoItemFactory.item(for: record, index: index) {
                completionHandler(item, nil)
            } else {
                completionHandler(nil, noSuchItem)
            }
        } else {
            completionHandler(DuoItemFactory.containerItem(for: record), nil)
        }
        return Progress()
    }

    func fetchContents(
        for itemIdentifier: NSFileProviderItemIdentifier,
        version requestedVersion: NSFileProviderItemVersion?,
        request: NSFileProviderRequest,
        completionHandler: @escaping (URL?, NSFileProviderItem?, Error?) -> Void
    ) -> Progress {
        // Resolve on record EXISTENCE, not `isActive` (see item(for:)): a
        // retired/tombstoned generation stays fetchable by itemIdentifier while
        // its durable record exists. The only noSuchItem is a genuinely absent
        // record.
        guard let parsed = DuoItemModel.parse(itemIdentifier), let index = parsed.index,
              let record = replicaStore.record(for: parsed.transferId),
              let item = DuoItemFactory.item(for: record, index: index), item.documentSize != nil,
              requestedVersion == nil || requestedVersion == item.itemVersion else {
            completionHandler(nil, nil, NSError(domain: NSFileProviderErrorDomain, code: NSFileProviderError.noSuchItem.rawValue))
            return Progress()
        }
        // Post-fetch cache cleanup: fetchContents completion means "content
        // supplied to File Provider", NOT "materialized into the mount" - the
        // system clones the temp into the mount asynchronously ~after
        // completion. So we only SCHEDULE cleanup; the coordinator waits for the
        // exact item to appear in the materialized set, then (grace) evicts and
        // verifies. Never evict at completion (proven no-op race).
        let cleanup = self.cleanup
        let transferId = parsed.transferId
        let scheduling: (URL?, NSFileProviderItem?, Error?) -> Void = { url, fetchedItem, error in
            completionHandler(url, fetchedItem, error)
            guard error == nil else { return }
            cleanup?.schedule(itemIdentifier: itemIdentifier.rawValue, transferId: transferId)
        }
        return fetchController.fetch(item, request: request, completion: scheduling)
    }

    func enumerator(
        for containerItemIdentifier: NSFileProviderItemIdentifier,
        request: NSFileProviderRequest
    ) throws -> NSFileProviderEnumerator {
        return DuoEnumerator(enumeratedItemIdentifier: containerItemIdentifier, store: replicaStore, journal: changeJournal)
    }

    // MARK: - Mutating operations: read-only backend, all unsupported.

    func createItem(
        basedOn itemTemplate: NSFileProviderItem,
        fields: NSFileProviderItemFields,
        contents url: URL?,
        options: NSFileProviderCreateItemOptions = [],
        request: NSFileProviderRequest,
        completionHandler: @escaping (NSFileProviderItem?, NSFileProviderItemFields, Bool, Error?) -> Void
    ) -> Progress {
        completionHandler(nil, [], false, NSError(domain: NSCocoaErrorDomain, code: NSFeatureUnsupportedError))
        return Progress()
    }

    func modifyItem(
        _ item: NSFileProviderItem,
        baseVersion version: NSFileProviderItemVersion,
        changedFields: NSFileProviderItemFields,
        contents newContents: URL?,
        options: NSFileProviderModifyItemOptions = [],
        request: NSFileProviderRequest,
        completionHandler: @escaping (NSFileProviderItem?, NSFileProviderItemFields, Bool, Error?) -> Void
    ) -> Progress {
        completionHandler(nil, [], false, NSError(domain: NSCocoaErrorDomain, code: NSFeatureUnsupportedError))
        return Progress()
    }

    func deleteItem(
        identifier: NSFileProviderItemIdentifier,
        baseVersion version: NSFileProviderItemVersion,
        options: NSFileProviderDeleteItemOptions = [],
        request: NSFileProviderRequest,
        completionHandler: @escaping (Error?) -> Void
    ) -> Progress {
        completionHandler(NSError(domain: NSCocoaErrorDomain, code: NSFeatureUnsupportedError))
        return Progress()
    }
}
