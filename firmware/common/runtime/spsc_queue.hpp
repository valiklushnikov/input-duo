#pragma once

// A bounded queue between exactly one producer and exactly one consumer.
//
// On U1 that is Core 1 producing and Core 0 consuming. Core 0 owns USB, SPI
// and flash and must never wait for anything: a Core 0 that blocks stops
// answering the host, and Windows removes devices that stop answering. Core 1
// must not wait either, because the thing it is waiting for is Core 0.
//
// So neither side ever blocks. The producer is told when the queue is full and
// decides what to do about it; the consumer is told when it is empty. There is
// no lock, no allocation and no growth - just a fixed ring and two indices,
// one written by each side.
//
// One slot is always left empty. That is what distinguishes "full" from
// "empty" without a shared count that both cores would have to update.

#include <atomic>
#include <cstddef>
#include <cstdint>

namespace duo_input::runtime {

template <typename T, std::size_t Capacity>
class SpscQueue {
    static_assert(Capacity >= 2, "a queue smaller than two slots can hold nothing");

public:
    /// How many items the queue can actually hold.
    static constexpr std::size_t capacity = Capacity - 1;

    /// Add one item. Returns false when the queue is full; nothing is lost.
    ///
    /// Overwriting the oldest item would be worse than refusing: the oldest
    /// item might be the key release that stops a key repeating forever.
    bool push(const T& item) {
        const std::size_t head = head_.load(std::memory_order_relaxed);
        const std::size_t next = advance(head);
        if (next == tail_.load(std::memory_order_acquire)) {
            return false;
        }
        slots_[head] = item;
        // Release, so the consumer that sees this index also sees the item.
        head_.store(next, std::memory_order_release);
        return true;
    }

    /// Take the oldest item. Returns false when there is nothing to take.
    bool pop(T& item) {
        const std::size_t tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) {
            return false;
        }
        item = slots_[tail];
        tail_.store(advance(tail), std::memory_order_release);
        return true;
    }

    /// How many items are waiting. A snapshot; it may already be stale.
    std::size_t size() const {
        const std::size_t head = head_.load(std::memory_order_acquire);
        const std::size_t tail = tail_.load(std::memory_order_acquire);
        return head >= tail ? head - tail : Capacity - tail + head;
    }

    bool empty() const { return size() == 0; }

    /// Throw away everything the consumer can currently see.
    ///
    /// The consumer's call, and only the consumer's: it writes the index the
    /// consumer already owns. The producer may be pushing at the same time,
    /// and anything it pushes after the head read here survives - which is the
    /// point, since the consumer is discarding what went before, not stopping
    /// the other core.
    void clear() {
        tail_.store(head_.load(std::memory_order_acquire), std::memory_order_release);
    }

private:
    static std::size_t advance(std::size_t index) {
        const std::size_t next = index + 1;
        return next == Capacity ? 0 : next;
    }

    T slots_[Capacity] = {};
    std::atomic<std::size_t> head_{0};
    std::atomic<std::size_t> tail_{0};
};

}  // namespace duo_input::runtime
