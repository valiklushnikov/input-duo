"""Спайк macOS: зовёт ли Qt retrieveData лениво — только когда pasteboard
читает другой процесс, — а не сразу при setMimeData?

Не тест. Запускается руками на настоящей сессии macOS (нужен реальный цикл
событий и оконный сервер, без offscreen):
    .venv-mac/bin/python configurator/tests/clipboard/spike_lazy_mimedata_macos.py

Почему не просто прочитать буфер в этом же процессе: если положить в
NSPasteboard подкласс QMimeData и прочитать его же здесь, Qt отдаст тот же
Python-объект напрямую — это ничего не доказывает про отложенную отдачу
macOS. На macOS отложенные данные предоставляются через provider
(promiseKeeper у Qt), которого система дёргает в процессе-владельце по IPC,
когда данные запрашивает ДРУГОЙ процесс. Поэтому:

  Часть A (автоматически). Этот процесс (Qt) кладёт в буфер подкласс QMimeData
  со счётчиком вызовов retrieveData, затем — уже внутри работающего цикла
  событий, чтобы успеть ответить на запрос данных, — запускает `pbpaste`
  (обычная утилита macOS, отдельный процесс, читает NSPasteboard через
  AppKit). Скрипт печатает, сколько раз retrieveData вызван до и после этого
  межпроцессного чтения.

  Часть B (вручную). После автопроверки окно остаётся открытым: вставьте
  (⌘V) в TextEdit, Notes, Safari/Chrome — и смотрите в консоли, когда именно
  дёргается retrieveData и какой формат запрашивается. Через 40 секунд окно
  закроется само.

pbpaste читает текст (public.utf8-plain-text), поэтому спайк объявляет
text/plain — этого достаточно, чтобы установить сам факт ленивости.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtWidgets import QApplication

CALLS: list[str] = []
LAZY_PAYLOAD = "lazy payload macos"


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
        print(f"calls right before launching pbpaste: {calls_before_read}", flush=True)
        state["calls_before_read"] = calls_before_read

        # Popen + poll, а не блокирующий run: pbpaste может ждать, пока наш
        # процесс ответит на запрос отложенных данных, а ответить мы можем
        # только пока крутится цикл событий Qt. Блокирующий вызов тут дал бы
        # взаимную блокировку. Между тиками управление возвращается в event loop.
        state["process"] = subprocess.Popen(
            ["/usr/bin/pbpaste"],
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
                return  # pbpaste ещё работает, ждём следующий тик

            poll_timer.stop()
            stdout, stderr = process.communicate()
            print("--- pbpaste stdout ---", flush=True)
            print(repr(stdout), flush=True)
            if stderr:
                print("--- pbpaste stderr ---", flush=True)
                print(stderr, flush=True)
            print(f"--- pbpaste exit code: {process.returncode} ---", flush=True)

            calls_after_read = len(CALLS)
            calls_before_read = state["calls_before_read"]
            print(f"calls right after pbpaste read: {calls_after_read}", flush=True)
            print(
                f"DELTA caused by the cross-process read: "
                f"{calls_after_read - calls_before_read}",
                flush=True,
            )
            got_lazy = stdout == LAZY_PAYLOAD
            print(
                "VERDICT part A: "
                + (
                    "LAZY — retrieveData fired only on the cross-process read"
                    if calls_after_set == 0 and calls_after_read > calls_before_read
                    else "EAGER — data was materialised before any external read"
                    if calls_after_set > 0
                    else "UNCLEAR — see counts above"
                )
                + f" (pbpaste got our payload: {got_lazy})",
                flush=True,
            )
            print(
                "\nNow paste (Cmd+V) into TextEdit, Notes, Safari/Chrome. "
                "Window closes in 40s.",
                flush=True,
            )

        poll_timer.timeout.connect(check_reader)
        poll_timer.start()

    manual_only = "--manual-only" in sys.argv
    if manual_only:
        print(
            "MANUAL-ONLY run: no pbpaste. Paste (Cmd+V) into TextEdit, then Notes, "
            "then Safari/Chrome, in that order. Watch which paste first fires "
            "retrieveData('text/plain'). Window closes in 95s.",
            flush=True,
        )
    else:
        QTimer.singleShot(500, start_cross_process_read)
    QTimer.singleShot(95_000, application.quit)
    application.exec()

    print(f"total retrieveData calls: {len(CALLS)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
