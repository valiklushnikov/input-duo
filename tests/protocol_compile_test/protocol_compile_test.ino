#include "mouse_switch_protocol.h"
#include "../../improved/mouse_switch_master/mouse_switch_ps2_logic.h"
#include "../../improved/mouse_switch_receiver/mouse_switch_packet_queue.h"

constexpr PacketQueueState kEmptyQueue = makeEmptyPacketQueueState();
static_assert(packetQueueFreeSlots(kEmptyQueue) == 4,
              "An empty receiver queue must advertise four credits");

constexpr uint8_t kFirstPacketSlot = packetQueueEnqueueSlot(kEmptyQueue);
static_assert(kFirstPacketSlot == 0,
              "The first queued packet must use slot zero");
constexpr PacketQueueState kQueueAfterFirst =
    packetQueueAfterEnqueue(kEmptyQueue);
static_assert(packetQueueFreeSlots(kQueueAfterFirst) == 3,
              "One queued packet must consume one credit");

constexpr uint8_t kSecondPacketSlot =
    packetQueueEnqueueSlot(kQueueAfterFirst);
static_assert(kSecondPacketSlot == 1,
              "The second queued packet must use slot one");
constexpr PacketQueueState kQueueAfterSecond =
    packetQueueAfterEnqueue(kQueueAfterFirst);
static_assert(packetQueueFreeSlots(kQueueAfterSecond) == 2,
              "Two queued packets must leave two credits");

constexpr uint8_t kThirdPacketSlot =
    packetQueueEnqueueSlot(kQueueAfterSecond);
static_assert(kThirdPacketSlot == 2,
              "The third queued packet must use slot two");
constexpr PacketQueueState kQueueAfterThird =
    packetQueueAfterEnqueue(kQueueAfterSecond);
static_assert(packetQueueFreeSlots(kQueueAfterThird) == 1,
              "A three-packet burst must leave one credit");
static_assert(packetQueueDequeueSlot(kQueueAfterThird) == 0,
              "The first queued packet must be consumed first");

constexpr PacketQueueState kQueueAfterFirstConsumed =
    packetQueueAfterDequeue(kQueueAfterThird);
static_assert(packetQueueFreeSlots(kQueueAfterFirstConsumed) == 2,
              "Consuming one packet must restore one credit");
static_assert(packetQueueDequeueSlot(kQueueAfterFirstConsumed) == 1,
              "The second queued packet must be consumed second");

constexpr PacketQueueState kQueueAfterSecondConsumed =
    packetQueueAfterDequeue(kQueueAfterFirstConsumed);
static_assert(packetQueueFreeSlots(kQueueAfterSecondConsumed) == 3,
              "Consuming two packets must restore two credits");
static_assert(packetQueueDequeueSlot(kQueueAfterSecondConsumed) == 2,
              "The third queued packet must be consumed third");

constexpr PacketQueueState kQueueAfterThirdConsumed =
    packetQueueAfterDequeue(kQueueAfterSecondConsumed);
static_assert(packetQueueFreeSlots(kQueueAfterThirdConsumed) == 4,
              "Consuming the burst must restore all credits");
static_assert(!packetQueueCanDequeue(kQueueAfterThirdConsumed),
              "The queue must be empty after consuming the burst");

constexpr PacketQueueState kFullQueue = packetQueueAfterEnqueue(
    packetQueueAfterEnqueue(
        packetQueueAfterEnqueue(packetQueueAfterEnqueue(kEmptyQueue))));
static_assert(packetQueueFreeSlots(kFullQueue) == 0,
              "All four receiver queue slots must be usable");
static_assert(kFullQueue.tail == 0,
              "The four-slot queue tail must wrap to slot zero");
static_assert(!packetQueueCanEnqueue(kFullQueue),
              "A full queue must reject another enqueue reservation");
static_assert(packetQueueAfterEnqueue(kFullQueue).count ==
                  kPacketQueueCapacity,
              "A rejected enqueue must not overwrite queued packets");

static_assert(decodePs2Axis(0x80, false) == 128,
              "Positive axis values must not be narrowed to int8_t");
static_assert(decodePs2Axis(0xFF, false) == 255,
              "Positive axis maximum must be preserved");
static_assert(decodePs2Axis(0x00, true) == -256,
              "Negative axis minimum must use the status sign bit");
static_assert(decodePs2Axis(0x7F, true) == -129,
              "Negative values below int8_t range must be preserved");
static_assert(decodePs2Axis(0xFF, true) == -1,
              "Negative axis data must be reconstructed from nine bits");

static_assert(hidDisplacementChunk(127) == 127,
              "Positive HID boundary must be unchanged");
static_assert(hidDisplacementChunk(128) == 127,
              "Positive HID overflow must be chunked");
static_assert(hidDisplacementChunk(-127) == -127,
              "Negative HID boundary must be unchanged");
static_assert(hidDisplacementChunk(-128) == -127,
              "HID chunks must never contain -128");

constexpr int16_t kPositive255Remainder1 =
    255 - hidDisplacementChunk(255);
constexpr int16_t kPositive255Remainder2 =
    kPositive255Remainder1 - hidDisplacementChunk(kPositive255Remainder1);
static_assert(hidDisplacementChunk(255) +
                  hidDisplacementChunk(kPositive255Remainder1) +
                  hidDisplacementChunk(kPositive255Remainder2) ==
              255,
              "Chunks must preserve a +255 displacement");

constexpr int16_t kNegative256Remainder1 =
    -256 - hidDisplacementChunk(-256);
constexpr int16_t kNegative256Remainder2 =
    kNegative256Remainder1 - hidDisplacementChunk(kNegative256Remainder1);
static_assert(hidDisplacementChunk(-256) +
                  hidDisplacementChunk(kNegative256Remainder1) +
                  hidDisplacementChunk(kNegative256Remainder2) ==
              -256,
              "Chunks must preserve a -256 displacement");

constexpr int16_t kInvertedNegative256 = negatePs2Axis(-256);
constexpr int16_t kInvertedNegative256Remainder1 =
    kInvertedNegative256 - hidDisplacementChunk(kInvertedNegative256);
constexpr int16_t kInvertedNegative256Remainder2 =
    kInvertedNegative256Remainder1 -
    hidDisplacementChunk(kInvertedNegative256Remainder1);
static_assert(kInvertedNegative256 == 256,
              "PS/2 Y minimum must negate safely in int16_t");
static_assert(hidDisplacementChunk(kInvertedNegative256) +
                  hidDisplacementChunk(kInvertedNegative256Remainder1) +
                  hidDisplacementChunk(kInvertedNegative256Remainder2) ==
              256,
              "Chunks must preserve the inverted Y maximum");
static_assert(negatePs2Axis(255) == -255,
              "PS/2 Y maximum must negate safely in int16_t");

static_assert(decodePs2Wheel(0x04, 0x07) == 7,
              "Explorer wheel positive nibble must be preserved");
static_assert(decodePs2Wheel(0x04, 0x08) == -8,
              "Explorer wheel nibble must be sign-extended");
static_assert(decodePs2Wheel(0x04, 0x3F) == -1,
              "Explorer extra-button bits must not affect wheel data");
static_assert(decodePs2Wheel(0x03, 0x80) == -128,
              "IntelliMouse wheel must retain signed-byte behavior");
static_assert(decodePs2Wheel(0x03, 0x7F) == 127,
              "IntelliMouse positive wheel boundary must be preserved");
static_assert(decodePs2SideButtons(0x04, 0x10) == 0x10,
              "Explorer button 4 must be visible");
static_assert(decodePs2SideButtons(0x04, 0x20) == 0x20,
              "Explorer button 5 must be visible");
static_assert(decodePs2SideButtons(0x04, 0x3F) == 0x30,
              "Wheel nibble must not enter the side-button mask");
static_assert(decodePs2SideButtons(0x03, 0x30) == 0,
              "IntelliMouse reports do not expose Explorer buttons");
static_assert(decodePs2SideButtons(0x00, 0x30) == 0,
              "Standard reports do not contain a fourth byte");
static_assert(isPs2SideButtonPressed(0x10, 0x10),
              "The measured 0x10 button must activate switching");
static_assert(isPs2SideButtonPressed(0x30, 0x10),
              "The selected button must remain visible with both held");
static_assert(!isPs2SideButtonPressed(0x20, 0x10),
              "The other side button must not activate switching");
static_assert(!isPs2SideButtonPressed(0x00, 0x10),
              "Released side buttons must not activate switching");
static_assert(shouldAttemptExplorerUpgrade(0x03),
              "IntelliMouse ID must trigger Explorer negotiation");
static_assert(!shouldAttemptExplorerUpgrade(0x00),
              "Standard mouse ID must not trigger Explorer negotiation");
static_assert(!shouldAttemptExplorerUpgrade(0x04),
              "Explorer ID must not repeat Explorer negotiation");
static_assert(!shouldReconnectAfterPs2Failure(1, 3),
              "One damaged PS/2 report must be ignored");
static_assert(!shouldReconnectAfterPs2Failure(2, 3),
              "Two damaged PS/2 reports must not reset the mouse");
static_assert(shouldReconnectAfterPs2Failure(3, 3),
              "The configured consecutive-failure limit must reconnect");

static_assert(kPacketSize == 7, "Unexpected packet size");
static_assert(checksumBytes(0xA1, 0x7F, 0x80, 0x7F, 0xFF, 0x03) == 0xDD,
              "Checksum contract changed");
static_assert(packetLengthIsValid(7), "Seven-byte packets must be accepted");
static_assert(!packetLengthIsValid(0), "Empty packets must be rejected");
static_assert(!packetLengthIsValid(6), "Short packets must be rejected");
static_assert(!packetLengthIsValid(8), "Long packets must be rejected");

constexpr MousePacket kBoundaryReport =
    makeReportPacket(0x22, -128, 127, -128, 0x03);
static_assert(kBoundaryReport.type == kReportPacketType,
              "Report constructor must set its type");
static_assert(kBoundaryReport.sequence == 0x22,
              "Report constructor must retain sequence");
static_assert(static_cast<uint8_t>(kBoundaryReport.dx) == 0x80,
              "Protocol must preserve signed -128 payload bytes");
static_assert(static_cast<uint8_t>(kBoundaryReport.dy) == 0x7F,
              "Protocol must preserve signed +127 payload bytes");
static_assert(static_cast<uint8_t>(kBoundaryReport.wheel) == 0x80,
              "Protocol wheel must preserve signed -128 payload bytes");
static_assert(kBoundaryReport.buttons == 0x03,
              "Report constructor must retain allowed buttons");
static_assert(kBoundaryReport.checksum == 0xFF,
              "Report constructor must calculate checksum");
static_assert(packetIsValid(kBoundaryReport),
              "Constructed boundary report must validate");
constexpr MousePacket kMaskedReport =
    makeReportPacket(0x24, 0, 0, 0, 0x83);
static_assert(kMaskedReport.buttons == 0x03,
              "Report constructor must remove unsupported button bits");
static_assert(packetIsValid(kMaskedReport),
              "Report constructor must produce a valid masked packet");

constexpr MousePacket kRelease = makeReleasePacket(0x23);
static_assert(kRelease.type == kReleasePacketType,
              "Release constructor must set its type");
static_assert(kRelease.sequence == 0x23,
              "Release constructor must retain sequence");
static_assert(kRelease.dx == 0 && kRelease.dy == 0 && kRelease.wheel == 0 &&
                  kRelease.buttons == 0,
              "Release constructor must clear movement and buttons");
static_assert(kRelease.checksum == 0x83,
              "Release constructor must calculate checksum");
static_assert(packetIsValid(kRelease),
              "Constructed release packet must validate");

constexpr MousePacket kBadType = {0xA2, 0x01, 0, 0, 0, 0, 0xA3};
static_assert(!packetIsValid(kBadType), "Unknown packet types must be rejected");
constexpr MousePacket kIllegalButtons = {
    kReportPacketType, 0x02, 0, 0, 0, 0x04, 0xA7};
static_assert(!packetIsValid(kIllegalButtons),
              "Reports with illegal button bits must be rejected");
constexpr MousePacket kMalformedRelease = {
    kReleasePacketType, 0x03, 1, 0, 0, 0x01, 0xA3};
static_assert(!packetIsValid(kMalformedRelease),
              "Release packets with movement or buttons must be rejected");
constexpr MousePacket kCorruptedChecksum = {
    kReportPacketType, 0x04, 1, -1, 0, 0x03, 0x00};
static_assert(!packetIsValid(kCorruptedChecksum),
              "Reports with corrupted checksums must be rejected");

static_assert(isMiddlePressEdge(true, false), "Press edge must switch");
static_assert(!isMiddlePressEdge(true, true), "Held button must not switch again");
static_assert(!isMiddlePressEdge(false, true), "Release must not switch");

void setup() {}

void loop() {}
