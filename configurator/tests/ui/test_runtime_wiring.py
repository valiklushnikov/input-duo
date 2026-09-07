"""Production path: what main assembles, not only isolated test objects."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from duo_input import app as app_module
from duo_input.app import build_main_window, configure_runtime, single_instance_lock
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.i18n import TranslationManager


def _settings(tmp_path, enabled: bool) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("clipboard/enabled", enabled)
    return settings


def _candidate(
    *, fingerprint: str = "f" * 64, machine_name: str = "LAPTOP-TWO"
) -> PairingCandidate:
    return PairingCandidate(
        origin_id="2" * 32,
        machine_name=machine_name,
        fingerprint=fingerprint,
        address="192.168.1.5",
        port=47654,
    )


def _click_role(dialog: QMessageBox, role: QMessageBox.ButtonRole) -> int:
    button = next(
        button for button in dialog.buttons() if dialog.buttonRole(button) is role
    )
    button.click()
    return 0


def test_nothing_is_built_while_sharing_is_off(qtbot, qapp, tmp_path, monkeypatch):
    """Подсистема общего буфера не собирается, пока фича выключена - но, в
    отличие от прежнего правила, ``TrayIcon`` в этот список запретов больше не
    входит: решение продукта от 2026-09-03 (§4) требует трей всегда, поэтому
    его конструктор здесь не запрещён, а проверен отдельным тестом ниже."""
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the disabled path constructed a clipboard runtime object")

    for name in (
        "load_or_create",
        "TrustStore",
        "ClipboardCoordinator",
        "WindowsClipboardBackend",
    ):
        monkeypatch.setattr(app_module, name, forbidden, raising=False)

    assert configure_runtime(qapp, window, _settings(tmp_path, False)) is None


def test_the_tray_icon_exists_even_while_sharing_is_off(qtbot, qapp, tmp_path, monkeypatch):
    """КРИТИЧНО (§4, решение продукта 2026-09-03): без всегда-живого трея
    окно, которое теперь всегда прячется по закрытию, стало бы недоступным
    при выключенном общем буфере - показать нечем, выйти нечем."""
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)
    configure_runtime(qapp, window, settings)

    assert len(trays) == 1
    # Галочка отражает настоящую сохранённую настройку, а не принудительное
    # "включено" (старый дефект: трей раньше ставился только вместе с
    # подсистемой, поэтому его галочка была жёстко True).
    assert trays[0].sharing_action.isChecked() is False


def test_no_socket_listens_while_sharing_starts_disabled(qtbot, qapp, tmp_path):
    """§4: пока общий буфер выключен, ни один сокет не открывается - включая
    тот случай, когда фича никогда не включалась в этом запуске вовсе, а не
    только когда её выключили после включения (это уже покрыто
    ``test_switching_off_actually_releases_the_listening_socket``)."""
    from PySide6.QtNetwork import QTcpServer

    from duo_input.clipboard.coordinator import TCP_PORT

    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    configure_runtime(qapp, window, settings)

    probe = QTcpServer()
    try:
        assert probe.listen(port=TCP_PORT) is True, "порт должен быть свободен"
    finally:
        probe.close()


def test_the_tray_checkbox_starts_and_stops_the_subsystem_from_a_cold_start(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Требование продукта: галочка в трее обязана работать в обе стороны
    даже тогда, когда подсистема ни разу не запускалась в этом сеансе -
    старт через настоящий пункт меню трея, а не через запись настройки."""
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)
    configure_runtime(qapp, window, settings)

    tray = trays[0]
    assert tray.sharing_action.isChecked() is False

    tray.sharing_action.trigger()  # то же самое, что клик по пункту меню - вкл.

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is True
    assert window.clipboard_page.sharing_checkbox.isChecked() is True
    assert tray.sharing_action.isChecked() is True

    tray.sharing_action.trigger()  # повторный клик - выкл.

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False
    assert window.clipboard_page.sharing_checkbox.isChecked() is False
    assert tray.sharing_action.isChecked() is False


def test_the_coordinator_is_built_when_sharing_is_on(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)

    assert coordinator is not None
    coordinator.service._backend.stop()
    coordinator.stop()


def test_switching_sharing_on_with_the_real_checkbox_stops_quitting_with_the_window(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: включение через настоящий переключатель обязано поднять подсистему.

    До исправления (M5 ревью) этот тест сам клал ``clipboard/enabled`` в
    настройки и звал ``configure_runtime`` напрямую - он проходил даже когда
    ``sharing_toggled`` был не подключён ни к чему, потому что ни разу не
    трогал сам чекбокс. Здесь подсистема стартует выключенной, и единственный
    способ её включить - реальный виджет на странице.
    """
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    window.clipboard_page.sharing_checkbox.setChecked(True)

    assert qapp.quitOnLastWindowClosed() is False
    assert bool(settings.value("clipboard/enabled", False, type=bool)) is True
    # Трей и страница обязаны показывать одно и то же состояние (C1).
    assert window.clipboard_page.sharing_checkbox.isChecked() is True

    # Выключение тем же путём обязано по-настоящему остановить подсистему -
    # а не только сменить надпись (§4).
    window.clipboard_page.sharing_checkbox.setChecked(False)

    assert qapp.quitOnLastWindowClosed() is True
    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False


def test_switching_off_actually_releases_the_listening_socket(qtbot, qapp, tmp_path, monkeypatch):
    """C1: выключение обязано ДЕЙСТВИТЕЛЬНО остановить координатора, а не
    только сменить надпись - иначе порт остался бы занятым навсегда."""
    from PySide6.QtNetwork import QTcpServer

    from duo_input.clipboard.coordinator import TCP_PORT

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    window.clipboard_page.sharing_checkbox.setChecked(True)
    probe = QTcpServer()
    assert probe.listen(port=TCP_PORT) is False, "порт должен быть занят, пока фича включена"

    window.clipboard_page.sharing_checkbox.setChecked(False)
    probe2 = QTcpServer()
    try:
        assert probe2.listen(port=TCP_PORT) is True, "выключение обязано освободить порт"
    finally:
        probe2.close()


def test_switching_off_disconnects_page_controls_from_the_stopped_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """With sharing off, Pair/address edits must not revive the old runtime."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinators = []
    real_coordinator = app_module.ClipboardCoordinator

    def capturing_coordinator(*args, **kwargs):
        coordinator = real_coordinator(*args, **kwargs)
        coordinators.append(coordinator)
        return coordinator

    monkeypatch.setattr(app_module, "ClipboardCoordinator", capturing_coordinator)
    configure_runtime(qapp, window, settings)
    window.clipboard_page.sharing_checkbox.setChecked(True)
    coordinator = coordinators[0]

    window.clipboard_page.sharing_checkbox.setChecked(False)
    window.clipboard_page.pair_button.click()
    window.clipboard_page.address_field.setText("192.168.1.42")
    window.clipboard_page.address_field.editingFinished.emit()

    try:
        assert coordinator._discovery._timer.isActive() is False
        assert coordinator.state.value == "unpaired"
        assert coordinator._manual_address == ""
    finally:
        coordinator.stop()


def test_repeated_enable_cycles_destroy_each_stopped_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Enable/disable cycles must not accumulate QApplication-owned runtimes."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinators = []
    destroyed: list[int] = []
    real_coordinator = app_module.ClipboardCoordinator

    def capturing_coordinator(*args, **kwargs):
        coordinator = real_coordinator(*args, **kwargs)
        coordinators.append(coordinator)
        coordinator.destroyed.connect(
            lambda _object=None, number=len(coordinators): destroyed.append(number)
        )
        return coordinator

    monkeypatch.setattr(app_module, "ClipboardCoordinator", capturing_coordinator)
    configure_runtime(qapp, window, settings)

    for _ in range(3):
        window.clipboard_page.sharing_checkbox.setChecked(True)
        coordinator = coordinators[-1]
        window.clipboard_page.sharing_checkbox.setChecked(False)
        QCoreApplication.sendPostedEvents(coordinator, QEvent.Type.DeferredDelete)

    assert len(coordinators) == 3
    assert destroyed == [1, 2, 3]


def test_the_tray_checkbox_toggle_stops_the_subsystem_and_the_page_agrees(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: трей и страница подключены к одному переключателю в обе стороны."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)

    configure_runtime(qapp, window, settings)
    window.clipboard_page.sharing_checkbox.setChecked(True)

    assert len(trays) == 1
    tray = trays[0]
    assert tray.sharing_action.isChecked() is True

    tray.sharing_action.trigger()  # то же самое, что клик по пункту меню

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False
    assert window.clipboard_page.sharing_checkbox.isChecked() is False
    assert qapp.quitOnLastWindowClosed() is True


def test_a_manual_address_typed_on_the_page_reaches_the_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: поле ручного адреса должно быть подключено к координатору."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)

    window.clipboard_page.address_field.setText("192.168.1.42")
    window.clipboard_page.address_field.editingFinished.emit()

    assert coordinator._manual_address == "192.168.1.42"
    coordinator.service._backend.stop()
    coordinator.stop()


def test_toggling_autostart_on_the_page_writes_the_shortcut(qtbot, qapp, tmp_path, monkeypatch):
    """C2: автозапуск - мёртвый код, пока переключатель к нему не подключён."""
    from duo_input.persistence import autostart

    startup_dir = tmp_path / "startup"
    monkeypatch.setattr(autostart, "startup_directory", lambda: startup_dir)
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path / "app")
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    window.clipboard_page.autostart_checkbox.setChecked(True)

    assert autostart.is_enabled() is True
    assert bool(settings.value("clipboard/autostart", False, type=bool)) is True
    content = autostart.shortcut_path().read_text("utf-8")
    assert autostart.HIDDEN_START_ARGUMENT in content

    window.clipboard_page.autostart_checkbox.setChecked(False)

    assert autostart.is_enabled() is False
    assert bool(settings.value("clipboard/autostart", False, type=bool)) is False


def test_settings_read_at_startup_show_the_same_state_on_page_and_tray(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: страница никогда не читала своё состояние - трей выставлялся
    принудительно checked=True, вне зависимости от настроек."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    settings.setValue("clipboard/autostart", True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)

    coordinator = configure_runtime(qapp, window, settings)

    assert window.clipboard_page.sharing_checkbox.isChecked() is True
    assert window.clipboard_page.autostart_checkbox.isChecked() is True
    assert trays[0].sharing_action.isChecked() is True
    coordinator.service._backend.stop()
    coordinator.stop()


def test_identity_failure_degrades_to_sharing_disabled_without_crashing(
    qtbot, qapp, tmp_path, monkeypatch
):
    """I6: отказ файлов идентичности не должен ронять всё приложение."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)

    def _broken_load_or_create(directory):
        raise OSError("нет доступа")

    monkeypatch.setattr(app_module, "load_or_create", _broken_load_or_create)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    # Не должно бросить исключение наружу.
    window.clipboard_page.sharing_checkbox.setChecked(True)

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False
    assert window.clipboard_page.sharing_checkbox.isChecked() is False
    assert window.clipboard_page.events_list.count() >= 1


def test_a_state_change_is_recorded_in_the_recent_events_list(qtbot, qapp, tmp_path, monkeypatch):
    """I7: страница должна показывать последние события - иначе увидеть, что
    произошло, негде вообще (то же ревью отметило это для I5)."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)

    coordinator.event_logged.emit("тестовое событие")

    assert window.clipboard_page.events_list.item(0).text() == "тестовое событие"
    coordinator.service._backend.stop()
    coordinator.stop()


def test_pairing_dialog_accepts_the_exact_candidate_and_six_digit_code(
    qtbot, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    candidate = _candidate(machine_name="DESKTOP-ALPHA")
    shown: list[QMessageBox] = []
    confirmed: list[PairingCandidate] = []

    def accept(dialog):
        shown.append(dialog)
        return _click_role(dialog, QMessageBox.ButtonRole.AcceptRole)

    monkeypatch.setattr(QMessageBox, "exec", accept)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(coordinator, "confirm_pairing", confirmed.append)

    coordinator.pairing_code_ready.emit("004271", candidate)

    assert shown[0].parent() is window
    assert "DESKTOP-ALPHA" in shown[0].text()
    assert "004271" in shown[0].text()
    assert confirmed == [candidate]
    coordinator.service._backend.stop()
    coordinator.stop()


def test_pairing_dialog_rejects_the_exact_candidate(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    candidate = _candidate()
    rejected: list[PairingCandidate] = []

    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda dialog: _click_role(dialog, QMessageBox.ButtonRole.RejectRole),
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(coordinator, "reject_pairing", rejected.append, raising=False)

    coordinator.pairing_code_ready.emit("918205", candidate)

    assert rejected == [candidate]
    coordinator.service._backend.stop()
    coordinator.stop()


def test_pairing_dialog_renders_an_untrusted_machine_name_as_plain_text(qtbot, qapp):
    window = build_main_window(transport_factory=lambda: None)
    qtbot.addWidget(window)
    candidate = _candidate(machine_name="<b>NOT THE REAL NAME</b>")

    dialog, _accept_button = app_module._pairing_confirmation_dialog(
        window, "004271", candidate
    )

    assert dialog.textFormat() is Qt.TextFormat.PlainText
    assert "<b>NOT THE REAL NAME</b>" in dialog.text()
    assert "004271" in dialog.text()
    dialog.deleteLater()


@pytest.mark.parametrize(
    ("language", "accept_text", "reject_text"),
    (
        ("ru", "Связать", "Отказать"),
        ("en", "Pair", "Reject"),
    ),
)
def test_pairing_dialog_owns_localized_button_labels(
    qtbot, qapp, tmp_path, language, accept_text, reject_text
):
    manager = TranslationManager(
        qapp,
        settings=QSettings(
            str(tmp_path / f"{language}.ini"), QSettings.Format.IniFormat
        ),
    )
    assert manager.set_language(language, remember=False) is True
    window = build_main_window(translations=manager, transport_factory=lambda: None)
    qtbot.addWidget(window)

    dialog, accept_button = app_module._pairing_confirmation_dialog(
        window, "918205", _candidate()
    )
    labels = {
        dialog.buttonRole(button): button.text()
        for button in dialog.buttons()
    }

    assert dialog.standardButtons() == QMessageBox.StandardButton.NoButton
    assert labels[QMessageBox.ButtonRole.AcceptRole] == accept_text
    assert labels[QMessageBox.ButtonRole.RejectRole] == reject_text
    assert accept_button.text() == accept_text
    dialog.deleteLater()


def test_the_second_instance_cannot_take_the_lock(qapp):
    first = single_instance_lock("duo-input-test-lock")
    second = single_instance_lock("duo-input-test-lock")

    assert first is not None
    assert second is None

    first.close()


def test_a_second_instance_connecting_raises_the_first_window(qtbot, qapp):
    """I7: второй запуск обязан поднять окно первого, а не молча завершиться."""
    from PySide6.QtNetwork import QLocalSocket

    lock = single_instance_lock("duo-input-test-raise")
    assert lock is not None
    window = build_main_window(transport_factory=lambda: None)
    qtbot.addWidget(window)
    window.hide()
    lock.newConnection.connect(lambda: app_module._raise_existing_window(lock, window))

    second_instance_probe = QLocalSocket()
    second_instance_probe.connectToServer("duo-input-test-raise")
    assert second_instance_probe.waitForConnected(1000) is True

    qtbot.waitUntil(lambda: window.isVisible(), timeout=2000)

    second_instance_probe.disconnectFromServer()
    lock.close()


class _FakeSignal:
    def connect(self, callback) -> None:
        pass


class _FakeLock:
    """Достаточно замка, чтобы main() мог подписаться на newConnection."""

    newConnection = _FakeSignal()


def test_main_calls_configure_runtime(qapp, monkeypatch):
    """A helper main never reaches is missing production behavior."""
    called: list[bool] = []

    monkeypatch.setattr(app_module, "configure_runtime", lambda *args: called.append(True))
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *args, **kwargs: _FakeLock())
    monkeypatch.setattr(app_module, "start_window", lambda window, **kwargs: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    app_module.main([])

    assert called == [True]


def test_main_survives_an_unexpected_configure_runtime_failure(qapp, monkeypatch):
    """I6: последний рубеж - main() не должен упасть, даже если подсистема
    сломалась непредвиденно (не только предвиденным OSError на файлах
    идентичности, который ловит сам _start())."""

    def _boom(*_args):
        raise RuntimeError("непредвиденный сбой")

    monkeypatch.setattr(app_module, "configure_runtime", _boom)
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *args, **kwargs: _FakeLock())
    monkeypatch.setattr(app_module, "start_window", lambda window, **kwargs: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    result = app_module.main([])

    assert result == 0
