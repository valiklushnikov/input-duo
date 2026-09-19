// Objective-C → Swift bridging header. Exposes the shared XPC protocol contract
// (DuoFPProto.h) and the error domain (DuoFPErrors.h) to the Swift extension and
// test targets, so `NSXPCInterface(with: DuoHostCallback.self)` resolves from the
// same clang-defined @protocol the PyObjC host loads from the dylib.
#import "DuoFPProto.h"
#import "DuoFPErrors.h"
