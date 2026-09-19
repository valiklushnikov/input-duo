import FileProvider
import UniformTypeIdentifiers

/// Minimal root container item. Fixed content: the root itself is never a
/// replica-derived entry (its children - the generation containers - are;
/// see `ItemModel.swift`/`Enumerator.swift`), so a static version is
/// correct and deterministic by construction.
final class RootItem: NSObject, NSFileProviderItem {
    var itemIdentifier: NSFileProviderItemIdentifier { .rootContainer }
    var parentItemIdentifier: NSFileProviderItemIdentifier { .rootContainer }
    var filename: String { "Duo Input" }
    var contentType: UTType { .folder }
    var capabilities: NSFileProviderItemCapabilities { [.allowsContentEnumerating] }
    var itemVersion: NSFileProviderItemVersion {
        NSFileProviderItemVersion(contentVersion: Data("root".utf8),
                                  metadataVersion: Data("root".utf8))
    }
}
