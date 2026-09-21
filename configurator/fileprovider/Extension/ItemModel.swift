import FileProvider
import UniformTypeIdentifiers

/// One manifest entry, decoded from `GenerationRecord.manifest["entries"]`
/// (see `ReplicaStore.swift` for the manifest shape). `index` is the entry's
/// position in that array - it IS the identity suffix
/// (`"<transfer_id>:<index>"`), so entries are never re-sorted or filtered
/// before identifiers are assigned.
struct DuoManifestEntry {
    let path: String
    let kind: String
    let size: Int64
    let mtimeNs: Int64

    static let directoryKind = "directory"

    var isDirectory: Bool { kind == DuoManifestEntry.directoryKind }

    /// Last POSIX path component: `"f.txt"` from `"dir/sub/f.txt"`.
    var name: String { DuoManifestEntry.posixName(of: path) }

    /// Containing POSIX path: `"dir/sub"` from `"dir/sub/f.txt"`; `""` for a
    /// top-level entry (parent is the generation container, not another
    /// entry).
    var parentPath: String { DuoManifestEntry.posixParent(of: path) }

    static func posixName(of path: String) -> String {
        let comps = path.split(separator: "/", omittingEmptySubsequences: true)
        return comps.last.map(String.init) ?? path
    }

    static func posixParent(of path: String) -> String {
        var comps = path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard !comps.isEmpty else { return "" }
        comps.removeLast()
        return comps.joined(separator: "/")
    }
}

/// Pure mapping from a `GenerationRecord` (Task 4's replica) to the
/// identifiers/versions the domain namespace is built from. Everything here
/// is a deterministic function of the record's own bytes - no clocks, no
/// randomness, no filesystem access beyond what `ReplicaStore` already did.
enum DuoItemModel {
    /// Bumped only when the capability/flag mapping itself changes, so a
    /// capability-only fix still invalidates cached `itemVersion`s.
    static let capsRev = 1

    static func entries(in record: GenerationRecord) -> [DuoManifestEntry] {
        guard let raw = record.manifest["entries"] as? [[String: Any]] else { return [] }
        return raw.map { dict in
            DuoManifestEntry(
                path: dict["path"] as? String ?? "",
                kind: dict["kind"] as? String ?? "file",
                size: (dict["size"] as? NSNumber)?.int64Value ?? 0,
                mtimeNs: (dict["mtime_ns"] as? NSNumber)?.int64Value ?? 0
            )
        }
    }

    static func entryIdentifier(transferId: String, index: Int) -> NSFileProviderItemIdentifier {
        NSFileProviderItemIdentifier("\(transferId):\(index)")
    }

    static func containerIdentifier(transferId: String) -> NSFileProviderItemIdentifier {
        NSFileProviderItemIdentifier(transferId)
    }

    /// Every File Provider identifier a generation contributes to the namespace:
    /// its container plus one per manifest entry. This is exactly the set the
    /// change journal records on publish (`update`) and on tombstone (`delete`),
    /// so the daemon learns about every item the generation ever exposed.
    static func namespaceIdentifiers(of record: GenerationRecord) -> [String] {
        var ids = [containerIdentifier(transferId: record.transferId).rawValue]
        let entries = entries(in: record)
        for index in entries.indices {
            ids.append(entryIdentifier(transferId: record.transferId, index: index).rawValue)
        }
        return ids
    }

    /// Parses `"<transfer_id>:<index>"` (entry) or `"<transfer_id>"`
    /// (generation container) back into its parts. Returns `nil` only for
    /// malformed identifiers (non-numeric suffix) - `.rootContainer` is
    /// handled separately by callers, never passed here.
    static func parse(_ identifier: NSFileProviderItemIdentifier) -> (transferId: String, index: Int?)? {
        let raw = identifier.rawValue
        guard let colon = raw.firstIndex(of: ":") else {
            return (raw, nil)
        }
        let transferId = String(raw[..<colon])
        let indexPart = String(raw[raw.index(after: colon)...])
        guard let index = Int(indexPart) else { return nil }
        return (transferId, index)
    }

    /// Resolves the parent item identifier for `entries[index]` by matching
    /// its POSIX parent path against the record's own entry set (matching a
    /// directory entry by `path`, per the identity-scheme ruling) - never by
    /// re-deriving a synthetic path-based id.
    static func parentIdentifier(
        transferId: String,
        entries: [DuoManifestEntry],
        index: Int
    ) -> NSFileProviderItemIdentifier {
        let parentPath = entries[index].parentPath
        if parentPath.isEmpty {
            return containerIdentifier(transferId: transferId)
        }
        if let parentIndex = entries.firstIndex(where: { $0.isDirectory && $0.path == parentPath }) {
            return entryIdentifier(transferId: transferId, index: parentIndex)
        }
        // Defensive: manifest referenced a parent directory that isn't
        // itself an entry. Attach to the generation container rather than
        // dropping the item from the tree.
        return containerIdentifier(transferId: transferId)
    }

    static func contentVersion(size: Int64, mtimeNs: Int64) -> Data {
        Data("\(size):\(mtimeNs)".utf8)
    }

    static func metadataVersion(name: String, size: Int64, mtimeNs: Int64) -> Data {
        Data("\(name):\(size):\(mtimeNs):\(capsRev)".utf8)
    }

    static func contentType(forFilename filename: String, isDirectory: Bool) -> UTType {
        if isDirectory { return .folder }
        let ext = (filename as NSString).pathExtension
        if !ext.isEmpty, let type = UTType(filenameExtension: ext) {
            return type
        }
        return .data
    }
}

/// Read-only `NSFileProviderItem` reconstructed purely from a replica
/// record. A `final class: NSObject` (not the `struct` shorthand in the task
/// brief's interface sketch) because `NSFileProviderItem` refines
/// `NSObjectProtocol`, which a plain Swift struct cannot conform to without
/// hand-writing `isEqual`/`hash`/`superclass`/etc.; `RootItem` in
/// `ScaffoldItems.swift` already establishes this same pattern.
final class DuoItem: NSObject, NSFileProviderItem {
    let itemIdentifier: NSFileProviderItemIdentifier
    let parentItemIdentifier: NSFileProviderItemIdentifier
    let filename: String
    let contentType: UTType
    let documentSize: NSNumber?
    let capabilities: NSFileProviderItemCapabilities
    private let contentVersionData: Data
    private let metadataVersionData: Data

    var itemVersion: NSFileProviderItemVersion {
        NSFileProviderItemVersion(contentVersion: contentVersionData, metadataVersion: metadataVersionData)
    }

    /// Expresses mode 0600 / no-`uchg` ONLY through this API value - never
    /// via `chmod`/`chflags`, per Global Constraints.
    var fileSystemFlags: NSFileProviderFileSystemFlags { [.userReadable, .userWritable] }

    init(
        itemIdentifier: NSFileProviderItemIdentifier,
        parentItemIdentifier: NSFileProviderItemIdentifier,
        filename: String,
        contentType: UTType,
        documentSize: NSNumber?,
        capabilities: NSFileProviderItemCapabilities,
        contentVersion: Data,
        metadataVersion: Data
    ) {
        self.itemIdentifier = itemIdentifier
        self.parentItemIdentifier = parentItemIdentifier
        self.filename = filename
        self.contentType = contentType
        self.documentSize = documentSize
        self.capabilities = capabilities
        self.contentVersionData = contentVersion
        self.metadataVersionData = metadataVersion
        super.init()
    }
}

/// Builds `DuoItem`s from a `GenerationRecord`. This is the ONLY place that
/// decides capabilities/contentType/version-string shape, so every call
/// site (the extension's `item(for:)` and the enumerator) stays consistent.
enum DuoItemFactory {
    // `.allowsEvicting` marks the materialized blob purgeable so post-fetch
    // cache cleanup can dehydrate it; without it `evictItem` fails with -2008
    // (NSFileProviderErrorNonEvictable). `.allowsWriting` is retained (Phase 9.6:
    // makes user copies mutable / kills `uchg`); no other capability is added.
    private static let fileCapabilities: NSFileProviderItemCapabilities = [.allowsReading, .allowsWriting, .allowsEvicting]
    private static let directoryCapabilities: NSFileProviderItemCapabilities = [
        .allowsReading, .allowsWriting, .allowsContentEnumerating,
    ]

    /// The generation container: the top-level folder Finder shows for one
    /// transfer. Its own id is the bare `transfer_id` (ruling #1); its
    /// filename is the `transfer_id` too, since the manifest carries no
    /// other human-facing name for the generation as a whole.
    static func containerItem(for record: GenerationRecord) -> DuoItem {
        let transferId = record.transferId
        let totalBytes = (record.manifest["total_bytes"] as? NSNumber)?.int64Value ?? 0
        return DuoItem(
            itemIdentifier: DuoItemModel.containerIdentifier(transferId: transferId),
            parentItemIdentifier: .rootContainer,
            filename: transferId,
            contentType: .folder,
            documentSize: nil,
            capabilities: directoryCapabilities,
            contentVersion: DuoItemModel.contentVersion(size: totalBytes, mtimeNs: record.createdNs),
            metadataVersion: DuoItemModel.metadataVersion(name: transferId, size: totalBytes, mtimeNs: record.createdNs)
        )
    }

    /// The entry at `index` in `record.manifest["entries"]`, or `nil` if
    /// `index` is out of range.
    static func item(for record: GenerationRecord, index: Int) -> DuoItem? {
        let entries = DuoItemModel.entries(in: record)
        guard entries.indices.contains(index) else { return nil }
        let entry = entries[index]
        return DuoItem(
            itemIdentifier: DuoItemModel.entryIdentifier(transferId: record.transferId, index: index),
            parentItemIdentifier: DuoItemModel.parentIdentifier(
                transferId: record.transferId, entries: entries, index: index
            ),
            filename: entry.name,
            contentType: DuoItemModel.contentType(forFilename: entry.name, isDirectory: entry.isDirectory),
            documentSize: entry.isDirectory ? nil : NSNumber(value: entry.size),
            capabilities: entry.isDirectory ? directoryCapabilities : fileCapabilities,
            contentVersion: DuoItemModel.contentVersion(size: entry.size, mtimeNs: entry.mtimeNs),
            metadataVersion: DuoItemModel.metadataVersion(name: entry.name, size: entry.size, mtimeNs: entry.mtimeNs)
        )
    }
}
