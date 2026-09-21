#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// ============================================================================
// ONE_PROTOCOL_SOURCE. This header is the single source of truth for the Duo
// Input File Provider XPC contract. Both consumers derive from it:
//   * Swift extension  → NSXPCInterface (via the bridging header, DuoXPC.swift)
//   * PyObjC host      → NSXPCInterface (via ctypes.CDLL + objc.protocolNamed,
//                        fileprovider_proto.py)
// The protocols are Objective-C @protocols on purpose: NSXPCInterface requires
// extended method signatures that objc.formal_protocol cannot provide
// (Phase 10.2 OBSERVED). Selectors here MUST match on both sides.
// ============================================================================

/// Implemented by the HOST (Python), called BY the extension during fetchContents.
@protocol DuoHostCallback <NSObject>
- (void)openFetch:(NSString *)generationId
          entryId:(NSNumber *)entryIndex
            reply:(void (^)(NSString * _Nullable fetchToken,
                            NSNumber * _Nullable totalSize,
                            NSError * _Nullable error))reply;
- (void)pullChunk:(NSString *)fetchToken
            reply:(void (^)(NSData * _Nullable chunk,
                            BOOL eof,
                            NSError * _Nullable error))reply;
- (void)cancelFetch:(NSString *)fetchToken;
@end

/// Implemented by the EXTENSION, called BY the host.
/// retireGeneration persists state="retired" (record kept); deleteGeneration is
/// the GC step that permanently removes the replica record. Distinct operations.
@protocol DuoExtensionControl <NSObject>
- (void)publishGeneration:(NSData *)recordJSON
                    reply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
- (void)retireGeneration:(NSString *)generationId
                    reply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
- (void)deleteGeneration:(NSString *)generationId
                    reply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
// Side-effect-free liveness/attach RPC. The host calls this once right after
// binding the XPC connection so the extension's NSXPCListener fires
// shouldAcceptNewConnection and captures the connection — establishing the
// bidirectional channel WITHOUT a clipboard publication. Required so a durable
// (retired) generation stays fetchable after a Mac app/extension restart with
// no new publish (Gate C restart durability). It mutates NOTHING.
- (void)activateWithReply:(void (^)(BOOL ack, NSError * _Nullable error))reply;
@end

// Anchors: referencing @protocol(...) from an exported, default-visibility
// function forces the Objective-C runtime to emit + register protocol metadata
// into any image that links DuoFPProto.m. That is what lets `ctypes.CDLL(...)`
// followed by `objc.protocolNamed("DuoHostCallback")` resolve the protocol in a
// PyObjC host — with no objc.formal_protocol fallback anywhere.
FOUNDATION_EXPORT Protocol *DuoFPHostCallbackProtocol(void);
FOUNDATION_EXPORT Protocol *DuoFPExtensionControlProtocol(void);

NS_ASSUME_NONNULL_END
