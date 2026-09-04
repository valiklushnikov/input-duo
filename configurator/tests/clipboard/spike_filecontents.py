"""SPIKE (одноразовый, не production): можно ли из Qt-приложения на Python
положить в буфер обмена IDataObject с CFSTR_FILEDESCRIPTORW/CFSTR_FILECONTENTS
так, чтобы оболочка Windows вставила это как настоящие файлы.

Запуск (из configurator/tests/clipboard):
    ../../../.venv/Scripts/python.exe spike_filecontents.py --scenario a

Сценарии:
    a  один маленький файл, сверка байтов
    b  из какого потока приходят вызовы, крутится ли цикл событий Qt
    c  медленный большой источник (прогресс/отмена/заморозка окна)
    d  несколько файлов + папка с вложенными
    e  ошибка посреди большого файла (остаётся ли половинчатый файл)
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from spike_com import RPC_E_CHANGED_MODE, kernel32, ole32  # noqa: E402
from spike_dataobject import (  # noqa: E402
    ByteSource,
    SourceError,
    SpikeDataObject,
    T0,
    stamp,
    tid,
)

HERE = Path(__file__).resolve().parent
LOG: list[str] = []


def log(message: str) -> None:
    LOG.append(message)
    print(message, flush=True)


# --------------------------------------------------------------- источники
PATTERN = bytes(range(256))


def pattern_bytes(offset: int, count: int) -> bytes:
    start = offset % 256
    raw = PATTERN * ((count // 256) + 2)
    return raw[start:start + count]


def make_slow_source(name: str, size: int, delay_per_mib: float):
    def produce(offset: int, count: int) -> bytes:
        if delay_per_mib:
            time.sleep(delay_per_mib * count / (1024 * 1024))
        return pattern_bytes(offset, count)

    return ByteSource(name, size, produce)


def make_failing_source(name: str, size: int, fail_at: int, delay_per_mib: float):
    def produce(offset: int, count: int) -> bytes:
        if offset >= fail_at:
            raise SourceError(f"имитация обрыва сети на offset={offset}")
        if delay_per_mib:
            time.sleep(delay_per_mib * count / (1024 * 1024))
        return pattern_bytes(offset, count)

    return ByteSource(name, size, produce)


def scenario_sources(name: str):
    if name == "a":
        payload = b"duo-input spike: file contents over IStream\r\n" * 3
        src = ByteSource("spike-small.txt", len(payload),
                         lambda o, c: payload[o:o + c])
        return [src], {"spike-small.txt": payload}
    if name == "b":
        # 4 МиБ, отдаём по 1 с на МиБ: дыры в пульсе таймера Qt станут видны
        size = 4 * 1024 * 1024
        return ([make_slow_source("spike-thread-probe.bin", size, 1.0)],
                {"spike-thread-probe.bin": pattern_bytes(0, size)})
    if name == "c":
        size = 200 * 1024 * 1024
        return [make_slow_source("spike-big.bin", size, 0.20)], {}
    if name == "d":
        a = b"file A\r\n"
        b = b"nested B\r\n" * 4
        c = b"deep C\r\n" * 2
        return (
            [
                ByteSource("top.txt", len(a), lambda o, n, p=a: p[o:o + n]),
                ByteSource("folder", 0, None, directory=True),
                ByteSource("folder\\nested.txt", len(b),
                           lambda o, n, p=b: p[o:o + n]),
                ByteSource("folder\\deeper", 0, None, directory=True),
                ByteSource("folder\\deeper\\deep.txt", len(c),
                           lambda o, n, p=c: p[o:o + n]),
            ],
            {"top.txt": a, "folder\\nested.txt": b,
             "folder\\deeper\\deep.txt": c},
        )
    if name == "e":
        size = 64 * 1024 * 1024
        return [make_failing_source("spike-broken.bin", size,
                                    fail_at=8 * 1024 * 1024,
                                    delay_per_mib=0.05)], {}
    raise SystemExit(f"неизвестный сценарий {name}")


# ------------------------------------------------------------------- прогон
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="a")
    parser.add_argument("--ftm", action="store_true",
                        help="агрегировать free-threaded marshaler в IStream")
    parser.add_argument("--wait", type=int, default=45)
    parser.add_argument("--cancel-after", type=int, default=0)
    parser.add_argument("--formats", default="both",
                        choices=["both", "minus-one", "index-only"],
                        help="как перечислять CFSTR_FILECONTENTS в EnumFormatEtc")
    parser.add_argument("--driver", default="shell",
                        choices=["shell", "reader", "drop", "explorer"],
                        help="shell = вставка Проводником, reader = свой процесс")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    qt_tid = tid()
    label = QLabel(f"Duo Input spike {args.scenario}")
    label.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    label.resize(420, 90)
    label.show()

    hr = ole32.OleInitialize(None)
    log(f"{stamp()} Qt main thread id = {qt_tid}; process pid = {os.getpid()}")
    log(f"{stamp()} OleInitialize -> 0x{hr & 0xFFFFFFFF:08X} "
        f"({'S_OK — мы инициализировали' if hr == 0 else 'S_FALSE — Qt уже сделал это' if hr == 1 else 'RPC_E_CHANGED_MODE!' if hr == RPC_E_CHANGED_MODE else '?'})")

    sources, expected = scenario_sources(args.scenario)
    trace: list = []
    data_object = SpikeDataObject(sources, log, trace, ftm_streams=args.ftm,
                                  formats_mode=args.formats)
    log(f"{stamp()} перечисление FileContents: режим {args.formats!r}, "
        f"форматов всего {len(data_object._formats)}")

    ole32.OleSetClipboard.argtypes = [ctypes.c_void_p]
    hr = ole32.OleSetClipboard(ctypes.c_void_p(data_object.pointer))
    log(f"{stamp()} OleSetClipboard -> 0x{hr & 0xFFFFFFFF:08X}")
    if hr != 0:
        return 1

    dest = Path(tempfile.mkdtemp(prefix=f"duo-spike-{args.scenario}-"))
    log(f"{stamp()} пустая папка назначения: {dest}")

    # пульс: если цикл событий Qt встанет, между отметками появится дыра
    beats: list[float] = []
    heartbeat = QTimer()
    heartbeat.setInterval(50)
    heartbeat.timeout.connect(lambda: beats.append(time.monotonic() - T0))
    heartbeat.start()

    state: dict = {}

    def launch_paste() -> None:
        if args.driver == "reader":
            cmd = [sys.executable, str(HERE / "spike_read_dataobject.py")]
            log(f"{stamp()} запускаю независимый читатель буфера обмена")
            state["proc"] = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", cwd=str(HERE))
            state["started"] = time.monotonic()
            return
        if args.driver == "drop":
            cmd = [sys.executable, str(HERE / "spike_drop_target.py"), str(dest),
                   "--wait", str(args.wait),
                   "--cancel-after", str(args.cancel_after)]
            log(f"{stamp()} запускаю сброс на IDropTarget папки назначения")
            state["proc"] = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", cwd=str(HERE))
            state["started"] = time.monotonic()
            return
        script = ("spike_explorer_paste.ps1" if args.driver == "explorer"
                  else "spike_paste.ps1")
        cmd = [
            "powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
            "-File", str(HERE / script),
            "-Dest", str(dest), "-WaitSeconds", str(args.wait),
            "-CancelAfter", str(args.cancel_after),
        ]
        log(f"{stamp()} запускаю вставку: {' '.join(cmd[-8:])}")
        state["proc"] = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
        state["started"] = time.monotonic()

    def poll() -> None:
        proc = state.get("proc")
        if proc is None:
            return
        if proc.poll() is None:
            if time.monotonic() - state["started"] > args.wait + 40:
                log(f"{stamp()} ТАЙМАУТ — убиваю помощника")
                proc.kill()
            return
        poll_timer.stop()
        heartbeat.stop()
        out = proc.stdout.read()
        log("--- вывод помощника вставки ---")
        log(out.strip())
        log("--- конец вывода помощника ---")
        finish(dest, expected, trace, beats, qt_tid, args)
        app.quit()

    QTimer.singleShot(700, launch_paste)
    poll_timer = QTimer()
    poll_timer.setInterval(200)
    poll_timer.timeout.connect(poll)
    poll_timer.start()

    app.exec()
    return 0


def finish(dest, expected, trace, beats, qt_tid, args) -> None:
    log("")
    log("================ РАЗБОР ================")
    produced = sorted(p for p in dest.rglob("*"))
    log(f"файлов/папок в назначении: {len(produced)}")
    for path in produced:
        rel = str(path.relative_to(dest))
        if path.is_dir():
            log(f"  DIR  {rel}")
        else:
            raw = path.read_bytes()
            log(f"  FILE {rel}  {len(raw)} байт  sha256={hashlib.sha256(raw).hexdigest()[:16]}")

    if expected:
        log("--- сверка байтов ---")
        for rel, want in expected.items():
            target = dest / rel
            if not target.exists():
                log(f"  {rel}: ОТСУТСТВУЕТ")
                continue
            got = target.read_bytes()
            same = got == want
            log(f"  {rel}: {'СОВПАДАЕТ' if same else 'ОТЛИЧАЕТСЯ'} "
                f"({len(got)} против ожидаемых {len(want)})")

    reads = [t for t in trace if t[0] == "read"]
    log("--- вызовы ---")
    from collections import Counter

    kinds = Counter(t[0] for t in trace)
    log(f"  всего вызовов по видам: {dict(kinds)}")
    threads = Counter(t[1] for t in trace)
    log(f"  потоки, из которых нас звали: {dict(threads)}  (поток Qt = {qt_tid})")
    if reads:
        total = sum(r[5] for r in reads)
        sizes = Counter(r[4] for r in reads)
        log(f"  IStream::Read: {len(reads)} вызовов, отдано {total} байт "
            f"({total / 1048576:.1f} МиБ)")
        log(f"  запрашиваемые размеры кусков: {dict(sizes)}")
        log(f"  первый Read в {reads[0][2]:.3f}s, последний в {reads[-1][2]:.3f}s")
    errors = [t for t in trace if t[0] == "read-error"]
    if errors:
        log(f"  Read вернул ошибку {len(errors)} раз, первый на offset={errors[0][3]}")

    if len(beats) > 1:
        gaps = [(beats[i] - beats[i - 1], beats[i - 1]) for i in range(1, len(beats))]
        worst = sorted(gaps, reverse=True)[:5]
        log("--- цикл событий Qt (таймер 50 мс) ---")
        log(f"  тиков: {len(beats)}, окно {beats[0]:.2f}s..{beats[-1]:.2f}s")
        log(f"  5 худших пауз: " +
            ", ".join(f"{g:.3f}s@{t:.2f}s" for g, t in worst))
        stalled = sum(1 for g, _ in gaps if g > 0.5)
        log(f"  пауз длиннее 0.5 с: {stalled}")
    log("========================================")

    if args.scenario in ("c", "e"):
        log(f"папка назначения НЕ удалена для осмотра: {dest}")
    else:
        shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
