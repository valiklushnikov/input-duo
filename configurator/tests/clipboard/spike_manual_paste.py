"""SPIKE: РУЧНАЯ проверка. Кладёт файлы в буфер обмена и ждёт, пока человек
сам нажмёт Ctrl+V в настоящем Проводнике. Никакой эмуляции нажатий.

Запуск (из configurator/tests/clipboard):
    ../../../.venv/Scripts/python.exe spike_manual_paste.py

Одноразовый код спайка, не production.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from spike_com import ole32  # noqa: E402
from spike_dataobject import ByteSource, SpikeDataObject, T0, stamp, tid  # noqa: E402

SEP = chr(92)  # обратный слэш, без возни с экранированием
CRLF = chr(13) + chr(10)
LOG: list[str] = []

SMALL_NAME = "duo-malenkiy.txt"
BIG_NAME = "duo-bolshoy.bin"
DIR_NAME = "duo-papka"
NESTED_1 = DIR_NAME + SEP + "vnutri-odin.txt"
NESTED_2 = DIR_NAME + SEP + "vnutri-dva.txt"

SMALL_TEXT = (
    "Duo Input: proverka obshchego bufera obmena." + CRLF
    + "Esli vy chitaete etu stroku - soderzhimoe doshlo tselikom." + CRLF
    + "kontrolnaya-stroka-12345" + CRLF
)
NESTED_1_TEXT = "vlozhennyy fayl nomer odin" + CRLF
NESTED_2_TEXT = "vlozhennyy fayl nomer dva" + CRLF

BIG_SIZE = 40 * 1024 * 1024
BIG_DELAY_PER_MIB = 0.5  # ~20 с, чтобы человек успел увидеть прогресс
PATTERN = bytes(range(256))


def log(message: str) -> None:
    LOG.append(message)
    print(message, flush=True)


def pattern_bytes(offset: int, count: int) -> bytes:
    raw = PATTERN * ((count // 256) + 2)
    start = offset % 256
    return raw[start:start + count]


def big_sha256() -> str:
    digest = hashlib.sha256()
    off = 0
    while off < BIG_SIZE:
        n = min(1 << 20, BIG_SIZE - off)
        digest.update(pattern_bytes(off, n))
        off += n
    return digest.hexdigest()


BANNER = """
################################################################
#                                                              #
#            Т Р Е Б У Е Т С Я   Ч Е Л О В Е К                 #
#                                                              #
#   1. Перейдите в окно Проводника, которое сейчас открылось   #
#      (или откройте ЛЮБУЮ свою папку).                        #
#                                                              #
#   2. Нажмите в нём   Ctrl + V                                #
#                                                              #
#   3. Дождитесь окончания вставки. Большой файл идёт около    #
#      20 секунд - на нём должен быть виден прогресс.          #
#                                                              #
#   ЭТО ОКНО ЗАКРЫВАТЬ НЕЛЬЗЯ, пока идёт вставка:              #
#   файлы отдаёт именно оно. Закроете - вставка оборвётся.     #
#                                                              #
################################################################
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formats", default="both",
                        choices=["both", "minus-one", "index-only"],
                        help="как перечислять CFSTR_FILECONTENTS")
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()

    app = QApplication(sys.argv)
    qt_tid = tid()
    dest = Path(tempfile.mkdtemp(prefix="duo-ruchnaya-proverka-"))

    small = SMALL_TEXT.encode()
    nested1 = NESTED_1_TEXT.encode()
    nested2 = NESTED_2_TEXT.encode()

    def big_producer(offset: int, count: int) -> bytes:
        time.sleep(BIG_DELAY_PER_MIB * count / (1024 * 1024))
        return pattern_bytes(offset, count)

    sources = [
        ByteSource(SMALL_NAME, len(small), lambda o, c: small[o:o + c]),
        ByteSource(BIG_NAME, BIG_SIZE, big_producer),
        ByteSource(DIR_NAME, 0, None, directory=True),
        ByteSource(NESTED_1, len(nested1), lambda o, c: nested1[o:o + c]),
        ByteSource(NESTED_2, len(nested2), lambda o, c: nested2[o:o + c]),
    ]

    trace: list = []
    ole32.OleInitialize(None)
    data_object = SpikeDataObject(sources, log, trace, formats_mode=args.formats)
    ole32.OleSetClipboard.argtypes = [ctypes.c_void_p]
    hr = ole32.OleSetClipboard(ctypes.c_void_p(data_object.pointer))

    log("")
    log("=" * 64)
    log("  Duo Input - ручная проверка передачи файлов через буфер обмена")
    log("=" * 64)
    log(f"  поток Qt = {qt_tid}, режим перечисления = {args.formats!r}")
    log(f"  OleSetClipboard -> 0x{hr & 0xFFFFFFFF:08X}"
        f"{'  (объект в буфере)' if hr == 0 else '  ОШИБКА!'}")
    if hr != 0:
        return 1
    log("")
    log("  В буфер обмена положено:")
    log(f"    1. {SMALL_NAME}   ({len(small)} байт, известный текст)")
    log(f"    2. {BIG_NAME}   ({BIG_SIZE // (1024 * 1024)} МиБ, отдаётся медленно)")
    log(f"    3. {DIR_NAME}{SEP}   - папка, внутри два файла:")
    log(f"         {NESTED_1}")
    log(f"         {NESTED_2}")
    log("")
    log(f"  Подготовленная пустая папка: {dest}")

    subprocess.Popen(["explorer.exe", str(dest)])
    log(BANNER)

    state = {"done_at": None, "last_line": "", "announced": False}
    wanted = sum(s.size for s in sources if not s.directory)
    files_count = sum(1 for s in sources if not s.directory)

    def totals() -> int:
        return sum(e[5] for e in trace if e[0] == "read")

    def content_requests():
        return [e for e in trace if e[0] == "getdata" and e[3] == "FileContents"]

    def tick() -> None:
        elapsed = time.monotonic() - T0
        left = args.seconds - int(elapsed)
        got = totals()
        requests = content_requests()
        if requests and not state["announced"]:
            state["announced"] = True
            log("")
            log(f"{stamp()} ОБОЛОЧКА НАЧАЛА ЧИТАТЬ СОДЕРЖИМОЕ - Ctrl+V принят")
        percent = (100.0 * got / wanted) if wanted else 0.0
        line = (f"  ждём Ctrl+V... осталось {max(left, 0):3d} с | "
                f"запросов содержимого: {len(requests)} | "
                f"отдано {got / 1048576:6.1f} из {wanted / 1048576:.1f} МиБ "
                f"({percent:5.1f}%)")
        if line != state["last_line"]:
            print(line, flush=True)
            state["last_line"] = line

        released = sum(1 for e in trace if e[0] == "stream-released")
        if got >= wanted > 0 and released >= files_count and not state["done_at"]:
            state["done_at"] = elapsed
            log("")
            log(f"{stamp()} всё содержимое отдано, оболочка отпустила потоки")
            QTimer.singleShot(2500, app.quit)
        if elapsed >= args.seconds:
            app.quit()

    timer = QTimer()
    timer.setInterval(1000)
    timer.timeout.connect(tick)
    timer.start()

    label = QLabel("Duo Input: нажмите Ctrl+V в Проводнике.\n"
                   "Это окно закрывать нельзя.")
    label.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    label.setMargin(18)
    label.show()

    app.exec()
    verdict(dest, trace, qt_tid, wanted)
    return 0


def verdict(dest: Path, trace: list, qt_tid: int, wanted: int) -> None:
    log("")
    log("=" * 64)
    log("  И Т О Г")
    log("=" * 64)

    descriptors = [e for e in trace
                   if e[0] == "getdata" and e[3] == "FileGroupDescriptorW"]
    contents = [e for e in trace if e[0] == "getdata" and e[3] == "FileContents"]
    reads = [e for e in trace if e[0] == "read"]
    served = sum(e[5] for e in reads)

    log(f"  за описанием файлов оболочка обращалась: {len(descriptors)} раз")
    log(f"  за содержимым обращалась: {len(contents)} раз"
        + (f" (номера файлов: {sorted({e[4] for e in contents})})"
           if contents else ""))
    log(f"  вызовов IStream::Read: {len(reads)}")
    log(f"  отдано байт: {served} из {wanted} "
        f"({100.0 * served / wanted if wanted else 0.0:.1f}%)")
    log(f"  потоки, из которых нас звали: {sorted({e[1] for e in trace})} "
        f"(поток Qt = {qt_tid})")

    log("")
    if not contents:
        log("  >>> ОБОЛОЧКА ЗА СОДЕРЖИМЫМ НЕ ОБРАЩАЛАСЬ.")
        log("      Либо Ctrl+V не нажали, либо Проводник отказался брать наш")
        log("      объект. Если вы точно нажимали Ctrl+V - это и есть ОТВЕТ:")
        log("      настоящий Проводник этот путь не принимает.")
    else:
        log("  >>> Оболочка запросила содержимое и прочитала его.")

    produced = sorted(dest.rglob("*"))
    log("")
    if produced:
        expected = {SMALL_NAME: SMALL_TEXT.encode(),
                    NESTED_1: NESTED_1_TEXT.encode(),
                    NESTED_2: NESTED_2_TEXT.encode()}
        big_expected = big_sha256()
        all_ok = True
        log(f"  В подготовленной папке {dest} появилось:")
        for path in produced:
            rel = str(path.relative_to(dest))
            if path.is_dir():
                log(f"    ПАПКА  {rel}")
                continue
            size = path.stat().st_size
            if rel in expected:
                same = path.read_bytes() == expected[rel]
                all_ok = all_ok and same
                log(f"    ФАЙЛ   {rel}  {size} байт  "
                    f"{'СОДЕРЖИМОЕ СОВПАДАЕТ' if same else 'СОДЕРЖИМОЕ ОТЛИЧАЕТСЯ'}")
            elif rel == BIG_NAME:
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                same = digest.hexdigest() == big_expected
                all_ok = all_ok and same
                log(f"    ФАЙЛ   {rel}  {size} байт  sha256 "
                    f"{'СОВПАДАЕТ' if same else 'ОТЛИЧАЕТСЯ'}")
            else:
                log(f"    ФАЙЛ   {rel}  {size} байт  (неожиданный)")
        missing = [n for n in (SMALL_NAME, BIG_NAME, NESTED_1, NESTED_2)
                   if not (dest / n).exists()]
        if missing:
            all_ok = False
            log(f"    НЕ ХВАТАЕТ: {missing}")
        log("")
        log("  >>> ПРОВЕРКА ПРОЙДЕНА ПОЛНОСТЬЮ" if all_ok
            else "  >>> ЕСТЬ РАСХОЖДЕНИЯ, смотрите строки выше")
    else:
        log(f"  Подготовленная папка {dest} пуста.")
        log("  Если вставляли в другую папку - осмотрите её сами.")

    log("")
    log("  ВОПРОСЫ К ЧЕЛОВЕКУ (ответьте, пожалуйста, словами):")
    log("    1. Появились ли все четыре файла и папка?")
    log(f"    2. Открывается ли {SMALL_NAME} и есть ли в нём строка")
    log("       'kontrolnaya-stroka-12345'?")
    log("    3. Показывал ли Проводник окно прогресса на большом файле?")
    log("    4. Была ли в этом окне кнопка отмены?")
    log("")
    log(f"  Папку {dest} после осмотра можно удалить.")
    log("=" * 64)


if __name__ == "__main__":
    raise SystemExit(main())
