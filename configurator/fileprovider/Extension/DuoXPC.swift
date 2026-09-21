import Foundation

/// Builds the two `NSXPCInterface`s from the shared clang-defined Objective-C
/// protocols in `DuoFPProto.h` (exposed via the bridging header). One protocol
/// source, two interface consumers — this Swift side and the PyObjC host
/// (`fileprovider_proto.py`). Reply-argument classes are whitelisted explicitly
/// for secure coding.
enum DuoXPC {
    /// Host-implemented interface (extension → host: openFetch / pullChunk / cancelFetch).
    static func hostCallbackInterface() -> NSXPCInterface {
        let iface = NSXPCInterface(with: DuoHostCallback.self)
        // openFetch:entryId:reply: → (NSString token, NSNumber size, NSError)
        whitelist(iface, "openFetch:entryId:reply:", 0, [NSString.self, NSError.self], ofReply: true)
        whitelist(iface, "openFetch:entryId:reply:", 1, [NSNumber.self, NSError.self], ofReply: true)
        whitelist(iface, "openFetch:entryId:reply:", 2, [NSError.self], ofReply: true)
        // pullChunk:reply: → (NSData chunk, BOOL eof [scalar], NSError)
        whitelist(iface, "pullChunk:reply:", 0, [NSData.self, NSError.self], ofReply: true)
        whitelist(iface, "pullChunk:reply:", 2, [NSError.self], ofReply: true)
        return iface
    }

    /// Extension-implemented interface (host → extension: publish / retire / delete).
    static func extensionControlInterface() -> NSXPCInterface {
        let iface = NSXPCInterface(with: DuoExtensionControl.self)
        // Incoming NSData argument (recordJSON) — explicit for secure coding.
        whitelist(iface, "publishGeneration:reply:", 0, [NSData.self], ofReply: false)
        // *:reply: → (BOOL ack [scalar], NSError)
        for selector in ["publishGeneration:reply:", "retireGeneration:reply:", "deleteGeneration:reply:"] {
            whitelist(iface, selector, 1, [NSError.self], ofReply: true)
        }
        // activateWithReply: → (BOOL ack [scalar], NSError). The reply block is
        // the only argument (index 0). Side-effect-free connection activation.
        whitelist(iface, "activateWithReply:", 0, [NSError.self], ofReply: true)
        return iface
    }

    private static func whitelist(_ iface: NSXPCInterface,
                                  _ selector: String,
                                  _ index: Int,
                                  _ classes: [AnyClass],
                                  ofReply: Bool) {
        let set = NSSet(array: classes) as! Set<AnyHashable>
        iface.setClasses(set, for: Selector(selector), argumentIndex: index, ofReply: ofReply)
    }
}
