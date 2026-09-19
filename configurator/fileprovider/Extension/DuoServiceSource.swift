import Foundation
import FileProvider

/// The XPC service name the host uses with `getServiceWithName:`. It is an NSXPC
/// service *label*, not a Mach service registration — the endpoint is anonymous
/// and delivered by the File Provider infrastructure (no App Group, no named
/// Mach service, no temporary-exception).
let duoFileProviderServiceName = NSFileProviderServiceName("com.duoinput.configurator.fileprovider.xpc")

/// Vends an anonymous `NSXPCListener` endpoint through `NSFileProviderServiceSource`.
///
/// Task 1 scaffold: it proves the service source exists and hands back an
/// anonymous endpoint. Peer authentication (`SecCodeCheckValidity`) and the
/// exported `DuoExtensionControl` object are added in later tasks, so this
/// scaffold rejects connections (there is nothing to talk about yet).
final class DuoServiceSource: NSObject, NSFileProviderServiceSource, NSXPCListenerDelegate {
    var serviceName: NSFileProviderServiceName { duoFileProviderServiceName }

    private var listener: NSXPCListener?

    func makeListenerEndpoint() throws -> NSXPCListenerEndpoint {
        let anonymous = NSXPCListener.anonymous()
        anonymous.delegate = self
        anonymous.resume()
        listener = anonymous
        return anonymous.endpoint
    }

    func listener(
        _ listener: NSXPCListener,
        shouldAcceptNewConnection newConnection: NSXPCConnection
    ) -> Bool {
        // Peer-auth + exported interface land in Task 2/Task 3. Until the XPC
        // contract exists there is no exported object to offer, so refuse.
        return false
    }
}
