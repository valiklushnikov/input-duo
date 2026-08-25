#include "config/validator.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace {

bool view_is_within(duo_input::protocol::ByteView view, const std::uint8_t* begin,
                    std::size_t size) {
    if (view.data == nullptr || begin == nullptr) {
        return false;
    }
    const std::uintptr_t view_begin = reinterpret_cast<std::uintptr_t>(view.data);
    const std::uintptr_t owner_begin = reinterpret_cast<std::uintptr_t>(begin);
    const std::uintptr_t owner_end = owner_begin + size;
    return view_begin >= owner_begin && view_begin <= owner_end &&
           view.size <= owner_end - view_begin;
}

void check_binding(const duo_input::config::BindingView& binding) {
    static_cast<void>(binding.trigger_kind());
    static_cast<void>(binding.trigger_code());
    static_cast<void>(binding.trigger_modifiers());
    static_cast<void>(binding.mode());
    static_cast<void>(binding.action_kind());
    static_cast<void>(binding.action_argument());
}

void check_view_tree(const duo_input::config::ConfigView& config, const std::uint8_t* data,
                     std::size_t size) {
    if (config.profile_count() == 0U) {
        std::abort();
    }
    for (std::size_t profile_index = 0; profile_index < config.profile_count(); ++profile_index) {
        duo_input::config::ProfileView profile{};
        if (!config.profile_at(profile_index, profile) ||
            !view_is_within(profile.name(), data, size)) {
            std::abort();
        }
        static_cast<void>(profile.id());
        static_cast<void>(profile.keyboard_route());
        static_cast<void>(profile.mouse_route());
        static_cast<void>(profile.text_layout());

        for (std::size_t binding_index = 0; binding_index < profile.binding_count();
             ++binding_index) {
            duo_input::config::BindingView binding{};
            if (!profile.binding_at(binding_index, binding)) {
                std::abort();
            }
            check_binding(binding);
        }

        for (std::size_t macro_index = 0; macro_index < profile.macro_count(); ++macro_index) {
            duo_input::config::MacroView macro{};
            if (!profile.macro_at(macro_index, macro) ||
                !view_is_within(macro.name(), data, size)) {
                std::abort();
            }
            static_cast<void>(macro.id());
            static_cast<void>(macro.target());

            for (std::size_t step_index = 0; step_index < macro.step_count(); ++step_index) {
                duo_input::config::StepView step{};
                if (!macro.step_at(step_index, step) ||
                    !view_is_within(step.payload(), data, size)) {
                    std::abort();
                }
                static_cast<void>(step.type());
            }
        }
    }
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    const auto result = duo_input::config::validate_config({data, size});
    if (!result) {
        return 0;
    }
    check_view_tree(result.view(), data, size);
    return 0;
}
