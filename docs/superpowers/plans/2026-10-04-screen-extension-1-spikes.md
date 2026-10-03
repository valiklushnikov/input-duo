# Расширение экрана, план 1 из 3 — спайки Windows и macOS

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** до написания кода агента получить ответы на вопросы о платформах, без которых агент нельзя спроектировать надёжно. Результат — записка с ответами. Код спайков выбрасывается.

**Architecture:** два набора одноразовых скриптов: ctypes на Windows и pyobjc на macOS. Скрипты живут в каталоге-черновике сессии и не коммитятся. В репозиторий попадает только записка `docs/superpowers/records/2026-10-04-screen-extension-spikes.md`.

**Tech Stack:** Python 3.12 из `.venv`, ctypes (user32, shcore, kernel32), pyobjc (AppKit, Quartz) на Mac.

**Spec:** `docs/superpowers/specs/2026-10-04-screen-extension-design.md`, раздел 5 «Спайки».

**Порядок планов:** этот план — первый. План 2 (`…-2-firmware.md`) от него не зависит и может идти параллельно. План 3 (`…-3-agent.md`) начинается только после этой записки: от её ответов зависят `probe_windows.py` и `probe_macos.py`.

## Global Constraints

- Скрипты спайков **не коммитятся**. Они создаются в каталоге-черновике (`$env:TEMP\duo-spikes\` на Windows, `/tmp/duo-spikes/` на Mac) и после записки удаляются.
- Python — только `.venv/Scripts/python.exe` (Windows) / `.venv/bin/python` (Mac). Голый `python` может не иметь зависимостей.
- Каждый ответ в записке — **с показаниями**: вывод скрипта, а не впечатление. Отрицательный результат («не работает») сопровождается выводом, доказывающим, что проверка вообще могла увидеть положительный (урок «отсутствие — доказательство, только если наличие печатается»).
- Шаги, требующие человека у компьютера (Win+L, экран блокировки, игра), агент готовит и ждёт результата.

## Review Focus

- **Терминал с разрешениями.** Если Terminal на Mac уже имеет Accessibility или Input Monitoring, warp «сработает» благодаря чужому разрешению. Записать состояние разрешений Terminal в записку до проверки.
- **Скрипт, а не собранный exe.** PMv2 у Python-процесса ничего не говорит о Nuitka-exe. Проверять нужно запущенный `dist`-образ.
- **Подавление после warp не видно без движения.** Замер имеет смысл, только если мышь в это время непрерывно двигают: иначе «0 событий за 250 мс» — норма, а не подавление.
- **Смешанные мониторы.** Проверка краёв на одном мониторе ничего не говорит о стыке Retina + 1080p.
- **Экран блокировки** проверяется по журналу, записанному во время блокировки, а не по воспоминанию.

---

### Task 1: Спайк Windows — DPI-осознанность собранного exe и защищённый рабочий стол

**Files:**
- Create (черновик, не коммитить): `%TEMP%\duo-spikes\win_dpi_of_process.py`, `%TEMP%\duo-spikes\win_secure_desktop.py`
- Create: `docs/superpowers/records/2026-10-04-screen-extension-spikes.md` (раздел «Windows»)

**Interfaces:**
- Produces: ответы W1–W3 в записке. План 3 читает их в задаче `probe_windows.py`.

- [ ] **Step 1: Скрипт DPI-осознанности чужого процесса.** Создать `win_dpi_of_process.py`:

```python
"""Какой DPI-режим у запущенного процесса Duo Input. Аргумент — PID."""
import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
pid = int(sys.argv[1])
handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
if not handle:
    sys.exit(f"OpenProcess failed: {ctypes.get_last_error()}")

user32.GetDpiAwarenessContextForProcess.restype = ctypes.c_void_p
user32.GetDpiAwarenessContextForProcess.argtypes = [wintypes.HANDLE]
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

context = user32.GetDpiAwarenessContextForProcess(handle)
awareness = user32.GetAwarenessFromDpiAwarenessContext(context)
PMV2 = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
is_v2 = bool(user32.AreDpiAwarenessContextsEqual(context, PMV2))
print(f"pid={pid} awareness={awareness} (0=unaware 1=system 2=per-monitor) pmv2={is_v2}")
```

- [ ] **Step 2: Проверить скрипт на заведомо известном процессе.** Запустить `.venv/Scripts/python.exe -c "import time; time.sleep(60)"` в отдельном окне, узнать его PID (`Get-Process python | Select Id,StartTime`) и выполнить `.venv/Scripts/python.exe %TEMP%\duo-spikes\win_dpi_of_process.py <pid>`.
  Expected: `awareness=0` или `1` — голый Python не PMv2. Так проверяется, что скрипт способен увидеть «не PMv2».

- [ ] **Step 3: Проверить собранное приложение.** Запустить установленный или собранный exe (`configurator/dist/…/DuoInput.exe` — путь взять из `tools/build_release.ps1`). Узнать его PID через `Get-Process DuoInput` и прогнать скрипт.
  Expected (что записать): `awareness=2 pmv2=True` → ответ W1 «да». Любое другое значение → ответ W1 «нет», в записке указать фактическое значение. Тогда плану 3 нужна задача на манифест или `SetProcessDpiAwarenessContext` до создания `QApplication`.

- [ ] **Step 4: Скрипт защищённого рабочего стола.** Создать `win_secure_desktop.py`:

```python
"""Журнал GetCursorPos/SetCursorPos раз в 250 мс в течение 40 с."""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
point = wintypes.POINT()
start = time.monotonic()
while time.monotonic() - start < 40:
    ok_get = user32.GetCursorPos(ctypes.byref(point))
    err_get = ctypes.get_last_error()
    ok_set = user32.SetCursorPos(point.x + 1, point.y)
    err_set = ctypes.get_last_error()
    print(f"{time.monotonic() - start:6.2f} get={ok_get} err={err_get} "
          f"pos=({point.x},{point.y}) set={ok_set} err={err_set}", flush=True)
    time.sleep(0.25)
```

- [ ] **Step 5: Прогнать под блокировкой (человек).** Запустить `.venv/Scripts/python.exe %TEMP%\duo-spikes\win_secure_desktop.py > %TEMP%\duo-spikes\secure.log`. Через 5 с нажать Win+L, подождать 15 с, разблокировать. Также за время прогона открыть окно UAC (например, `Start-Process cmd -Verb RunAs`) и отменить его.
  Expected (что записать): строки до блокировки показывают `get=1` и растущий X; это доказывает, что скрипт видит успех. Строки во время блокировки и UAC — фактические `get`/`err`/`set`. Это ответы W2 (что видит `GetCursorPos`) и W3 (что делает `SetCursorPos`).

- [ ] **Step 6: Записать раздел «Windows».** В `docs/superpowers/records/2026-10-04-screen-extension-spikes.md`:

```markdown
# Расширение экрана — результаты спайков

## Windows (дата, версия Windows, путь к проверенному exe)

| № | Вопрос | Ответ | Показания |
| --- | --- | --- | --- |
| W1 | Собранный exe — Per-Monitor-V2? | да/нет | вывод win_dpi_of_process.py для python и для exe |
| W2 | GetCursorPos на экране блокировки и в UAC | … | строки secure.log |
| W3 | SetCursorPos там же | … | строки secure.log |

**Следствия для плана 3:** …
```

- [ ] **Step 7: Коммит.**

```bash
git add docs/superpowers/records/2026-10-04-screen-extension-spikes.md
git commit -m "docs(screen-extension): Windows spike results"
```

### Task 2: Спайк macOS — warp, подавление, координаты, скрытый курсор, нагрузка, блокировка

**Files:**
- Create (черновик, не коммитить): `/tmp/duo-spikes/mac_spike.py`
- Modify: `docs/superpowers/records/2026-10-04-screen-extension-spikes.md` (раздел «macOS»)

**Interfaces:**
- Produces: ответы M1–M6. План 3 читает их в задаче `probe_macos.py`.

- [ ] **Step 1: Записать исходное состояние разрешений.** В «Системные настройки → Конфиденциальность и безопасность» посмотреть, есть ли Terminal (или iTerm) в списках «Универсальный доступ» и «Мониторинг ввода». Если есть — выключить переключатели на время спайка. Записать, что было и что сделано.

- [ ] **Step 2: Создать скрипт.** `/tmp/duo-spikes/mac_spike.py`:

```python
"""Спайк macOS для «Расширения экрана». Подкоманды: screens, warp, suppress, hidden, cpu, lock."""
import sys
import time

import Quartz
from AppKit import NSEvent, NSScreen


def cg_point_from_cocoa(point):
    main_height = NSScreen.screens()[0].frame().size.height
    return (point.x, main_height - point.y)


def cursor_cg():
    return cg_point_from_cocoa(NSEvent.mouseLocation())


def screens():
    for index, screen in enumerate(NSScreen.screens()):
        frame = screen.frame()
        print(f"screen {index}: origin=({frame.origin.x},{frame.origin.y}) "
              f"size=({frame.size.width}x{frame.size.height}) scale={screen.backingScaleFactor()}")
    count = Quartz.CGGetActiveDisplayList(16, None, None)[2]
    for display in Quartz.CGGetActiveDisplayList(16, None, None)[1][:count]:
        bounds = Quartz.CGDisplayBounds(display)
        print(f"display {display}: CG bounds origin=({bounds.origin.x},{bounds.origin.y}) "
              f"size=({bounds.size.width}x{bounds.size.height})")
    print("Ведите курсор к каждому краю каждого экрана; 20 с журнала позиции (CG):")
    end = time.monotonic() + 20
    while time.monotonic() < end:
        x, y = cursor_cg()
        print(f"  ({x:.1f},{y:.1f})", flush=True)
        time.sleep(0.1)


def warp():
    before = cursor_cg()
    error = Quartz.CGWarpMouseCursorPosition((200.0, 200.0))
    time.sleep(0.05)
    after = cursor_cg()
    print(f"before={before} warp_error={error} after={after}")


def suppress(zero_interval):
    if zero_interval:
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
        Quartz.CGEventSourceSetLocalEventsSuppressionInterval(source, 0.0)
    print("Двигайте мышь непрерывно по кругу; через 3 с будет warp.")
    time.sleep(3)
    Quartz.CGWarpMouseCursorPosition((300.0, 300.0))
    Quartz.CGAssociateMouseAndMouseCursorPosition(True)
    warped_at = time.monotonic()
    last = cursor_cg()
    first_move = None
    while time.monotonic() - warped_at < 1.0:
        now = cursor_cg()
        if now != last and first_move is None:
            first_move = time.monotonic() - warped_at
        last = now
        time.sleep(0.002)
    print(f"zero_interval={zero_interval} first movement after warp: "
          f"{'none in 1 s' if first_move is None else f'{first_move * 1000:.0f} ms'}")


def hidden():
    print("20 с: CGCursorIsVisible раз в 0,5 с. Откройте игру или полноэкранное видео.")
    end = time.monotonic() + 20
    while time.monotonic() < end:
        print(f"  visible={Quartz.CGCursorIsVisible()}", flush=True)
        time.sleep(0.5)


def cpu():
    start_cpu = time.process_time()
    start = time.monotonic()
    while time.monotonic() - start < 10:
        NSEvent.mouseLocation()
        NSScreen.screens()
        time.sleep(0.008)
    used = time.process_time() - start_cpu
    print(f"cpu over 10 s at 125 Hz: {used:.3f} s = {used / 10 * 100:.2f}% of one core")


def lock():
    print("40 с журнала mouseLocation + warp раз в 0,5 с. Заблокируйте экран (Ctrl+Cmd+Q).")
    end = time.monotonic() + 40
    while time.monotonic() < end:
        before = cursor_cg()
        error = Quartz.CGWarpMouseCursorPosition((before[0] + 1, before[1]))
        print(f"  {time.monotonic():.2f} pos={before} warp_error={error}", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "suppress":
        suppress(zero_interval=sys.argv[2] == "zero")
    else:
        globals()[command]()
```

- [ ] **Step 3: M1 — warp без разрешений.** `.venv/bin/python /tmp/duo-spikes/mac_spike.py warp`.
  Expected (записать): `warp_error=0` и `after ≈ (200, 200)` → M1 «работает без разрешений». Если macOS показала запрос разрешения, записать какой именно.

- [ ] **Step 4: M2 — подавление после warp.** Выполнить дважды, оба раза непрерывно двигая мышь (подключённую через U1/U2 или напрямую): `… suppress default`, затем `… suppress zero`.
  Expected (записать): при `default` первое движение придёт примерно через 250 мс; это доказывает, что замер видит подавление. При `zero` оно должно прийти через ≤ 20 мс. Если и при `zero` задержка ~250 мс, M2 «не снимается», и это блокер для плана 3.

- [ ] **Step 5: M3 — координаты при смешанных мониторах.** Подключить внешний 1080p к Retina-Mac и расположить его в «Мониторах» сначала справа, затем выше. В каждой конфигурации выполнить `… screens` и провести курсор вдоль всех внешних краёв.
  Expected (записать): крайние значения X/Y из журнала совпадают с границами `CG bounds` (максимальное значение = origin + size − 1 или − 0,5 при дробных точках). Записать фактические крайние значения и расхождение, если оно есть.

- [ ] **Step 6: M4 — скрытый курсор.** `… hidden`, во время прогона открыть полноэкранное видео (курсор прячется через 2–3 с) или игру.
  Expected (записать): `visible=1` до и `visible=0`, пока курсор скрыт. Если значение не меняется, M4 «детект не работает», и в MVP подавление по скрытому курсору на Mac не делаем.

- [ ] **Step 7: M5 — нагрузка.** `… cpu`.
  Expected (записать): процент одного ядра. Цель ≤ 1%.

- [ ] **Step 8: M6 — экран блокировки.** `… lock > /tmp/duo-spikes/lock.log`, через 5 с заблокировать, через 15 с разблокировать.
  Expected (записать): строки до блокировки с меняющимся `pos` доказывают, что скрипт видит warp; строки во время блокировки показывают фактическое поведение.

- [ ] **Step 9: Записать раздел «macOS» и следствия.** В записку:

```markdown
## macOS (дата, версия macOS, модель Mac, мониторы)

Разрешения Terminal до спайка: … ; сделано: …

| № | Вопрос | Ответ | Показания |
| --- | --- | --- | --- |
| M1 | CGWarpMouseCursorPosition без разрешений | … | … |
| M2 | Подавление после warp снимается | … | default: … мс, zero: … мс |
| M3 | Точки NSScreen/mouseLocation на краях при смешанных мониторах | … | … |
| M4 | Детект скрытого курсора | … | … |
| M5 | CPU опроса 125 Гц | …% | … |
| M6 | Экран блокировки | … | … |

**Следствия для плана 3:** что делает probe_macos.py по каждому пункту; если M1 или M2 отрицательны — остановка и отдельное решение (абсолютный HID на U2), план 3 не начинается.
```

- [ ] **Step 10: Коммит.**

```bash
git add docs/superpowers/records/2026-10-04-screen-extension-spikes.md
git commit -m "docs(screen-extension): macOS spike results"
```

- [ ] **Step 11: Удалить черновики.** Удалить `%TEMP%\duo-spikes\` и `/tmp/duo-spikes/`.
