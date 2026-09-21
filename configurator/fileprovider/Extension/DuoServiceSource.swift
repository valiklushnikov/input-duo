import Foundation
import FileProvider
import Security

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
        control = DuoExtensionControlService(store: store, journal: journal, signal: signal)
        self.peerVerifier = peerVerifier
        super.init()
    }

    func makeListenerEndpoint() throws -> NSXPCListenerEndpoint {
        lock.lock()
        defer { lock.unlock() }
        if let listener { return listener.endpoint }
        let anonymous = NSXPCListener.anonymous()
        anonymous.delegate = self
        anonymous.resume()
        listener = anonymous
        return anonymous.endpoint
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
        guard peerVerifier(newConnection) else { return false }
        newConnection.exportedInterface = DuoXPC.extensionControlInterface()
        newConnection.remoteObjectInterface = DuoXPC.hostCallbackInterface()
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
        newConnection.resume()
        return true
    }
}
