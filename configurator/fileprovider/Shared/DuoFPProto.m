#import "DuoFPProto.h"
#import "DuoFPErrors.h"

NSErrorDomain const DuoFPErrorDomain = @"com.duoinput.configurator.fileprovider.error";

// Default visibility so the symbols survive dead-strip and dyld exports them;
// the @protocol(...) reference forces protocol metadata emission + registration
// at image load (dlopen), which objc.protocolNamed(...) then resolves.
__attribute__((visibility("default"))) Protocol *DuoFPHostCallbackProtocol(void) {
    return @protocol(DuoHostCallback);
}

__attribute__((visibility("default"))) Protocol *DuoFPExtensionControlProtocol(void) {
    return @protocol(DuoExtensionControl);
}
