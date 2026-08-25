#pragma once

#include "protocol/generated.hpp"

#include <cstddef>
#include <cstdint>

namespace duo_input::config {

inline constexpr std::size_t CONFIG_HEADER_SIZE = 64U;
inline constexpr std::size_t PROFILE_DESCRIPTOR_SIZE = 36U;
inline constexpr std::size_t BINDING_RECORD_SIZE = 12U;
inline constexpr std::size_t MACRO_DESCRIPTOR_SIZE = 24U;
inline constexpr std::size_t STEP_DESCRIPTOR_SIZE = 12U;

using KeyboardRoute = protocol::KeyboardRoute;
using MouseRoute = protocol::MouseRoute;
using TargetMode = protocol::TargetMode;
using MouseRouteCommand = protocol::MouseRouteCommand;
using TextLayout = protocol::TextLayout;
using TriggerKind = protocol::TriggerKind;
using BindingMode = protocol::BindingMode;
using ActionKind = protocol::ActionKind;

}  // namespace duo_input::config
