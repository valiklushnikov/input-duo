#pragma once

#include "config/format.hpp"
#include "protocol/bytes.hpp"
#include "protocol/generated.hpp"

#include <cstddef>
#include <cstdint>

namespace duo_input::config {

class ConfigView;
class ProfileView;
class BindingView;
class MacroView;
class StepView;
class ValidationResult;

enum class ValidationError : std::uint8_t {
    NONE = 0,
    INVALID_INPUT,
    INVALID_LENGTH,
    INVALID_CRC,
    INVALID_FORMAT,
};

class StepView {
public:
    StepView() = default;
    protocol::MacroStepType type() const;
    protocol::ByteView payload() const;

private:
    friend class MacroView;
    StepView(protocol::ByteView bytes, std::size_t offset) : bytes_(bytes), offset_(offset) {}
    protocol::ByteView bytes_{nullptr, 0U};
    std::size_t offset_{0U};
};

class MacroView {
public:
    MacroView() = default;
    std::uint8_t id() const;
    TargetMode target() const;
    protocol::ByteView name() const;
    std::size_t step_count() const;
    bool step_at(std::size_t index, StepView& output) const;

private:
    friend class ProfileView;
    MacroView(protocol::ByteView bytes, std::size_t offset) : bytes_(bytes), offset_(offset) {}
    protocol::ByteView bytes_{nullptr, 0U};
    std::size_t offset_{0U};
};

class BindingView {
public:
    BindingView() = default;
    TriggerKind trigger_kind() const;
    std::uint8_t trigger_code() const;
    std::uint8_t trigger_modifiers() const;
    TriggerSource source() const;
    BindingMode mode() const;
    ActionKind action_kind() const;
    std::uint8_t action_argument() const;

private:
    friend class ProfileView;
    BindingView(protocol::ByteView bytes, std::size_t offset) : bytes_(bytes), offset_(offset) {}
    protocol::ByteView bytes_{nullptr, 0U};
    std::size_t offset_{0U};
};

class ProfileView {
public:
    ProfileView() = default;
    std::uint8_t id() const;
    KeyboardRoute keyboard_route() const;
    MouseRoute mouse_route() const;
    TextLayout text_layout() const;
    protocol::ByteView name() const;
    std::size_t binding_count() const;
    bool binding_at(std::size_t index, BindingView& output) const;
    std::size_t macro_count() const;
    bool macro_at(std::size_t index, MacroView& output) const;

private:
    friend class ConfigView;
    ProfileView(protocol::ByteView bytes, std::size_t offset) : bytes_(bytes), offset_(offset) {}
    protocol::ByteView bytes_{nullptr, 0U};
    std::size_t offset_{0U};
};

class ConfigView {
public:
    std::uint8_t active_profile_id() const;
    std::size_t profile_count() const;
    bool profile_at(std::size_t index, ProfileView& output) const;

private:
    friend class ValidationResult;
    friend ValidationResult validate_config(protocol::ByteView input);
    ConfigView() = default;
    explicit ConfigView(protocol::ByteView bytes) : bytes_(bytes) {}
    protocol::ByteView bytes_{nullptr, 0U};
};

class ValidationResult {
public:
    ValidationError error() const { return error_; }
    const ConfigView& view() const { return view_; }
    explicit operator bool() const { return error_ == ValidationError::NONE; }

private:
    friend ValidationResult validate_config(protocol::ByteView input);
    ValidationResult(ValidationError error, ConfigView view) : error_(error), view_(view) {}
    static ValidationResult failure(ValidationError error);
    static ValidationResult success(protocol::ByteView input);
    const ValidationError error_;
    const ConfigView view_;
};

ValidationResult validate_config(protocol::ByteView input);

}  // namespace duo_input::config
