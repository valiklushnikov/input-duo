#pragma once

// CH375's own events and setup, read across the neutral source boundary.
//
// A mechanical conversion and nothing more: Ch375Event's fields copied into a
// SourceEvent, DescriptorSetup's accessors copied into a SourceIdentity. It
// exists so Core 1's loop and InputPipeline never see ch375::Ch375Event again
// - only the shape a second backend will produce too - and so a bug in that
// translation is one small file to check rather than a seam scattered through
// the loop that drives two peripherals a millisecond apart.

#include <cstdint>

#include "ch375/descriptor_setup.hpp"
#include "ch375/device.hpp"
#include "input/source.hpp"

namespace duo_input::u1::input {

class Ch375SourceAdapter {
public:
    /// ``source_id`` is this adapter's own tag for the SourceEvents it
    /// produces - the pipeline never interprets it, only carries it forward.
    explicit Ch375SourceAdapter(std::uint8_t source_id) : source_id_(source_id) {}

    /// Copy one Ch375Event into the neutral shape.
    ///
    /// Returns false for an event this boundary carries no kind for -
    /// Attached and the empty None - which is exactly what InputPipeline's
    /// old CH375-specific on_event did with them: nothing. ``out`` is
    /// unchanged when this returns false.
    bool convert(const ch375::Ch375Event& event, SourceEvent& out) const;

    /// Read a SourceIdentity out of a DescriptorSetup once it is Done.
    /// Fill caller-owned storage directly; a full report set must not become
    /// a temporary on the Core 1 event-dispatch stack.
    void identity(const ch375::DescriptorSetup& setup, SourceIdentity& out) const;

private:
    std::uint8_t source_id_;
};

}  // namespace duo_input::u1::input
