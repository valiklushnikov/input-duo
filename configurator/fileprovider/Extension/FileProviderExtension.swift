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
    /// Strong reference: the service source owns the anonymous listener.
    private let serviceSource: DuoServiceSource
    private let fetchController: FetchController

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
         temporaryDirectory: URL = FileManager.default.temporaryDirectory) {
        self.domain = domain
        self.manager = NSFileProviderManager(for: domain)
        self.replicaStore = replicaStore
        let source = DuoServiceSource(store: replicaStore)
        self.serviceSource = source
        self.fetchController = FetchController(hostProvider: hostProvider ?? { source.hostProxy(errorHandler: $0) },
                                               temporaryDirectory: temporaryDirectory)
        super.init()
    }

    func invalidate() { serviceSource.invalidate() }

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

        guard let parsed = DuoItemModel.parse(identifier),
              let record = replicaStore.record(for: parsed.transferId), record.isActive else {
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
        guard let parsed = DuoItemModel.parse(itemIdentifier), let index = parsed.index,
              let record = replicaStore.record(for: parsed.transferId), record.isActive,
              let item = DuoItemFactory.item(for: record, index: index), item.documentSize != nil,
              requestedVersion == nil || requestedVersion == item.itemVersion else {
            completionHandler(nil, nil, NSError(domain: NSFileProviderErrorDomain, code: NSFileProviderError.noSuchItem.rawValue))
            return Progress()
        }
        return fetchController.fetch(item, request: request, completion: completionHandler)
    }

    func enumerator(
        for containerItemIdentifier: NSFileProviderItemIdentifier,
        request: NSFileProviderRequest
    ) throws -> NSFileProviderEnumerator {
        return DuoEnumerator(enumeratedItemIdentifier: containerItemIdentifier, store: replicaStore)
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
