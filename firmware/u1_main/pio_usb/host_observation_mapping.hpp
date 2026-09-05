#pragma once

// The single boundary from Core-1 host observations to ConfigService's wire
// shape. Keeping it executable outside main() lets native tests drive the same
// mapping and serializer the firmware uses, including overwrite/relabel bugs.

#include "config_service.hpp"
#include "pio_usb/backend.hpp"

namespace duo_input::u1::pio_usb {

inline HostObservation to_wire_host_observation(const HostObservability& observed) noexcept {
    HostObservation out;
    out.init_flags = observed.init_flags;
    out.clk_hz_at_begin = observed.clk_hz_at_begin;
    out.clk_hz_now = observed.clk_hz_now;
    out.sof_frame_count = observed.sof_frame_count;
    out.root_port_state = observed.root_port_state;
    out.root_port_connects = observed.root_port_connects;
    out.core1_passes = observed.core1_passes;
    out.mount_events = observed.mount_events;
    out.umount_events = observed.umount_events;
    out.hid_mount_events = observed.hid_mount_events;
    out.ep_slots_opened = observed.ep_slots_opened;
    out.ep_max_failed_count = observed.ep_max_failed_count;
    out.max_pass_gap_us = observed.max_pass_gap_us;
    out.max_sof_gap = observed.max_sof_gap;
    out.root_port_resets = observed.root_port_resets;
    out.hub_mount_events = observed.hub_mount_events;
    return out;
}

}  // namespace duo_input::u1::pio_usb
