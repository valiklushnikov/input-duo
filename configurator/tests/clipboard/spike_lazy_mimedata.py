"""Спайк: зовёт ли Windows retrieveData лениво, только когда буфер читает
другой процесс — а не сразу при setMimeData?

Не тест. Запускается руками на настоящей Windows-сессии (без QT_QPA_PLATFORM,
нужен реальный цикл событий и реальная сессия):
    cd configurator && ../.venv/Scripts/python.exe tests/clipboard/spike_lazy_mimedata.py

Почему не просто вставить в Блокнот руками: если положить в буфер подкласс
QMimeData и прочитать его же в ЭТОМ ЖЕ процессе, Qt отдаст тот же Python-
объект напрямую — это ничего не доказывает про отложенную отрисовку Windows
(WM_RENDERFORMAT). Отложенную отрисовку запускает только чтение буфера ИЗ
ДРУГОГО процесса через голый Win32 API. Поэтому этот скрипт (процесс A, Qt)
кладёт в буфер обмена подкласс QMimeData со счётчиком вызовов retrieveData,
затем — уже внутри работающего цикла событий Qt, чтобы процесс мог ответить
на WM_RENDERFORMAT, — запускает процесс B (tests/clipboard/spike_read_clipboard.py,
обычный python + ctypes, без Qt), который читает CF_UNICODETEXT напрямую
через user32/kernel32. Скрипт печатает, сколько раз retrieveData был вызван
до и после этого межпроцессного чтения.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtWidgets import QApplication

CALLS: list[str] = []
LAZY_PAYLOAD = "lazy payload"


class LazyMimeData(QMimeData):
    def formats(self):  # noqa: N802 - Qt API
        return ["text/plain"]

    def retrieveData(self, mime_type, preferred_type):  # noqa: N802 - Qt API
        CALLS.append(mime_type)
        print(
            f"[{time.monotonic():.3f}] retrieveData({mime_type!r}) called, "
            f"total calls so far: {len(CALLS)}",
            flush=True,
        )
        return LAZY_PAYLOAD


def main() -> int:
    application = QApplication(sys.argv)
    clipboard = application.clipboard()
    clipboard.setMimeData(LazyMimeData())

    calls_after_set = len(CALLS)
    print(f"calls right after setMimeData: {calls_after_set}", flush=True)

    state: dict[str, object] = {}

    def start_cross_process_read() -> None:
        calls_before_read = len(CALLS)
        print(f"calls right before launching process B: {calls_before_read}", flush=True)
        state["calls_before_read"] = calls_before_read

        reader_script = Path(__file__).with_name("spike_read_clipboard.py")
        # ВАЖНО: Popen, а не subprocess.run/communicate. GetClipboardData в
        # процессе B блокируется внутри Windows, ожидая, что процесс A
        # ответит на WM_RENDERFORMAT. Процесс A может ответить на это
        # оконное сообщение, только если его цикл событий Qt крутится. Если
        # тут вызвать блокирующий subprocess.run, поток процесса A застрянет
        # в ожидании процесса B, а процесс B — в ожидании процесса A:
        # взаимная блокировка (мы её и получили при первом прогоне: process B
        # висел 15 секунд и упал по TimeoutExpired). Poll вместо wait —
        # чтобы между проверками управление возвращалось в цикл событий Qt.
        state["process"] = subprocess.Popen(
            [sys.executable, str(reader_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        poll_timer = QTimer()
        state["poll_timer"] = poll_timer
        poll_timer.setInterval(50)

        def check_reader() -> None:
            process = state["process"]
            assert isinstance(process, subprocess.Popen)
            if process.poll() is None:
                return  # процесс B ещё работает, ждём следующий тик

            poll_timer.stop()
            stdout, stderr = process.communicate()
            print("--- process B stdout ---", flush=True)
            print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)
            if stderr:
                print("--- process B stderr ---", flush=True)
                print(stderr, flush=True)
            print(f"--- process B exit code: {process.returncode} ---", flush=True)

            calls_after_read = len(CALLS)
            calls_before_read = state["calls_before_read"]
            print(f"calls right after process B read: {calls_after_read}", flush=True)
            print(
                f"DELTA caused by the cross-process read: "
                f"{calls_after_read - calls_before_read}",
                flush=True,
            )
            application.quit()

        poll_timer.timeout.connect(check_reader)
        poll_timer.start()

    # Запускаем процесс B не раньше application.exec(), а из QTimer.singleShot
    # внутри уже работающего цикла событий — иначе процесс A не сможет
    # ответить на WM_RENDERFORMAT, пока Windows будет ждать данные.
    QTimer.singleShot(500, start_cross_process_read)
    # Аварийный предохранитель: если что-то зависнет (например, процесс B
    # так и не дождётся ответа), не висеть вечно.
    QTimer.singleShot(20_000, application.quit)
    application.exec()

    print(f"total retrieveData calls: {len(CALLS)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
