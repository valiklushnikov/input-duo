#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Error domain shared by both XPC sides. The host emits these codes; the
/// extension maps them to NSFileProviderError/Foundation errors (Task 14).
FOUNDATION_EXPORT NSErrorDomain const DuoFPErrorDomain;

typedef NS_ERROR_ENUM(DuoFPErrorDomain, DuoFPError) {
    DuoFPErrorSourceMissing = 1,
    DuoFPErrorSourceChanged = 2,
    DuoFPErrorPeerLost      = 3,
    DuoFPErrorUnauthorized  = 4,
    DuoFPErrorTimeout       = 5,
    DuoFPErrorDiskFull      = 6,
    DuoFPErrorProtocol      = 7,
    DuoFPErrorNotConnected  = 8,
};

NS_ASSUME_NONNULL_END
