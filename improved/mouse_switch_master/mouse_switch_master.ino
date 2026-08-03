#include <Mouse.h>
#include <Wire.h>

#include "mouse_switch_protocol.h"
#include "mouse_switch_ps2_logic.h"

constexpr uint8_t kMouseDataPin = 9;
constexpr uint8_t kMouseClockPin = 10;
constexpr uint8_t kReceiverAddress = 0x02;
constexpr uint32_t kPs2EdgeTimeoutUs = 25000;
constexpr uint32_t kPs2BatTimeoutUs = 750000;
constexpr uint32_t kPs2DeviceIdTimeoutUs = 250000;
constexpr uint32_t kReconnectIntervalMs = 1000;
constexpr uint32_t kRemoteCreditTimeoutMs = 25;
constexpr uint8_t kPs2FailuresBeforeReconnect = 3;
constexpr uint8_t kSwitchSideButtonMask = 0x10;
constexpr bool kDebug = false;

enum class Target : uint8_t { LocalLaptop, RemoteLaptop };

struct MouseReport {
  uint8_t status;
  int16_t dx;
  int16_t dy;
  int8_t wheel;
  uint8_t buttons;
  uint8_t sideButtons;
};

Target target = Target::RemoteLaptop;
bool mouseConnected = false;
bool wheelAvailable = false;
uint8_t mouseId = 0x00;
bool previousSwitchButtonPressed = false;
bool localLeftPressed = false;
bool localRightPressed = false;
uint8_t sequence = 0;
uint8_t consecutivePs2Failures = 0;
uint32_t lastReconnectAttemptMs = 0;

void releaseLine(uint8_t pin) {
  pinMode(pin, INPUT_PULLUP);
}

void releasePs2Lines() {
  releaseLine(kMouseDataPin);
  releaseLine(kMouseClockPin);
}

bool failPs2Operation() {
  releasePs2Lines();
  return false;
}

void assertLineLow(uint8_t pin) {
  digitalWrite(pin, LOW);
  pinMode(pin, OUTPUT);
}

bool waitForPinLevel(uint8_t pin, uint8_t level) {
  const uint32_t startedAt = micros();
  while (digitalRead(pin) != level) {
    if (static_cast<uint32_t>(micros() - startedAt) >= kPs2EdgeTimeoutUs) {
      return failPs2Operation();
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

  // Request-to-send itself supplies START.  Once the mouse takes Clock low,
  // place bit 0 on DATA; the mouse samples it on the following rising edge.
  if (!waitForPinLevel(kMouseClockPin, LOW)) {
    return failPs2Operation();
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
      return failPs2Operation();
    }
  }

  if (parity != 0) {
    releaseLine(kMouseDataPin);
  } else {
    assertLineLow(kMouseDataPin);
  }
  if (!waitForPinLevel(kMouseClockPin, HIGH) ||
      !waitForPinLevel(kMouseClockPin, LOW)) {
    return failPs2Operation();
  }

  // Release DATA for STOP while Clock is low.  The mouse samples STOP on the
  // rising edge, asserts the transport ACK by taking DATA low while Clock is
  // high, then completes ACK on the following falling edge.
  releaseLine(kMouseDataPin);
  if (!waitForPinLevel(kMouseClockPin, HIGH) ||
      !waitForPinLevel(kMouseDataPin, LOW) ||
      !waitForPinLevel(kMouseClockPin, LOW)) {
    return failPs2Operation();
  }

  // Wait for idle, then inhibit Clock immediately.  This prevents a fast
  // command response from starting before ps2ReadByte() is ready for it.
  if (!waitForPinLevel(kMouseDataPin, HIGH) ||
      !waitForPinLevel(kMouseClockPin, HIGH)) {
    return failPs2Operation();
  }
  assertLineLow(kMouseClockPin);
  return true;
}

bool ps2ReadByte(uint8_t& value,
                 uint32_t firstEdgeTimeoutUs = kPs2EdgeTimeoutUs) {
  value = 0;
  uint8_t parity = 1;
  releaseLine(kMouseDataPin);
  releaseLine(kMouseClockPin);

  const uint32_t startedAt = micros();
  while (digitalRead(kMouseClockPin) != LOW) {
    if (static_cast<uint32_t>(micros() - startedAt) >= firstEdgeTimeoutUs) {
      return failPs2Operation();
    }
  }
  if (digitalRead(kMouseDataPin) != LOW ||
      !waitForPinLevel(kMouseClockPin, HIGH)) {
    return failPs2Operation();
  }

  for (uint8_t bit = 0; bit < 8; ++bit) {
    if (!waitForPinLevel(kMouseClockPin, LOW)) {
      return failPs2Operation();
    }
    const uint8_t dataBit = digitalRead(kMouseDataPin) == HIGH ? 1 : 0;
    value |= static_cast<uint8_t>(dataBit << bit);
    parity ^= dataBit;
    if (!waitForPinLevel(kMouseClockPin, HIGH)) {
      return failPs2Operation();
    }
  }

  if (!waitForPinLevel(kMouseClockPin, LOW)) {
    return failPs2Operation();
  }
  const bool parityBitHigh = digitalRead(kMouseDataPin) == HIGH;
  if (!waitForPinLevel(kMouseClockPin, HIGH) ||
      parityBitHigh != (parity != 0)) {
    return failPs2Operation();
  }

  if (!waitForPinLevel(kMouseClockPin, LOW)) {
    return failPs2Operation();
  }
  const bool stopBitHigh = digitalRead(kMouseDataPin) == HIGH;
  if (!waitForPinLevel(kMouseClockPin, HIGH) || !stopBitHigh) {
    return failPs2Operation();
  }
  // Hold the device between bytes.  Multi-byte reset and report responses can
  // otherwise begin before the next ps2ReadByte() call reaches its first wait.
  releaseLine(kMouseDataPin);
  assertLineLow(kMouseClockPin);
  return true;
}

bool sendCommand(uint8_t command) {
  uint8_t acknowledgement = 0;
  if (!ps2WriteByte(command) || !ps2ReadByte(acknowledgement) ||
      acknowledgement != 0xFA) {
    return failPs2Operation();
  }
  return true;
}

bool setSampleRate(uint8_t rate) {
  if (!sendCommand(0xF3) || !sendCommand(rate)) {
    return failPs2Operation();
  }
  return true;
}

bool readMouseId(uint8_t& deviceId) {
  if (!sendCommand(0xF2) || !ps2ReadByte(deviceId)) {
    return failPs2Operation();
  }
  return true;
}

bool initializeMouse() {
  uint8_t response = 0;
  if (!ps2WriteByte(0xFF) || !ps2ReadByte(response) || response != 0xFA ||
      !ps2ReadByte(response, kPs2BatTimeoutUs) || response != 0xAA ||
      !ps2ReadByte(response, kPs2DeviceIdTimeoutUs)) {
    return failPs2Operation();
  }

  if (!setSampleRate(200) || !setSampleRate(100) || !setSampleRate(80) ||
      !readMouseId(response)) {
    return failPs2Operation();
  }

  if (shouldAttemptExplorerUpgrade(response)) {
    if (!setSampleRate(200) || !setSampleRate(200) || !setSampleRate(80) ||
        !readMouseId(response)) {
      return failPs2Operation();
    }
  }

  mouseId = response;
  wheelAvailable = mouseId == 0x03 || mouseId == 0x04;
  if (response != 0x00 && !wheelAvailable) {
    return failPs2Operation();
  }

  if (!sendCommand(0xE8) || !sendCommand(0x03) || !sendCommand(0xE6) ||
      !setSampleRate(40) || !sendCommand(0xF4) || !sendCommand(0xF0)) {
    return failPs2Operation();
  }
  return true;
}

bool pollMouse(MouseReport& report) {
  uint8_t status = 0;
  uint8_t rawX = 0;
  uint8_t rawY = 0;
  uint8_t rawWheel = 0;
  if (!sendCommand(0xEB) || !ps2ReadByte(status) || !ps2ReadByte(rawX) ||
      !ps2ReadByte(rawY)) {
    return failPs2Operation();
  }

  if (wheelAvailable && !ps2ReadByte(rawWheel)) {
    return failPs2Operation();
  }

  const bool xSignSet = (status & 0x10) != 0;
  const bool ySignSet = (status & 0x20) != 0;
  const int16_t ps2X = (status & 0x40) != 0
                           ? (xSignSet ? -256 : 255)
                           : decodePs2Axis(rawX, xSignSet);
  const int16_t ps2Y = (status & 0x80) != 0
                           ? (ySignSet ? -256 : 255)
                           : decodePs2Axis(rawY, ySignSet);

  report.status = status;
  report.dx = ps2X;
  report.dy = negatePs2Axis(ps2Y);
  report.wheel = 0;
  if (wheelAvailable) {
    report.wheel = hidDisplacementChunk(
        negatePs2Axis(static_cast<int16_t>(decodePs2Wheel(mouseId, rawWheel))));
  }
  report.buttons = status & kAllowedButtonMask;
  report.sideButtons = decodePs2SideButtons(mouseId, rawWheel);
  return true;
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

bool waitForRemoteCredit() {
  const uint32_t startedAt = millis();
  do {
    const uint8_t received =
        Wire.requestFrom(kReceiverAddress, static_cast<uint8_t>(1));
    if (received != 1 || Wire.available() != 1) {
      if (Wire.available()) {
        Wire.read();
      }
      return false;
    }

    const int freeSlots = Wire.read();
    if (freeSlots > 0) {
      return true;
    }
  } while (static_cast<uint32_t>(millis() - startedAt) <
           kRemoteCreditTimeoutMs);

  return false;
}

bool sendRemotePacket(const MousePacket& packet) {
  if (!waitForRemoteCredit()) {
    if (kDebug) {
      Serial.println(F("I2C credit failure"));
    }
    return false;
  }

  Wire.beginTransmission(kReceiverAddress);
  const size_t written = Wire.write(reinterpret_cast<const uint8_t*>(&packet), kPacketSize);
  const uint8_t result = Wire.endTransmission();
  if (kDebug && (written != kPacketSize || result != 0)) {
    Serial.println(F("I2C packet failure"));
  }
  return written == kPacketSize && result == 0;
}

bool sendRemoteRelease() {
  const MousePacket packet = makeReleasePacket(sequence++);
  return sendRemotePacket(packet);
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

void applyReport(const MouseReport& report) {
  if (target == Target::LocalLaptop) {
    setLocalButtons(report.buttons);
  }

  int16_t remainingX = report.dx;
  int16_t remainingY = report.dy;
  bool firstChunk = true;
  while (remainingX != 0 || remainingY != 0 ||
         (firstChunk &&
          (report.wheel != 0 || target == Target::RemoteLaptop))) {
    const int8_t dx = hidDisplacementChunk(remainingX);
    const int8_t dy = hidDisplacementChunk(remainingY);
    const int8_t wheel = firstChunk ? report.wheel : 0;

    if (target == Target::LocalLaptop) {
      Mouse.move(dx, dy, wheel);
    } else {
      const MousePacket packet =
          makeReportPacket(sequence++, dx, dy, wheel, report.buttons);
      sendRemotePacket(packet);
    }

    remainingX -= dx;
    remainingY -= dy;
    firstChunk = false;
  }
}

void handleMouseFailure() {
  releasePs2Lines();
  releaseActiveTarget();
  mouseConnected = false;
  previousSwitchButtonPressed = false;
  consecutivePs2Failures = 0;
  lastReconnectAttemptMs = millis();
}

void setup() {
  releasePs2Lines();
  Mouse.begin();
  Wire.begin();
  Wire.setWireTimeout(25000, true);
  if (kDebug) {
    Serial.begin(9600);
  }

  mouseConnected = initializeMouse();
  lastReconnectAttemptMs = millis();
  if (!mouseConnected) {
    releasePs2Lines();
    releaseActiveTarget();
  }
}

void loop() {
  if (!mouseConnected) {
    const uint32_t now = millis();
    if (static_cast<uint32_t>(now - lastReconnectAttemptMs) >= kReconnectIntervalMs) {
      lastReconnectAttemptMs = now;
      mouseConnected = initializeMouse();
      previousSwitchButtonPressed = false;
      consecutivePs2Failures = 0;
      if (!mouseConnected) {
        releasePs2Lines();
        return;
      }
    }
    return;
  }

  MouseReport report = {0, 0, 0, 0, 0, 0};
  if (!pollMouse(report)) {
    releasePs2Lines();
    if (consecutivePs2Failures < UINT8_MAX) {
      ++consecutivePs2Failures;
    }
    if (shouldReconnectAfterPs2Failure(consecutivePs2Failures,
                                       kPs2FailuresBeforeReconnect)) {
      handleMouseFailure();
    }
    return;
  }
  consecutivePs2Failures = 0;

  const bool switchButtonPressed =
      isPs2SideButtonPressed(report.sideButtons, kSwitchSideButtonMask);
  if (isMiddlePressEdge(switchButtonPressed, previousSwitchButtonPressed)) {
    toggleTarget();
  }
  previousSwitchButtonPressed = switchButtonPressed;

  applyReport(report);
}
