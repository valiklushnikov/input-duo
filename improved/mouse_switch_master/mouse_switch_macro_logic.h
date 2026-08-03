#ifndef MOUSE_SWITCH_MACRO_LOGIC_H
#define MOUSE_SWITCH_MACRO_LOGIC_H

#include <stddef.h>
#include <stdint.h>

constexpr char kTargetMacroText[] = "/target KYPKYMA";
constexpr uint8_t kTargetMacroTextLength =
    static_cast<uint8_t>(sizeof(kTargetMacroText) - 1);
constexpr uint16_t kCharacterDelayMinMs = 35;
constexpr uint16_t kCharacterDelayMaxMs = 80;
constexpr uint16_t kCommandPauseMinMs = 80;
constexpr uint16_t kCommandPauseMaxMs = 140;

constexpr bool constexprStringsEqual(const char* left, const char* right) {
  return *left != *right
             ? false
             : (*left == '\0' ? true
                               : constexprStringsEqual(left + 1, right + 1));
}

enum class TargetMacroEventType : uint8_t { None, Enter, Character };

struct TargetMacroEvent {
  TargetMacroEventType type;
  char character;

  constexpr TargetMacroEvent(TargetMacroEventType eventType, char value)
      : type(eventType), character(value) {}
};

constexpr uint8_t kTargetMacroEventCount = kTargetMacroTextLength + 2;

constexpr TargetMacroEvent targetMacroEventAt(uint8_t eventIndex) {
  return eventIndex == 0 || eventIndex == kTargetMacroEventCount - 1
             ? TargetMacroEvent(TargetMacroEventType::Enter, 0)
             : (eventIndex < kTargetMacroEventCount - 1
                    ? TargetMacroEvent(TargetMacroEventType::Character,
                                       kTargetMacroText[eventIndex - 1])
                    : TargetMacroEvent(TargetMacroEventType::None, 0));
}

constexpr uint16_t delayMinAfterEvent(uint8_t eventIndex) {
  return eventIndex == 0 || eventIndex == kTargetMacroTextLength
             ? kCommandPauseMinMs
             : kCharacterDelayMinMs;
}

constexpr uint16_t delayMaxAfterEvent(uint8_t eventIndex) {
  return eventIndex == 0 || eventIndex == kTargetMacroTextLength
             ? kCommandPauseMaxMs
             : kCharacterDelayMaxMs;
}

struct DebouncedButtonState {
  bool initialized;
  bool rawPressed;
  bool stablePressed;
  bool armed;
  uint32_t rawChangedAtMs;

  constexpr DebouncedButtonState(bool isInitialized = false,
                                 bool raw = false,
                                 bool stable = false,
                                 bool isArmed = false,
                                 uint32_t changedAt = 0)
      : initialized(isInitialized),
        rawPressed(raw),
        stablePressed(stable),
        armed(isArmed),
        rawChangedAtMs(changedAt) {}
};

struct DebouncedButtonUpdate {
  DebouncedButtonState state;
  bool pressedEdge;

  constexpr DebouncedButtonUpdate(DebouncedButtonState nextState, bool edge)
      : state(nextState), pressedEdge(edge) {}
};

constexpr DebouncedButtonState makeInitialDebouncedButtonState() {
  return DebouncedButtonState();
}

constexpr bool debounceIntervalElapsed(uint32_t nowMs,
                                       uint32_t changedAtMs,
                                       uint16_t debounceMs) {
  return static_cast<uint32_t>(nowMs - changedAtMs) >= debounceMs;
}

constexpr DebouncedButtonUpdate updateDebouncedButton(
    DebouncedButtonState state,
    bool rawPressed,
    uint32_t nowMs,
    uint16_t debounceMs) {
  return !state.initialized
             ? DebouncedButtonUpdate(
                   DebouncedButtonState(true, rawPressed, rawPressed,
                                        !rawPressed, nowMs),
                   false)
         : rawPressed != state.rawPressed
             ? DebouncedButtonUpdate(
                   DebouncedButtonState(true, rawPressed,
                                        state.stablePressed, state.armed,
                                        nowMs),
                   false)
         : rawPressed != state.stablePressed &&
                   debounceIntervalElapsed(nowMs, state.rawChangedAtMs,
                                           debounceMs)
             ? DebouncedButtonUpdate(
                   DebouncedButtonState(true, rawPressed, rawPressed,
                                        rawPressed ? false : true,
                                        state.rawChangedAtMs),
                   rawPressed && state.armed)
             : DebouncedButtonUpdate(state, false);
}

constexpr bool shouldStartTargetMacro(bool debouncedPressedEdge,
                                      bool macroActive) {
  return debouncedPressedEdge && !macroActive;
}

#endif
