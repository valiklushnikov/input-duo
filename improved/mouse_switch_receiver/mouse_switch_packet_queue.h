#ifndef MOUSE_SWITCH_PACKET_QUEUE_H
#define MOUSE_SWITCH_PACKET_QUEUE_H

#include <stdint.h>

constexpr uint8_t kMaximumRemoteReportChunks = 3;
constexpr uint8_t kPacketQueueCapacity = 4;

static_assert(kPacketQueueCapacity >= kMaximumRemoteReportChunks,
              "Receiver queue must hold a complete remote report burst");

struct PacketQueueState {
  uint8_t head;
  uint8_t tail;
  uint8_t count;
};

constexpr PacketQueueState makeEmptyPacketQueueState() {
  return PacketQueueState{0, 0, 0};
}

constexpr bool packetQueueCanEnqueue(const PacketQueueState& state) {
  return state.count < kPacketQueueCapacity;
}

constexpr bool packetQueueCanDequeue(const PacketQueueState& state) {
  return state.count > 0;
}

constexpr uint8_t packetQueueFreeSlots(const PacketQueueState& state) {
  return static_cast<uint8_t>(kPacketQueueCapacity - state.count);
}

constexpr uint8_t packetQueueEnqueueSlot(const PacketQueueState& state) {
  return state.tail;
}

constexpr uint8_t packetQueueDequeueSlot(const PacketQueueState& state) {
  return state.head;
}

constexpr uint8_t packetQueueNextIndex(uint8_t index) {
  return static_cast<uint8_t>(index + 1) == kPacketQueueCapacity
             ? 0
             : static_cast<uint8_t>(index + 1);
}

constexpr PacketQueueState packetQueueAfterEnqueue(
    const PacketQueueState& state) {
  return packetQueueCanEnqueue(state)
             ? PacketQueueState{
                   state.head, packetQueueNextIndex(state.tail),
                   static_cast<uint8_t>(state.count + 1)}
             : state;
}

constexpr PacketQueueState packetQueueAfterDequeue(
    const PacketQueueState& state) {
  return packetQueueCanDequeue(state)
             ? PacketQueueState{
                   packetQueueNextIndex(state.head), state.tail,
                   static_cast<uint8_t>(state.count - 1)}
             : state;
}

#endif
