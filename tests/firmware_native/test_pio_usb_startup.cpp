#include "pio_usb/backend.hpp"
#include "test_support.hpp"

#include <string>
#include <thread>
#include <vector>

using duo_input::u1::pio_usb::ClockChangeBarrier;
using duo_input::u1::pio_usb::LinkStartupGate;

TEST_CASE(clock_change_barrier_publishes_core1_writes_to_core0) {
    ClockChangeBarrier barrier;
    int value_written_before_publish = 0;

    CHECK_FALSE(barrier.settled());
    std::thread core1([&] {
        value_written_before_publish = 0x5A17;
        barrier.publish_settled();
    });

    while (!barrier.settled()) {
        std::this_thread::yield();
    }
    CHECK(value_written_before_publish == 0x5A17);
    core1.join();
}

TEST_CASE(pio_link_waits_for_clock_then_restores_baud_before_any_transfer) {
    LinkStartupGate gate;
    std::vector<std::string> calls;
    const auto restore_baud = [&] { calls.emplace_back("restore-baud"); };
    const auto transfer = [&] { calls.emplace_back("transfer"); };

    CHECK_FALSE(gate.run_if_ready(false, restore_baud, transfer));
    CHECK(calls.empty());

    CHECK(gate.run_if_ready(true, restore_baud, transfer));
    CHECK((calls == std::vector<std::string>{"restore-baud", "transfer"}));

    calls.clear();
    CHECK(gate.run_if_ready(true, restore_baud, transfer));
    CHECK((calls == std::vector<std::string>{"transfer"}));
}
