import Foundation
import FileProvider
import os
import Security

private let serviceSourceLog = Logger(
    subsystem: "com.duoinput.configurator.fileprovider",
    category: "service"
)

/// The XPC service name the host uses with `getServiceWithName:`. It is an NSXPC
/// service *label*, not a Mach service registration — the endpoint is anonymous
/// and delivered by the File Provider infrastructure (no App Group, no named
/// Mach service, no temporary-exception).
let duoFileProviderServiceName = NSFileProviderServiceName("com.duoinput.configurator.fileprovider.xpc")

/// The Security check gates acceptance; XPC then enforces the same requirement
/// on every message, closing the race inherent in a PID-only initial lookup.
enum DuoPeerVerifier {
    static func verify(_ connection: NSXPCConnection) -> Bool {
        guard let team = Bundle.main.object(forInfoDictionaryKey: "DuoSigningTeamIdentifier") as? String,
              !team.isEmpty, team.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber) }) else { return false }
        let text = "anchor apple generic and certificate leaf[subject.OU] = \"\(team)\""
        var requirement: SecRequirement?
        guard SecRequirementCreateWithString(text as CFString, [], &requirement) == errSecSuccess,
              let requirement else { return false }
        var code: SecCode?
        let attributes = [kSecGuestAttributePid: NSNumber(value: connection.processIdentifier)] as CFDictionary
        guard SecCodeCopyGuestWithAttributes(nil, attributes, [], &code) == errSecSuccess,
              let code, SecCodeCheckValidity(code, [], requirement) == errSecSuccess else { return false }
        connection.setCodeSigningRequirement(text)
        return true
    }
}

/// Owns the authenticated connection and the control service for the SAME store
/// the extension enumerates. Tests inject only the peer-verification decision.
final class DuoServiceSource: NSObject, NSFileProviderServiceSource, NSXPCListenerDelegate {
    var serviceName: NSFileProviderServiceName { duoFileProviderServiceName }

    private let control: DuoExtensionControlService
    private let peerVerifier: (NSXPCConnection) -> Bool
    private let lock = NSLock()
    private var listener: NSXPCListener?
    private var connection: NSXPCConnection?

    init(store: ReplicaStore, journal: ChangeJournal, signal: EnumerationSignaling? = nil,
         peerVerifier: @escaping (NSXPCConnection) -> Bool = DuoPeerVerifier.verify) {
        serviceSourceLog.info(
            "fp_service_source_init_enter thread=\(Thread.current.description, privacy: .public) timestamp=\(Date().timeIntervalSince1970, privacy: .public)"
        )
        serviceSourceLog.info("fp_service_dependency_ready dependency=ReplicaStore")
        serviceSourceLog.info("fp_service_dependency_ready dependency=ChangeJournal")
        serviceSourceLog.info("fp_service_dependency_ready dependency=ManagerEnumerationSignal present=\(signal != nil, privacy: .public)")
        control = DuoExtensionControlService(store: store, journal: journal, signal: signal)
        self.peerVerifier = peerVerifier
        super.init()
        serviceSourceLog.info(
            "fp_service_source_init_success thread=\(Thread.current.description, privacy: .public) timestamp=\(Date().timeIntervalSince1970, privacy: .public)"
        )
    }

    func makeListenerEndpoint() throws -> NSXPCListenerEndpoint {
        serviceSourceLog.info(
            "fp_service_endpoint_request thread=\(Thread.current.description, privacy: .public) timestamp=\(Date().timeIntervalSince1970, privacy: .public)"
        )
        lock.lock()
        defer { lock.unlock() }
        if let listener {
            let endpoint = listener.endpoint
            serviceSourceLog.info("fp_service_listener_reused")
            serviceSourceLog.info("fp_service_endpoint_created listener=persistent")
            serviceSourceLog.info("fp_service_endpoint_returned listener=persistent")
            return endpoint
        }
        let anonymous = NSXPCListener.anonymous()
        serviceSourceLog.info("fp_service_listener_created")
        anonymous.delegate = self
        anonymous.resume()
        serviceSourceLog.info("fp_service_listener_resumed")
        listener = anonymous
        let endpoint = anonymous.endpoint
        serviceSourceLog.info("fp_service_endpoint_created listener=new")
        serviceSourceLog.info("fp_service_endpoint_returned listener=new")
        return endpoint
    }

    func hostProxy(errorHandler: @escaping (Error) -> Void) -> DuoHostCallback? {
        lock.lock()
        let current = connection
        lock.unlock()
        return current?.remoteObjectProxyWithErrorHandler(errorHandler) as? DuoHostCallback
    }

    func invalidate() {
        lock.lock()
        let current = connection
        connection = nil
        let currentListener = listener
        listener = nil
        lock.unlock()
        current?.invalidate()
        currentListener?.invalidate()
    }

    func listener(
        _ listener: NSXPCListener,
        shouldAcceptNewConnection newConnection: NSXPCConnection
    ) -> Bool {
        serviceSourceLog.info(
            "fp_service_should_accept_connection pid=\(newConnection.processIdentifier, privacy: .public) thread=\(Thread.current.description, privacy: .public) timestamp=\(Date().timeIntervalSince1970, privacy: .public)"
        )
        guard peerVerifier(newConnection) else {
            serviceSourceLog.error("fp_service_connection_rejected pid=\(newConnection.processIdentifier, privacy: .public) reason=peer_verification")
            return false
        }
        let exportedInterface = DuoXPC.extensionControlInterface()
        serviceSourceLog.info("fp_service_dependency_ready dependency=NSXPCInterface direction=exported")
        let remoteInterface = DuoXPC.hostCallbackInterface()
        serviceSourceLog.info("fp_service_dependency_ready dependency=NSXPCInterface direction=remote")
        newConnection.exportedInterface = exportedInterface
        newConnection.remoteObjectInterface = remoteInterface
        newConnection.exportedObject = control
        let disconnected = { [weak self, weak newConnection] in
            guard let self, let newConnection else { return }
            self.lock.lock()
            if self.connection === newConnection { self.connection = nil }
            self.lock.unlock()
        }
        newConnection.invalidationHandler = disconnected
        newConnection.interruptionHandler = disconnected
        lock.lock()
        let previous = connection
        connection = newConnection
        lock.unlock()
        previous?.invalidate()
        serviceSourceLog.info("fp_service_connection_configured pid=\(newConnection.processIdentifier, privacy: .public)")
        newConnection.resume()
        serviceSourceLog.info("fp_service_connection_resumed pid=\(newConnection.processIdentifier, privacy: .public)")
        return true
    }
}
