"""The one part of the HIL runner that needs a board on the bench.

Kept apart from ``hil_runner`` on purpose. Everything that decides a check, and
every piece of arithmetic behind one, is testable without hardware; this file is
the seam where that stops being true, and it is deliberately thin so that the
untestable part is small enough to read.

``DeviceService`` is asynchronous - it has to be, because it lives inside a Qt
application that must never stop answering the host - and this runner is a
script that wants an answer before it goes on. So each call here spins the Qt
event loop until the service reports an outcome, and reports whichever outcome
arrived. Waiting only for success is how a refusal answered in two milliseconds
became an anonymous five-second timeout once already in this project.
"""

from __future__ import annotations


class DeviceRefused(RuntimeError):
    """The device answered, and the answer was a refusal."""


class DeviceSilent(RuntimeError):
    """The device did not answer at all."""


class CdcSession:
    """One conversation with an attached U1, driven synchronously."""

    def __init__(self, port: str, timeout_ms: int = 5000) -> None:
        self._port = port
        self._timeout_ms = timeout_ms
        self._app = None
        self._service = None

    @property
    def port(self) -> str:
        return self._port

    def open(self) -> None:
        from hil_runner import HardwareRequired

        try:
            from PySide6.QtCore import QCoreApplication
            from duo_input.device.qt_transport import QSerialPortTransport
            from duo_input.device.service import DeviceService
        except ImportError as error:
            # A missing dependency and a missing board are different problems,
            # but both leave the caller with no device, and both must arrive as
            # the runner's own refusal rather than as a traceback that looks
            # like a defect in the scenario.
            raise HardwareRequired(
                "the configurator and PySide6 must be installed to reach a real "
                f"U1: {error}"
            ) from error

        self._app = QCoreApplication.instance() or QCoreApplication([])
        self._service = DeviceService(timeout_ms=self._timeout_ms)
        transport = QSerialPortTransport(self._port)
        try:
            self._wait(lambda: self._service.connect_device(transport), 10000)
        except (DeviceRefused, DeviceSilent) as error:
            # Raised as the runner's own refusal, because "this port is not a
            # U1" and "this scenario has no rig" are the same fact to a caller.
            raise HardwareRequired(f"{self._port} did not answer as a U1: {error}") from error

    def diagnostics(self):
        """One GET_DIAGNOSTICS, and the parsed counters it answered with."""
        self._wait(self._service.get_diagnostics, self._timeout_ms)
        return self._service.diagnostics

    def close(self) -> None:
        if self._service is not None:
            self._service.disconnect_device()

    def _wait(self, call, timeout_ms: int):
        """Run ``call`` and return its result, or say why there is not one.

        Both outcomes are listened for. A device that refuses in two
        milliseconds and a device that was never plugged in are different
        facts, and a wait that listens only for success reports them as the
        same one.
        """
        from PySide6.QtCore import QEventLoop, QTimer

        outcomes: list[tuple[str, object]] = []
        loop = QEventLoop()

        def finish(kind: str, value: object) -> None:
            outcomes.append((kind, value))
            loop.quit()

        on_success = lambda value: finish("succeeded", value)  # noqa: E731
        on_failure = lambda value: finish("failed", value)  # noqa: E731

        self._service.operation_succeeded.connect(on_success)
        self._service.operation_failed.connect(on_failure)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        try:
            call()
            if not outcomes:
                timer.start(timeout_ms)
                loop.exec()
        finally:
            self._service.operation_succeeded.disconnect(on_success)
            self._service.operation_failed.disconnect(on_failure)
            timer.stop()

        if not outcomes:
            raise DeviceSilent(
                f"the device neither answered nor failed within {timeout_ms} ms"
            )
        kind, value = outcomes[0]
        if kind == "failed":
            named = value.error_code.name if value.error_code is not None else ""
            raise DeviceRefused(
                f"{value.operation} failed: {value.reason.value}"
                + (f" ({named})" if named else "")
                + (f" - {value.detail}" if value.detail else "")
            )
        return value
