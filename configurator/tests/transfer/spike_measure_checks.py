# configurator/tests/transfer/spike_measure_checks.py
"""Throwaway: две проверки под замер задачи 3.2, которые сам харнесс не делает.

Запуск:
    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_measure_checks.py --plain-tcp
    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_measure_checks.py --verify-chunk-size

``--plain-tcp`` - потолок голого TCP на 127.0.0.1, БЕЗ TLS, без кадрирования,
без Qt. Нужен, чтобы отделить два разных диагноза для числа «транспорт без
моста = 91.9 МиБ/с»: это потолок НАШЕГО софта, или это потолок
локального транспорта, в который мы уже уперлись? Для будущей задачи про окно
разница решающая - гнаться за 92 МиБ/с или признать 92 уже почти предельными.
Двух машин для этого не нужно: шаг 2 брифа говорит «any plain TCP throughput
check», и на одной машине он отвечает на этот - более узкий - вопрос, хотя и
не даёт гигабитного знаменателя для порога §8.

Оба конца в ОДНОМ процессе, но на ДВУХ потоках - и это НЕ та же топология,
что у прохода с обойдённым мостом. Первая версия этого файла утверждала
обратное («та же топология, значит разница - это TLS и кадрирование»), и это
неверно: ``_measure_bypassed_transport`` шлёт в цикле на главном потоке Qt и
принимает там же через ``message_received``, так что шифрование, расшифровка
и ``FrameAssembler`` сериализуются на ОДНОМ потоке. Здешние ``sendall`` и
``recv_into`` идут на двух и отпускают GIL внутри системных вызовов.

Поэтому сравнение НЕ изолирует TLS с кадрированием. В разрыв вкладываются,
неразделимо этим замером: сериализация на одном потоке против двух,
диспетчеризация событий Qt, построение объекта ``Message`` на каждый кадр,
и собственно TLS с кадрированием. Что сравнение всё же держит - вывод на
порядок-два: потолок нашего софта против потолка локального сокета. Чего не
держит - атрибуцию разрыва какой-то одной из этих причин.

Главное ограничение сверх того: это потолок петли на локальной машине, а не
потолок настоящей сети.

``--verify-chunk-size`` - доказательство, что ``--read-chunk-bytes`` доходит
до провода, а не печатается в таблице по собственному аргументу. Строка
таблицы «размер чанка» - это печать значения CLI; она не наблюдение. Здесь
перехватывается ``FileTransferService.request_read`` и записывается ФАКТИЧЕСКАЯ
длина каждого FILE_READ. Запись задачи 3.2 стоит на том, что прибор обязан
быть показан читающим систему, - этот файл существует, чтобы то же правило
применялось и к самой этой записи.
"""

from __future__ import annotations

import argparse
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

MIB = 1024 * 1024

#: Те же два размера, при которых снят основной замер: собственная константа
#: харнесса и настоящий cb Проводника (спайк 1).
HARNESS_CHUNK = 65536
EXPLORER_CHUNK = 262144

#: Столько же байт, сколько прогоняет основной замер.
TOTAL_BYTES = 2048 * MIB


def plain_tcp_mib_s(total_bytes: int, chunk_bytes: int) -> tuple[float, float]:
    """Пропускная способность голого TCP на 127.0.0.1 и время прогона.

    Отправитель и получатель - два потока одного процесса. Проход с
    обойдённым мостом отправляет и наблюдает приём на одном потоке Qt;
    сравнение не отделяет эту разницу от TLS, кадрирования, диспетчеризации
    Qt и построения Message на каждый кадр.
    Часы идут от первого send до того момента, когда
    получатель досчитал последний байт, и НЕ включают ни установление
    соединения, ни выделение буфера.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    received = {"bytes": 0}
    receiver_done = threading.Event()
    error: list[BaseException] = []

    def _receive() -> None:
        try:
            conn, _ = listener.accept()
            with conn:
                # recv_into в переиспользуемый буфер: иначе аллокация нового
                # bytes на каждый чанк попала бы в измеряемый интервал и
                # мерился бы аллокатор Python, а не сокет.
                buffer = bytearray(chunk_bytes)
                view = memoryview(buffer)
                while received["bytes"] < total_bytes:
                    got = conn.recv_into(view, chunk_bytes)
                    if got == 0:
                        break
                    received["bytes"] += got
        except BaseException as exc:  # noqa: BLE001 - поток отдаёт наружу
            error.append(exc)
        finally:
            receiver_done.set()

    thread = threading.Thread(target=_receive, name="plain-tcp-receiver", daemon=True)
    thread.start()

    sender = socket.create_connection(("127.0.0.1", port))
    payload = bytes((index * 131 + 7) & 0xFF for index in range(chunk_bytes))
    remaining = total_bytes

    started = time.perf_counter()
    try:
        with sender:
            while remaining > 0:
                take = min(chunk_bytes, remaining)
                sender.sendall(payload[:take] if take != chunk_bytes else payload)
                remaining -= take
            if not receiver_done.wait(300.0):
                raise RuntimeError("получатель не досчитал за 300 с - прогон завис")
    finally:
        ended = time.perf_counter()
        thread.join(5.0)
        listener.close()

    if error:
        raise RuntimeError(f"поток-получатель упал: {error[0]!r}") from error[0]
    # Молчание прибора обязано быть отличимо от честного нуля: если получатель
    # недосчитал, это НЕ медленный сокет, и число возвращать нельзя.
    if received["bytes"] < total_bytes:
        raise RuntimeError(
            f"получено {received['bytes']} из {total_bytes} Б - сокет закрылся "
            "раньше времени, и это не измерение пропускной способности"
        )

    elapsed = ended - started
    if elapsed <= 0:
        raise RuntimeError("нулевой интервал - часы не идут")
    return total_bytes / MIB / elapsed, elapsed


#: Сколько раз повторять каждый замер голого TCP.
#:
#: НЕ три. Первая версия этого файла брала медиану из трёх и объясняла это
#: тем, что «один прогон на этой машине гуляет на единицы процентов». Это
#: оказалось неверно: при 262144 наблюдался разброс в 2.2 раза (2025 против
#: 4449 МиБ/с), причём первые два-три прогона систематически ниже остальных -
#: разогрев. Медиана из трёх на холодном старте поэтому садится около
#: МИНИМУМА распределения, а не в его середину, и выглядит точнее, чем есть.
#: Отсюда и правило ниже: печатать min/медиану/max и сам разброс, а не одно
#: число, которое читатель примет за определённую величину.
PLAIN_TCP_REPEATS = 15


def _run_plain_tcp() -> int:
    print("| чанк | голый TCP на 127.0.0.1: min / медиана / max | разброс |")
    print("|---|---|---|")
    results: dict[int, tuple[float, float, float]] = {}
    for chunk in (HARNESS_CHUNK, EXPLORER_CHUNK):
        rates = sorted(
            plain_tcp_mib_s(TOTAL_BYTES, chunk)[0] for _ in range(PLAIN_TCP_REPEATS)
        )
        low, mid, high = rates[0], statistics.median(rates), rates[-1]
        results[chunk] = (low, mid, high)
        print(
            f"| {chunk} Б | {low:.0f} / {mid:.0f} / {high:.0f} МиБ/с "
            f"| max/min = {high / low:.2f}x (n={PLAIN_TCP_REPEATS}) |"
        )
    print()
    print("Разброс здесь того же порядка, что и разница между чанками, поэтому")
    print("сравнивать чанки по этим числам НЕЛЬЗЯ: порядок между ними от запуска")
    print("к запуску меняется, и это шум, а не эффект размера кадра.")

    print()
    print("Для сравнения, из основного замера (тот же 127.0.0.1, тот же процесс,")
    print("но через PeerLink - TLS плюс кадрирование плюс Qt):")
    print("  транспорт без моста при 65536  = 84.3 МиБ/с")
    print("  транспорт без моста при 262144 = 91.9 МиБ/с")
    print()
    for chunk, ours in ((HARNESS_CHUNK, 84.3), (EXPLORER_CHUNK, 91.9)):
        low, _, high = results[chunk]
        # Доля печатается диапазоном, а не одним процентом: делитель гуляет
        # вдвое, значит и доля не определена точнее.
        print(
            f"  при {chunk} Б: наш транспорт {ours} МиБ/с - это "
            f"{ours / high * 100:.1f}-{ours / low * 100:.1f}% голого TCP, "
            f"запас примерно {low / ours:.0f}-{high / ours:.0f}x"
        )
    print()
    print("Вывод, который эти числа держат: наш потолок транспорта ниже потолка")
    print("локального сокета на порядок-два, то есть 91.9 МиБ/с - это предел")
    print("НАШЕГО софта, а не транспорта. Чего они НЕ держат: доли с точностью")
    print("до процента и сравнения чанков между собой.")
    return 0


def _run_verify_chunk_size() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from duo_input.transfer.service import FileTransferService

    import spike_measure_bridge as bridge

    seen: dict[int, int] = {}
    original = FileTransferService.request_read

    def _spy(self, pipe, offset, length):  # type: ignore[no-untyped-def]
        seen[length] = seen.get(length, 0) + 1
        return original(self, pipe, offset, length)

    FileTransferService.request_read = _spy  # type: ignore[method-assign]
    try:
        for chunk in (HARNESS_CHUNK, EXPLORER_CHUNK):
            seen.clear()
            size = 4 * MIB
            measurement = bridge.measure(size, read_chunk_bytes=chunk)
            expected = -(-size // chunk)
            print(f"--- запрошен cb={chunk}")
            print(f"    фактические длины FILE_READ на проводе: {seen}")
            print(f"    ожидалось {expected} чтений по {chunk} Б")
            if seen != {chunk: expected}:
                raise RuntimeError(
                    f"на проводе оказались другие длины: {seen} - флаг не доходит "
                    "до request_read, и строка таблицы про размер чанка врёт"
                )
            print(f"    строка таблицы: {measurement.read_chunk_bytes} Б")
            print(
                f"    RTT медиана {measurement.rtt_median_ms:.2f} мс, "
                f"пропускная {measurement.throughput_mib_s:.1f} МиБ/с, "
                f"high_water {measurement.pipe_high_water}"
            )
    finally:
        FileTransferService.request_read = original  # type: ignore[method-assign]
    print()
    print("Обе проверки сошлись: длины на проводе равны запрошенному cb, и")
    print("ни одного чтения другой длины не наблюдалось.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--plain-tcp",
        action="store_true",
        help="потолок голого TCP на 127.0.0.1, без TLS и кадрирования",
    )
    group.add_argument(
        "--verify-chunk-size",
        action="store_true",
        help="доказать, что --read-chunk-bytes доходит до провода",
    )
    args = parser.parse_args()
    if args.plain_tcp:
        return _run_plain_tcp()
    return _run_verify_chunk_size()


if __name__ == "__main__":
    raise SystemExit(main())
