#include <Mouse.h>
#include <Wire.h>

#include "mouse_switch_protocol.h"

constexpr uint8_t kMouseDataPin = 9;
constexpr uint8_t kMouseClockPin = 10;
constexpr uint8_t kReceiverAddress = 0x02;
constexpr uint32_t kPs2EdgeTimeoutUs = 25000;
constexpr uint32_t kReconnectIntervalMs = 1000;
constexpr bool kDebug = false;

enum class Target : uint8_t { LocalLaptop, RemoteLaptop };

Target target = Target::RemoteLaptop;
bool mouseConnected = false;
bool wheelAvailable = false;
bool previousMiddlePressed = false;
bool localLeftPressed = false;
bool localRightPressed = false;
uint8_t sequence = 0;
uint32_t lastReconnectAttemptMs = 0;

void releaseLine(uint8_t pin) {
  pinMode(pin, INPUT_PULLUP);
}

void assertLineLow(uint8_t pin) {
  digitalWrite(pin, LOW);
  pinMode(pin, OUTPUT);
}

bool waitForPinLevel(uint8_t pin, uint8_t level) {
  const uint32_t startedAt = micros();
  while (digitalRead(pin) != level) {
    if (static_cast<uint32_t>(micros() - startedAt) >= kPs2EdgeTimeoutUs) {
      return false;
    }
  }
  return true;
}

bool ps2WriteByte(uint8_t value) {
  uint8_t parity = 1;

  releaseLine(kMouseDataPin);
  releaseLine(kMouseClockPin);
  delayMicroseconds(150);
  assertLineLow(kMouseClockPin);
  delayMicroseconds(150);
  assertLineLow(kMouseDataPin);
  delayMicroseconds(10);
  releaseLine(kMouseClockPin);

  if (!waitForPinLevel(kMouseClockPin, LOW)) {
    return false;
  }

  for (uint8_t bit = 0; bit < 8; ++bit) {
    if ((value & 0x01) != 0) {
      releaseLine(kMouseDataPin);
    } else {
      assertLineLow(kMouseDataPin);
    }
    parity ^= (value & 0x01);
    value >>= 1;

    if (!waitForPinLevel(kMouseClockPin, HIGH) ||
        !waitForPinLevel(kMouseClockPin, LOW)) {
      return false;
    }
  }

  if (parity != 0) {
    releaseLine(kMouseDataPin);
  } else {
    assertLineLow(kMouseDataPin);
  }
  if (!waitForPinLevel(kMouseClockPin, HIGH) ||
      !waitForPinLevel(kMouseClockPin, LOW)) {
    return false;
  }

  releaseLine(kMouseDataPin);
  if (!waitForPinLevel(kMouseClockPin, HIGH) ||
      !waitForPinLevel(kMouseDataPin, HIGH)) {
    return false;
  }
  return true;
}

bool ps2ReadByte(uint8_t& value) {
  value = 0;
  uint8_t parity = 1;
  releaseLine(kMouseDataPin);
  releaseLine(kMouseClockPin);

  if (!waitForPinLevel(kMouseClockPin, LOW) ||
      digitalRead(kMouseDataPin) != LOW ||
      !waitForPinLevel(kMouseClockPin, HIGH)) {
    return false;
  }

  for (uint8_t bit = 0; bit < 8; ++bit) {
    if (!waitForPinLevel(kMouseClockPin, LOW)) {
      return false;
    }
    const uint8_t dataBit = digitalRead(kMouseDataPin) == HIGH ? 1 : 0;
    value |= static_cast<uint8_t>(dataBit << bit);
    parity ^= dataBit;
    if (!waitForPinLevel(kMouseClockPin, HIGH)) {
      return false;
    }
  }

  if (!waitForPinLevel(kMouseClockPin, LOW)) {
    return false;
  }
  const bool parityBitHigh = digitalRead(kMouseDataPin) == HIGH;
  if (!waitForPinLevel(kMouseClockPin, HIGH) ||
      parityBitHigh != (parity != 0)) {
    return false;
  }

  if (!waitForPinLevel(kMouseClockPin, LOW)) {
    return false;
  }
  const bool stopBitHigh = digitalRead(kMouseDataPin) == HIGH;
  if (!waitForPinLevel(kMouseClockPin, HIGH) || !stopBitHigh) {
    return false;
  }
  return true;
}

bool sendCommand(uint8_t command) {
  uint8_t acknowledgement = 0;
  return ps2WriteByte(command) && ps2ReadByte(acknowledgement) &&
         acknowledgement == 0xFA;
}

bool setSampleRate(uint8_t rate) {
  return sendCommand(0xF3) && sendCommand(rate);
}

bool initializeMouse() {
  uint8_t response = 0;
  if (!ps2WriteByte(0xFF) || !ps2ReadByte(response) || response != 0xFA ||
      !ps2ReadByte(response) || response != 0xAA ||
      !ps2ReadByte(response)) {
    return false;
  }

  if (!setSampleRate(200) || !setSampleRate(100) || !setSampleRate(80) ||
      !sendCommand(0xF2) || !ps2ReadByte(response)) {
    return false;
  }
  wheelAvailable = response == 0x03 || response == 0x04;
  if (response != 0x00 && !wheelAvailable) {
    return false;
  }

  return sendCommand(0xE8) && sendCommand(0x03) && sendCommand(0xE6) &&
         setSampleRate(40) && sendCommand(0xF4) && sendCommand(0xF0);
}

bool pollMouse(uint8_t& status, int8_t& rawX, int8_t& rawY, int8_t& rawWheel) {
  uint8_t byte = 0;
  if (!sendCommand(0xEB) || !ps2ReadByte(status) || !ps2ReadByte(byte)) {
    return false;
  }
  rawX = static_cast<int8_t>(byte);
  if (!ps2ReadByte(byte)) {
    return false;
  }
  rawY = static_cast<int8_t>(byte);
  rawWheel = 0;
  if (wheelAvailable) {
    if (!ps2ReadByte(byte)) {
      return false;
    }
    rawWheel = static_cast<int8_t>(byte);
  }
  return true;
}

int8_t negatePs2Delta(int8_t value) {
  return value == -128 ? 127 : static_cast<int8_t>(-value);
}

void setLocalButtons(uint8_t buttons) {
  const bool leftPressed = (buttons & 0x01) != 0;
  const bool rightPressed = (buttons & 0x02) != 0;
  if (leftPressed != localLeftPressed) {
    leftPressed ? Mouse.press(MOUSE_LEFT) : Mouse.release(MOUSE_LEFT);
    localLeftPressed = leftPressed;
  }
  if (rightPressed != localRightPressed) {
    rightPressed ? Mouse.press(MOUSE_RIGHT) : Mouse.release(MOUSE_RIGHT);
    localRightPressed = rightPressed;
  }
}

void releaseLocal() {
  if (localLeftPressed) {
    Mouse.release(MOUSE_LEFT);
    localLeftPressed = false;
  }
  if (localRightPressed) {
    Mouse.release(MOUSE_RIGHT);
    localRightPressed = false;
  }
}

bool sendPacket(const MousePacket& packet) {
  Wire.beginTransmission(kReceiverAddress);
  const size_t written = Wire.write(reinterpret_cast<const uint8_t*>(&packet), kPacketSize);
  const uint8_t result = Wire.endTransmission();
  if (kDebug && (written != kPacketSize || result != 0)) {
    Serial.println(F("I2C packet failure"));
  }
  return written == kPacketSize && result == 0;
}

void sendRemoteRelease() {
  const MousePacket packet = makeReleasePacket(sequence++);
  sendPacket(packet);
}

void releaseActiveTarget() {
  if (target == Target::LocalLaptop) {
    releaseLocal();
  } else {
    sendRemoteRelease();
  }
}

void toggleTarget() {
  if (target == Target::LocalLaptop) {
    releaseLocal();
    target = Target::RemoteLaptop;
  } else {
    sendRemoteRelease();
    target = Target::LocalLaptop;
  }
}

void applyReport(int8_t dx, int8_t dy, int8_t wheel, uint8_t buttons) {
  if (target == Target::LocalLaptop) {
    Mouse.move(dx, dy, wheel);
    setLocalButtons(buttons);
  } else {
    const MousePacket packet = makeReportPacket(sequence++, dx, dy, wheel, buttons);
    sendPacket(packet);
  }
}

void handleMouseFailure() {
  releaseActiveTarget();
  mouseConnected = false;
  previousMiddlePressed = false;
  lastReconnectAttemptMs = millis();
}

void setup() {
  releaseLine(kMouseDataPin);
  releaseLine(kMouseClockPin);
  Mouse.begin();
  Wire.begin();
  Wire.setWireTimeout(25000, true);
  if (kDebug) {
    Serial.begin(9600);
  }

  mouseConnected = initializeMouse();
  lastReconnectAttemptMs = millis();
  if (!mouseConnected) {
    releaseActiveTarget();
  }
}

void loop() {
  if (!mouseConnected) {
    const uint32_t now = millis();
    if (static_cast<uint32_t>(now - lastReconnectAttemptMs) >= kReconnectIntervalMs) {
      lastReconnectAttemptMs = now;
      mouseConnected = initializeMouse();
      previousMiddlePressed = false;
      if (!mouseConnected) {
        return;
      }
    }
    return;
  }

  uint8_t status = 0;
  int8_t rawX = 0;
  int8_t rawY = 0;
  int8_t rawWheel = 0;
  if (!pollMouse(status, rawX, rawY, rawWheel)) {
    handleMouseFailure();
    return;
  }

  const bool middlePressed = (status & 0x04) != 0;
  if (isMiddlePressEdge(middlePressed, previousMiddlePressed)) {
    toggleTarget();
  }
  previousMiddlePressed = middlePressed;

  const int8_t dx = rawX;
  const int8_t dy = negatePs2Delta(rawY);
  const int8_t wheel = negatePs2Delta(rawWheel);
  const uint8_t buttons = status & kAllowedButtonMask;
  applyReport(dx, dy, wheel, buttons);
}
