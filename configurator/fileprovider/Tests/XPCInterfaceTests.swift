import XCTest
import Foundation
import ObjectiveC

/// Contract tests: both NSXPCInterfaces build from the shared clang-defined
/// Objective-C protocols, and the protocols are actually registered with the
/// Objective-C runtime (the same guarantee the PyObjC host relies on).
final class XPCInterfaceTests: XCTestCase {
    func testHostInterfaceBuilds() {
        let iface = DuoXPC.hostCallbackInterface()   // would trap if the protocol were missing
        XCTAssertNotNil(iface as NSXPCInterface?)
    }

    func testExtensionInterfaceBuilds() {
        let iface = DuoXPC.extensionControlInterface()
        XCTAssertNotNil(iface as NSXPCInterface?)
    }

    func testProtocolsAreRegisteredWithRuntime() {
        XCTAssertNotNil(DuoFPHostCallbackProtocol())
        XCTAssertNotNil(DuoFPExtensionControlProtocol())
        XCTAssertNotNil(objc_getProtocol("DuoHostCallback"))
        XCTAssertNotNil(objc_getProtocol("DuoExtensionControl"))
    }

    func testReplyClassIsWhitelisted() {
        let iface = DuoXPC.hostCallbackInterface()
        let classes = iface.classes(for: Selector("pullChunk:reply:"), argumentIndex: 0, ofReply: true)
        let names = classes.map { String(describing: $0) }
        XCTAssertTrue(names.contains(where: { $0.contains("NSData") }),
                      "pullChunk reply chunk NSData must be whitelisted; got \(names)")
    }
}
