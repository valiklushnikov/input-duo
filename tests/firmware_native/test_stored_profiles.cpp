// Reading the configuration the configurator actually writes.
//
// Every test here runs against `valid_full.bin` - the interoperability vector
// the host tooling generates - rather than a blob written by hand next to the
// code that parses it. A parser tested against its author's idea of the format
// agrees with itself and with nothing else.

#include "config_profiles.hpp"
#include "protocol/crc.hpp"
#include "test_support.hpp"

#include <cstdio>
#include <string>
#include <vector>

using duo_input::config::ActionKind;
using duo_input::config::BindingMode;
using duo_input::config::KeyboardRoute;
using duo_input::config::MouseRoute;
using duo_input::config::MacroStepType;
using duo_input::config::TriggerKind;
using duo_input::protocol::ByteView;
using duo_input::u1::StoredProfiles;
using duo_input::u1::macros::MacroDefinition;
using duo_input::u1::mapping::Binding;
using duo_input::u1::mapping::kMaxBindings;

namespace {

std::vector<std::uint8_t> read_vector(const char* name) {
    const std::string path = std::string(DUO_TEST_VECTOR_DIR) + "/config_vectors/" + name;
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return {};
    }
    std::vector<std::uint8_t> bytes;
    std::uint8_t chunk[4096];
    std::size_t read = 0;
    while ((read = std::fread(chunk, 1, sizeof(chunk), file)) > 0) {
        bytes.insert(bytes.end(), chunk, chunk + read);
    }
    std::fclose(file);
    return bytes;
}

struct Loaded {
    std::vector<std::uint8_t> blob = read_vector("valid_full.bin");
    StoredProfiles profiles;

    Loaded() { profiles.load(ByteView{blob.data(), blob.size()}); }
};

/// The vector stores every profile as starting on PC1, which is also what a
/// fresh Routes holds - so reading it proves nothing about whether the bytes
/// were read at all. This moves profile 1 off the default and repairs the
/// header CRC, exactly as a hand-edited project file would arrive.
struct Rerouted {
    std::vector<std::uint8_t> blob = read_vector("valid_full.bin");
    StoredProfiles profiles;

    Rerouted() {
        const std::size_t descriptor = duo_input::config::CONFIG_HEADER_SIZE;
        blob[descriptor + 1U] = static_cast<std::uint8_t>(KeyboardRoute::BOTH);
        blob[descriptor + 2U] = static_cast<std::uint8_t>(MouseRoute::PC2);
        // The header's own CRC field reads as zero while the CRC is computed.
        blob[12] = 0;
        blob[13] = 0;
        blob[14] = 0;
        blob[15] = 0;
        const std::uint32_t crc =
            duo_input::protocol::crc32_ieee(ByteView{blob.data(), blob.size()});
        blob[12] = static_cast<std::uint8_t>(crc);
        blob[13] = static_cast<std::uint8_t>(crc >> 8);
        blob[14] = static_cast<std::uint8_t>(crc >> 16);
        blob[15] = static_cast<std::uint8_t>(crc >> 24);
        profiles.load(ByteView{blob.data(), blob.size()});
    }
};

}  // namespace

TEST_CASE(the_interoperability_vector_is_present_and_accepted) {
    Loaded loaded;

    // Everything below is worthless if this fails, and a parser that reports
    // an empty configuration for an unreadable file looks exactly like one
    // reading a configuration with nothing in it.
    CHECK(!loaded.blob.empty());
    CHECK(loaded.profiles.loaded());
}

TEST_CASE(the_configurations_own_active_profile_is_the_one_reported) {
    Loaded loaded;

    // The vector is built with profile 8 active.
    CHECK_EQ(static_cast<int>(loaded.profiles.active_profile_id()), 8);
}

TEST_CASE(a_profiles_bindings_are_read_in_full) {
    Loaded loaded;
    Binding bindings[kMaxBindings];

    // Profile 1 carries the four bindings; the rest are empty.
    CHECK_EQ(loaded.profiles.bindings_for(1, bindings), 4u);
    CHECK_EQ(loaded.profiles.bindings_for(2, bindings), 0u);
}

TEST_CASE(a_keyboard_binding_keeps_its_trigger_and_its_modifiers) {
    Loaded loaded;
    Binding bindings[kMaxBindings];
    loaded.profiles.bindings_for(1, bindings);

    CHECK_EQ(static_cast<int>(bindings[0].trigger),
             static_cast<int>(TriggerKind::KEYBOARD_USAGE));
    CHECK_EQ(static_cast<int>(bindings[0].code), 4);
    CHECK_EQ(static_cast<int>(bindings[0].required_modifiers), 2);
    CHECK_EQ(static_cast<int>(bindings[0].mode), static_cast<int>(BindingMode::REPLACE));
}

TEST_CASE(a_mouse_binding_keeps_the_button_number_the_format_uses) {
    Loaded loaded;
    Binding bindings[kMaxBindings];
    loaded.profiles.bindings_for(1, bindings);

    // The format numbers buttons from one and the engine matches on the same
    // number the capture reports, so nothing is renumbered on the way in.
    CHECK_EQ(static_cast<int>(bindings[1].trigger), static_cast<int>(TriggerKind::MOUSE_BUTTON));
    CHECK_EQ(static_cast<int>(bindings[1].code), 5);
    CHECK_EQ(static_cast<int>(bindings[1].mode), static_cast<int>(BindingMode::ADD));
}

TEST_CASE(an_action_that_carries_no_argument_still_arrives_intact) {
    Loaded loaded;
    Binding bindings[kMaxBindings];
    loaded.profiles.bindings_for(1, bindings);

    CHECK_EQ(static_cast<int>(bindings[2].action),
             static_cast<int>(ActionKind::TOGGLE_KEYBOARD_ROUTE));
}

TEST_CASE(a_macro_binding_points_at_a_slot_and_not_at_a_wire_identifier) {
    Loaded loaded;
    Binding bindings[kMaxBindings];
    loaded.profiles.bindings_for(1, bindings);

    // The format lets a macro be numbered up to 255. The output side keeps
    // macro-held keys apart from the operator's by an owner index, and there
    // are thirty-two of those - so the wire's identifier cannot be used as
    // one. It is translated here, once, where both tables are in view; passing
    // 255 through would index off the end of the owner table.
    CHECK_EQ(static_cast<int>(bindings[0].action), static_cast<int>(ActionKind::RUN_MACRO));
    CHECK(bindings[0].parameter < 32);
}

TEST_CASE(the_slot_a_binding_names_holds_the_macro_it_meant) {
    Loaded loaded;
    Binding bindings[kMaxBindings];
    loaded.profiles.bindings_for(1, bindings);
    MacroDefinition definitions[32];
    loaded.profiles.macros_for(1, definitions, 32);

    // Macro 255 in the vector has no steps; macro 1 has nine. Getting the
    // translation backwards would silently run the wrong macro, which is the
    // kind of fault that looks like a hardware problem.
    CHECK_EQ(definitions[bindings[0].parameter].count, 0u);
}

TEST_CASE(a_profiles_macros_are_read_with_all_their_steps) {
    Loaded loaded;
    MacroDefinition definitions[32];

    CHECK_EQ(loaded.profiles.macros_for(1, definitions, 32), 2u);
    // Macro 1 carries one of every step type the format defines.
    const std::size_t nine = definitions[0].count + definitions[1].count;
    CHECK_EQ(nine, 9u);
}

TEST_CASE(a_delay_step_becomes_a_fixed_part_and_a_spread) {
    Loaded loaded;
    MacroDefinition definitions[32];
    loaded.profiles.macros_for(1, definitions, 32);
    const MacroDefinition& macro = definitions[0].count == 9 ? definitions[0] : definitions[1];

    // The format stores a delay as a minimum and a maximum; the scheduler
    // wants a fixed wait and how much longer it may randomly be. The vector's
    // is 12 to 60000.
    const auto& step = macro.steps[5];
    CHECK_EQ(static_cast<int>(step.kind), static_cast<int>(MacroStepType::DELAY));
    CHECK_EQ(static_cast<int>(step.delay_ms), 12);
    CHECK_EQ(static_cast<int>(step.jitter_ms), 60000 - 12);
}

TEST_CASE(a_tap_keeps_the_modifier_that_makes_its_letter_capital) {
    Loaded loaded;
    MacroDefinition definitions[32];
    loaded.profiles.macros_for(1, definitions, 32);
    const MacroDefinition& macro = definitions[0].count == 9 ? definitions[0] : definitions[1];

    const auto& step = macro.steps[0];
    CHECK_EQ(static_cast<int>(step.kind), static_cast<int>(MacroStepType::KEY_TAP));
    CHECK_EQ(static_cast<int>(step.pair_bytes), 2);
    CHECK_EQ(static_cast<int>(step.pairs[0]), 0x02);
    CHECK_EQ(static_cast<int>(step.pairs[1]), 0x04);
}

TEST_CASE(a_text_step_points_at_the_pairs_the_host_compiled) {
    Loaded loaded;
    MacroDefinition definitions[32];
    loaded.profiles.macros_for(1, definitions, 32);
    const MacroDefinition& macro = definitions[0].count == 9 ? definitions[0] : definitions[1];

    const auto& step = macro.steps[4];
    CHECK_EQ(static_cast<int>(step.kind), static_cast<int>(MacroStepType::TEXT));
    CHECK_EQ(static_cast<int>(step.pair_bytes), 4);
    CHECK_EQ(static_cast<int>(step.pairs[1]), 0x0B);
    CHECK_EQ(static_cast<int>(step.pairs[3]), 0x0C);
}

TEST_CASE(a_steps_payload_is_read_where_it_lies_and_not_copied) {
    Loaded loaded;
    MacroDefinition definitions[32];
    loaded.profiles.macros_for(1, definitions, 32);
    const MacroDefinition& macro = definitions[0].count == 9 ? definitions[0] : definitions[1];

    // A text step may hold a thousand characters and this chip has no memory
    // to duplicate that per macro. The configuration outlives every macro
    // that reads it, so the payload is pointed at.
    const std::uint8_t* first = loaded.blob.data();
    CHECK(macro.steps[4].pairs >= first);
    CHECK(macro.steps[4].pairs < first + loaded.blob.size());
}

TEST_CASE(a_single_byte_step_carries_its_usage_in_the_code) {
    Loaded loaded;
    MacroDefinition definitions[32];
    loaded.profiles.macros_for(1, definitions, 32);
    const MacroDefinition& macro = definitions[0].count == 9 ? definitions[0] : definitions[1];

    CHECK_EQ(static_cast<int>(macro.steps[1].kind), static_cast<int>(MacroStepType::KEY_DOWN));
    CHECK_EQ(static_cast<int>(macro.steps[1].code), 0x05);
    CHECK_EQ(static_cast<int>(macro.steps[2].kind), static_cast<int>(MacroStepType::KEY_UP));
    CHECK_EQ(static_cast<int>(macro.steps[2].code), 0x05);
}

TEST_CASE(a_consumer_step_carries_a_two_byte_usage) {
    // Against a usage whose high byte is not zero. The full vector's consumer
    // usage is 0x00E9, which reads the same whether the code takes two bytes
    // or one - so it cannot tell a correct implementation from a broken one,
    // and a test that cannot fail is not a test.
    std::vector<std::uint8_t> blob = read_vector("valid_wide_usages.bin");
    StoredProfiles profiles;
    CHECK(profiles.load(ByteView{blob.data(), blob.size()}));

    MacroDefinition definitions[32];
    profiles.macros_for(1, definitions, 32);
    const MacroDefinition& macro = definitions[0].count == 1 ? definitions[0] : definitions[1];

    CHECK_EQ(static_cast<int>(macro.steps[0].kind),
             static_cast<int>(MacroStepType::CONSUMER_TAP));
    CHECK_EQ(static_cast<int>(macro.steps[0].code), 0x01B1);
}

TEST_CASE(a_macro_numbered_beyond_the_owner_table_still_gets_a_slot) {
    // The format allows macro 200; the output side has thirty-two owner
    // indices. Passing the wire number through would index off the end of the
    // owner table, and the full vector cannot catch it because its own macro
    // happens to sit at a low slot either way.
    std::vector<std::uint8_t> blob = read_vector("valid_wide_usages.bin");
    StoredProfiles profiles;
    profiles.load(ByteView{blob.data(), blob.size()});

    Binding bindings[kMaxBindings];
    CHECK_EQ(profiles.bindings_for(1, bindings), 1u);
    CHECK_EQ(static_cast<int>(bindings[0].action), static_cast<int>(ActionKind::RUN_MACRO));
    CHECK(bindings[0].parameter < 32);

    // And it is the slot holding macro 200 - the one with the step in it -
    // not merely some number small enough to pass.
    MacroDefinition definitions[32];
    profiles.macros_for(1, definitions, 32);
    CHECK_EQ(definitions[bindings[0].parameter].count, 1u);
}

TEST_CASE(a_refused_configuration_leaves_nothing_half_loaded) {
    std::vector<std::uint8_t> blob = read_vector("valid_full.bin");
    blob[blob.size() / 2] ^= 0xFF;  // break the CRC
    StoredProfiles profiles;

    CHECK(!profiles.load(ByteView{blob.data(), blob.size()}));
    CHECK(!profiles.loaded());

    // And a caller that ignores the result gets nothing rather than fragments
    // of a configuration nobody validated.
    Binding bindings[kMaxBindings];
    CHECK_EQ(profiles.bindings_for(1, bindings), 0u);
}

TEST_CASE(an_empty_configuration_is_refused_rather_than_read) {
    StoredProfiles profiles;

    CHECK(!profiles.load(ByteView{nullptr, 0}));
    CHECK(!profiles.loaded());
}

TEST_CASE(a_profile_that_is_not_there_yields_nothing) {
    Loaded loaded;
    Binding bindings[kMaxBindings];

    CHECK_EQ(loaded.profiles.bindings_for(200, bindings), 0u);
}

TEST_CASE(a_profile_carries_the_routes_it_is_stored_as_starting_in) {
    Rerouted rerouted;
    CHECK(rerouted.profiles.loaded());

    KeyboardRoute keyboard = KeyboardRoute::PC1;
    MouseRoute mouse = MouseRoute::PC1;
    CHECK(rerouted.profiles.routes_for(1, keyboard, mouse));

    // Two separate bytes one after the other in the descriptor, and reading
    // them the wrong way round is the kind of mistake nothing else would
    // catch: BOTH is not a mouse route and PC2 is a valid keyboard one.
    CHECK_EQ(static_cast<int>(keyboard), static_cast<int>(KeyboardRoute::BOTH));
    CHECK_EQ(static_cast<int>(mouse), static_cast<int>(MouseRoute::PC2));
}

TEST_CASE(the_other_profiles_routes_are_their_own) {
    Rerouted rerouted;

    KeyboardRoute keyboard = KeyboardRoute::BOTH;
    MouseRoute mouse = MouseRoute::PC2;
    CHECK(rerouted.profiles.routes_for(2, keyboard, mouse));

    CHECK_EQ(static_cast<int>(keyboard), static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(mouse), static_cast<int>(MouseRoute::PC1));
}

TEST_CASE(a_profile_that_is_not_there_has_no_routes_to_give) {
    Loaded loaded;

    KeyboardRoute keyboard = KeyboardRoute::PC1;
    MouseRoute mouse = MouseRoute::PC1;

    // Not a route of its own invention: the runtime keeps the one it has.
    CHECK_FALSE(loaded.profiles.routes_for(200, keyboard, mouse));
}
