<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="ru_RU">
<context>
    <name>BindingTable</name>
    <message>
        <location filename="../../ui/models/binding_table.py" line="192"/>
        <source>Switch the keyboard between PC1 and PC2</source>
        <extracomment>Modifier key of the editor, in the order the labels are joined. Buttons every HID mouse reports; 4 and 5 have to be seen before they exist. Highest button number the binary format can store, mirrored from validation. HID usage to the legend the operator sees. Missing usages read as a number. Usages this program can name but must not offer. A binding on a key the operator has no way to press is one that silently never fires, and the chooser is where that mistake would be made. They stay in KEY_NAMES so a configuration read back from the device is still legible. Usages the key chooser offers, in HID order. What each action does, said the way the operator would say it. The protocol name stays available in a tooltip: a screenshot has to be readable against the diagnostics and the documentation, which both speak in identifiers.</extracomment>
        <translation>Переключить клавиатуру между ПК1 и ПК2</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="193"/>
        <source>Each press sends the keyboard to the other computer.</source>
        <translation>Каждое нажатие отправляет клавиатуру на другой компьютер.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="199"/>
        <source>Switch the mouse between PC1 and PC2</source>
        <translation>Переключить мышь между ПК1 и ПК2</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="200"/>
        <source>Each press sends the mouse to the other computer.</source>
        <translation>Каждое нажатие отправляет мышь на другой компьютер.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="205"/>
        <source>Send the keyboard to one computer</source>
        <translation>Направить клавиатуру на один компьютер</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="206"/>
        <location filename="../../ui/models/binding_table.py" line="213"/>
        <source>Always the same computer, whichever one was being used before.</source>
        <translation>Всегда один и тот же компьютер, каким бы ни был предыдущий.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="212"/>
        <source>Send the mouse to one computer</source>
        <translation>Направить мышь на один компьютер</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="219"/>
        <source>Switch to another profile</source>
        <translation>Перейти на другой профиль</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="220"/>
        <source>Loads a different set of bindings on the device.</source>
        <translation>Загружает на устройство другой набор назначений.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="225"/>
        <source>Run a macro</source>
        <translation>Запустить макрос</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="226"/>
        <source>Plays a recorded sequence of keys and pauses.</source>
        <translation>Проигрывает записанную последовательность клавиш и пауз.</translation>
    </message>
</context>
<context>
    <name>BindingTableModel</name>
    <message>
        <location filename="../../ui/models/binding_table.py" line="491"/>
        <source>Trigger</source>
        <extracomment>``(vendor_id, product_id)`` of every device U1 reported on its own bus. ``None`` while no report has said - which is not the same as an empty set, and is why an unread device greys nothing out. The one of them that enumerated as a mouse, when U1 named one.</extracomment>
        <translation>Триггер</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="491"/>
        <source>Mode</source>
        <translation>Режим</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="491"/>
        <source>Action</source>
        <translation>Действие</translation>
    </message>
</context>
<context>
    <name>BindingsPage</name>
    <message>
        <location filename="../../ui/bindings.py" line="240"/>
        <source>Bindings</source>
        <extracomment>A mouse button the device just reported, so the shell can remember it.</extracomment>
        <translation>Назначения</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="241"/>
        <source>Choose what a key or mouse button does in this profile.</source>
        <translation>Настройте действия клавиш и кнопок мыши в этом профиле.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="248"/>
        <source>Bindings of the active profile</source>
        <translation>Назначения активного профиля</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="263"/>
        <source>Binding</source>
        <translation>Назначение</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="271"/>
        <source>Trigger kind</source>
        <translation>Вид триггера</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="272"/>
        <location filename="../../ui/bindings.py" line="279"/>
        <source>Keyboard key</source>
        <translation>Клавиша клавиатуры</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="273"/>
        <location filename="../../ui/bindings.py" line="295"/>
        <source>Mouse button</source>
        <translation>Кнопка мыши</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="274"/>
        <source>Consumer control</source>
        <translation>Мультимедийное управление</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="276"/>
        <source>Trigger:</source>
        <translation>Триггер:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="283"/>
        <source>Key:</source>
        <translation>Клавиша:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="289"/>
        <source>Consumer usage</source>
        <translation>Код consumer-управления</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="291"/>
        <source>Consumer usage:</source>
        <translation>Код consumer-управления:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="297"/>
        <source>Button:</source>
        <translation>Кнопка:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="304"/>
        <source>{0} modifier</source>
        <translation>Модификатор {0}</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="309"/>
        <source>Modifiers:</source>
        <translation>Модификаторы:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="312"/>
        <source>Binding mode</source>
        <translation>Режим назначения</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="313"/>
        <source>Replace</source>
        <translation>Заменить</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="314"/>
        <location filename="../../ui/bindings.py" line="349"/>
        <source>Add</source>
        <translation>Добавить</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="315"/>
        <source>Mode:</source>
        <translation>Режим:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="318"/>
        <source>Action</source>
        <translation>Действие</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="330"/>
        <source>Action:</source>
        <translation>Действие:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="333"/>
        <source>Action target</source>
        <translation>Цель действия</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="335"/>
        <source>Target:</source>
        <translation>Цель:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="339"/>
        <source>Why this binding cannot be used</source>
        <translation>Почему это назначение нельзя применить</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="346"/>
        <source>Detect</source>
        <translation>Определить</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="347"/>
        <source>Detect the trigger on the device</source>
        <translation>Определить триггер на устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="350"/>
        <source>Add this binding</source>
        <translation>Добавить это назначение</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="353"/>
        <source>Apply</source>
        <translation>Применить</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="354"/>
        <source>Apply the changes to the selected binding</source>
        <translation>Применить изменения к выбранному назначению</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="356"/>
        <source>Remove</source>
        <translation>Удалить</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="357"/>
        <source>Remove the selected binding</source>
        <translation>Удалить выбранное назначение</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="553"/>
        <source>Button {0}</source>
        <translation>Кнопка {0}</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="615"/>
        <source>No mouse is attached, so no mouse button can be bound.</source>
        <translation>Мышь не подключена, поэтому назначить кнопку мыши нельзя.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="618"/>
        <source>Choose a trigger first.</source>
        <translation>Сначала выберите триггер.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="621"/>
        <source>This profile has no macros to run.</source>
        <translation>В этом профиле нет макросов для запуска.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="623"/>
        <source>Choose what the trigger should do.</source>
        <translation>Выберите, что должен делать триггер.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="628"/>
        <source>{0} is already bound in this profile.</source>
        <translation>{0} уже назначен в этом профиле.</translation>
    </message>
</context>
<context>
    <name>CaptureDialog</name>
    <message>
        <location filename="../../ui/bindings.py" line="114"/>
        <source>Detect a key or button</source>
        <extracomment>Seconds the device keeps capture mode open, mirrored from the design spec. Actions whose argument is fixed at zero.</extracomment>
        <translation>Определение клавиши или кнопки</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="119"/>
        <source>Press the key or mouse button you want to bind.</source>
        <translation>Нажмите клавишу или кнопку мыши, которую хотите назначить.</translation>
    </message>
    <message>
        <source>Time left to press a key</source>
        <translation type="vanished">Осталось времени на нажатие</translation>
    </message>
    <message>
        <source>{0} s left</source>
        <translation type="vanished">осталось {0} с</translation>
    </message>
</context>
<context>
    <name>ClipboardPage</name>
    <message>
        <location filename="../../ui/clipboard_page.py" line="59"/>
        <location filename="../../ui/clipboard_page.py" line="311"/>
        <source>Компьютер не выбран</source>
        <extracomment>Сколько последних событий держим на экране - не журнал целиком, а то, что помогает понять, что произошло только что (§12).</extracomment>
        <translation>Компьютер не выбран</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="63"/>
        <source>Связать компьютеры</source>
        <translation>Связать компьютеры</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="66"/>
        <source>Забыть компьютер</source>
        <translation>Забыть компьютер</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="70"/>
        <source>Общий буфер обмена</source>
        <translation>Общий буфер обмена</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="73"/>
        <source>Передача файлов</source>
        <translation>Передача файлов</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="78"/>
        <source>Загружать входящие файлы автоматически</source>
        <translation>Загружать входящие файлы автоматически</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="85"/>
        <source>Запускать вместе с Windows</source>
        <translation>Запускать вместе с Windows</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="89"/>
        <source>Отменить</source>
        <translation>Отменить</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="112"/>
        <source>Адрес второго компьютера, если поиск не нашёл</source>
        <extracomment>Последний адрес, который показал show_address_in_use. Нужен, чтобы отличить настоящую правку от editingFinished на голой потере фокуса - оно срабатывает и без единого нажатия клавиши. Последний текст, который действительно ушёл сигналом address_changed. Нужен, чтобы один и тот же выбор не улетал дважды: клик по элементу списка шлёт activated, а следующая обычная потеря фокуса - editingFinished с тем же текстом. None - ещё ни разу не отправляли, поэтому пустая строка не гасится сама.</extracomment>
        <translation>Адрес второго компьютера, если поиск не нашёл</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="213"/>
        <source>Входящие соединения запрещены политикой администратора этого компьютера.</source>
        <translation>Входящие соединения запрещены политикой администратора этого компьютера.</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="218"/>
        <source>Windows не разрешает Duo Input входящие соединения в локальной сети - второй компьютер не сможет подключиться.</source>
        <translation>Windows не разрешает Duo Input входящие соединения в локальной сети - второй компьютер не сможет подключиться.</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="124"/>
        <source>Разрешить</source>
        <translation>Разрешить</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="140"/>
        <source>Второй компьютер</source>
        <translation>Второй компьютер</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="188"/>
        <source>Получение {0} / {1}</source>
        <translation>Получение {0} / {1}</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="264"/>
        <location filename="../../ui/clipboard_page.py" line="303"/>
        <source>введён вручную</source>
        <translation>введён вручную</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="270"/>
        <source>найден автоматически</source>
        <translation>найден автоматически</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="316"/>
        <source>Отпечаток: {0}</source>
        <translation>Отпечаток: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="134"/>
        <location filename="../../ui/clipboard_page.py" line="136"/>
        <source>Последние события</source>
        <translation>Последние события</translation>
    </message>
</context>
<context>
    <name>CountdownRing</name>
    <message>
        <location filename="../../ui/theme.py" line="411"/>
        <source>{0} s left</source>
        <extracomment>Body text: headings, values, anything the operator reads to get an answer. Field labels and secondary text. Readable, deliberately not the loudest. Absence of data. Quieter than a label so ``unknown`` cannot pass for a value. Cards, tables and text inputs. The page behind the cards. Toolbar, state strip and status bar - the frame around the pages. Hairlines between and around things. The heavier line an input or a button draws around itself. The navigation rail: dark, so the chrome never competes with the content. Interactive. Save, selection, focus. Agreement: saved, in sync, connected. Divergence: unsaved edits, a device holding something else. Failure, and the one button that overwrites the device. Which wash sits behind each signal colour when it is used as a chip. Chips, captions and small print. Controls, labels and values - everything by default. The title of one card. The title of a page. Weights. Qt maps these onto the faces the family actually ships. What the interface is set in - bundled, not borrowed from the machine. Where digits have to line up: hashes, counters, byte sizes, IDs. Font files Windows ships that the offscreen database has to be handed. Everything that is a distance is a multiple of this. Minimum height of anything the operator clicks. Corner radius, one value for everything that has corners. Cards sit a little softer than the controls they contain. Object names the stylesheet reaches for by name rather than by role. Where the two image assets the stylesheet needs live on disk. The one drawn asset: a chevron for the combo boxes, at 1x and 2x.</extracomment>
        <translation>осталось {0} с</translation>
    </message>
</context>
<context>
    <name>DiagnosticsPage</name>
    <message>
        <location filename="../../ui/diagnostics.py" line="63"/>
        <source>Versions</source>
        <extracomment>Field key to the label beside it. Keys are stable identifiers.</extracomment>
        <translation>Версии</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="65"/>
        <source>Configurator</source>
        <translation>Конфигуратор</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="66"/>
        <source>CDC protocol</source>
        <translation>Протокол CDC</translation>
    </message>
    <message>
        <source>U1 firmware</source>
        <translation type="vanished">Прошивка U1</translation>
    </message>
    <message>
        <source>U2 firmware</source>
        <translation type="vanished">Прошивка U2</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="67"/>
        <source>Chip ID</source>
        <translation>Идентификатор чипа</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="71"/>
        <source>Health</source>
        <translation>Состояние</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="73"/>
        <source>Reset reason</source>
        <translation>Причина перезагрузки</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="74"/>
        <source>Watchdog resets</source>
        <translation>Перезагрузки по watchdog</translation>
    </message>
    <message>
        <source>CH375 state</source>
        <translation type="vanished">Состояние CH375</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="75"/>
        <source>Second board answering</source>
        <translation>Плата U2 отвечает</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="76"/>
        <source>Second board USB</source>
        <translation>USB платы U2</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="77"/>
        <source>SPI frames sent</source>
        <translation>Кадров отправлено по SPI</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="78"/>
        <source>SPI CRC errors</source>
        <translation>Ошибки CRC по SPI</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="79"/>
        <source>SPI frames echoed back</source>
        <translation>Кадров вернулось эхом</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="80"/>
        <source>SPI timeouts</source>
        <translation>Тайм-ауты SPI</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="81"/>
        <source>Link drops seen by U2</source>
        <translation>Срывов связи по счёту U2</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="82"/>
        <source>U2 released after (ms)</source>
        <translation>U2 отпустила через, мс</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="83"/>
        <source>Input commands never delivered</source>
        <translation>Команды ввода, не доставленные ни разу</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="84"/>
        <source>Interfaces refused: source capacity</source>
        <translation>Интерфейсов отклонено: предел источников</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="85"/>
        <source>Input source decoding</source>
        <translation>Декодирование источников ввода</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="86"/>
        <source>HID report sets</source>
        <translation>Наборы HID-отчётов</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="87"/>
        <source>Capture reception and filter</source>
        <translation>Получение и фильтрация захвата</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="91"/>
        <source>CDC counters</source>
        <translation>Счётчики CDC</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="93"/>
        <source>Bad CRC</source>
        <translation>Неверный CRC</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="94"/>
        <source>Bad sequence</source>
        <translation>Неверная последовательность</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="95"/>
        <source>Timeouts</source>
        <translation>Тайм-ауты</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="96"/>
        <source>Disconnects</source>
        <translation>Обрывы связи</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="97"/>
        <source>Aborted writes</source>
        <translation>Прерванные записи</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="101"/>
        <source>Configuration</source>
        <translation>Конфигурация</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="103"/>
        <source>Generation on device</source>
        <translation>Поколение на устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="104"/>
        <source>Hash on device</source>
        <translation>Хеш на устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="105"/>
        <source>Advertised capabilities</source>
        <translation>Заявленные возможности</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="112"/>
        <source>Input backend</source>
        <translation>Драйвер ввода</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="113"/>
        <source>Input backend counters</source>
        <translation>Счётчики драйвера ввода</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="118"/>
        <source>Input host stack</source>
        <translation>Хост-стек ввода</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="126"/>
        <source>Reference queue overflows</source>
        <translation>Переполнения очереди эталона</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="130"/>
        <source>Reference ignored interfaces</source>
        <translation>Игнорированные интерфейсы эталона</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="134"/>
        <source>Reference keyboard role ready</source>
        <translation>Роль клавиатуры готова (эталон)</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="138"/>
        <source>Reference mouse role ready</source>
        <translation>Роль мыши готова (эталон)</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="140"/>
        <source>Peripherals</source>
        <translation>Периферия</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="160"/>
        <source>Diagnostics</source>
        <translation>Диагностика</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="162"/>
        <source>What the device reports about itself, and how to send it on.</source>
        <translation>Что устройство сообщает о себе и как передать это дальше.</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="233"/>
        <source>A report never contains your macro text. Including the saved project adds that file, and everything you typed into it.</source>
        <translation>Отчёт никогда не содержит текст ваших макросов. Включение сохранённого проекта добавляет этот файл и всё, что вы в нём набрали.</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="245"/>
        <source>Refresh</source>
        <translation>Обновить</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="246"/>
        <source>Ask the device for its counters</source>
        <translation>Запросить счётчики у устройства</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="249"/>
        <source>Include the saved project</source>
        <translation>Включить сохранённый проект</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="251"/>
        <source>Include the saved project in the report</source>
        <translation>Включить сохранённый проект в отчёт</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="255"/>
        <source>Export report...</source>
        <translation>Экспорт отчёта...</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="256"/>
        <source>Save a diagnostic report</source>
        <translation>Сохранить диагностический отчёт</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="335"/>
        <source>Export diagnostic report</source>
        <translation>Экспорт диагностического отчёта</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="342"/>
        <source>Export failed</source>
        <translation>Экспорт не выполнен</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="345"/>
        <source>Report saved</source>
        <translation>Отчёт сохранён</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="345"/>
        <source>Saved to {0}</source>
        <translation>Сохранено в {0}</translation>
    </message>
</context>
<context>
    <name>MacroSteps</name>
    <message>
        <location filename="../../ui/models/macro_steps.py" line="153"/>
        <source>From the device: {0} keystrokes</source>
        <extracomment>How much of a text step is shown in the list before it is elided. A default step of every type, so the editor can offer all of them.</extracomment>
        <translation>С устройства: нажатий — {0}</translation>
    </message>
</context>
<context>
    <name>MacrosPage</name>
    <message>
        <location filename="../../ui/macros.py" line="145"/>
        <location filename="../../ui/macros.py" line="156"/>
        <source>Macros</source>
        <extracomment>The route a profile keyboard follows, as the macro target it corresponds to. Editor pages, one per step shape.</extracomment>
        <translation>Макросы</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="146"/>
        <source>Build a sequence once, then run it from any assigned trigger.</source>
        <translation>Создайте последовательность один раз и запускайте её назначенной клавишей или кнопкой.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="162"/>
        <source>Macros of the active profile</source>
        <translation>Макросы активного профиля</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="168"/>
        <source>Macro name</source>
        <translation>Имя макроса</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="171"/>
        <source>Name:</source>
        <translation>Имя:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="174"/>
        <source>Macro target</source>
        <translation>Цель макроса</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="176"/>
        <source>Inherit from profile</source>
        <translation>Как в профиле</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="179"/>
        <source>Both</source>
        <translation>Оба</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="183"/>
        <source>Types on:</source>
        <translation>Печатает на:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="188"/>
        <source>New macro</source>
        <translation>Новый макрос</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="189"/>
        <source>Add a macro to this profile</source>
        <translation>Добавить макрос в этот профиль</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="191"/>
        <source>Delete macro</source>
        <translation>Удалить макрос</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="192"/>
        <source>Delete the selected macro</source>
        <translation>Удалить выбранный макрос</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="194"/>
        <source>Test run...</source>
        <translation>Тестовый запуск...</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="195"/>
        <source>Run this macro on the device</source>
        <translation>Запустить этот макрос на устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="203"/>
        <source>Steps</source>
        <translation>Шаги</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="209"/>
        <source>Steps of the selected macro</source>
        <translation>Шаги выбранного макроса</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="223"/>
        <source>Step type</source>
        <translation>Тип шага</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="228"/>
        <source>Add step</source>
        <translation>Добавить шаг</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="229"/>
        <source>Add a step of this type</source>
        <translation>Добавить шаг этого типа</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="231"/>
        <source>Delete step</source>
        <translation>Удалить шаг</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="232"/>
        <source>Delete the selected step</source>
        <translation>Удалить выбранный шаг</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="234"/>
        <source>Up</source>
        <translation>Вверх</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="235"/>
        <source>Move the selected step earlier</source>
        <translation>Переместить выбранный шаг выше</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="237"/>
        <source>Down</source>
        <translation>Вниз</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="238"/>
        <source>Move the selected step later</source>
        <translation>Переместить выбранный шаг ниже</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="252"/>
        <source>Why this step cannot be stored</source>
        <translation>Почему этот шаг нельзя сохранить</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="258"/>
        <source>Apply to step</source>
        <translation>Применить к шагу</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="259"/>
        <source>Store the edited step</source>
        <translation>Сохранить изменённый шаг</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="273"/>
        <source>Key</source>
        <translation>Клавиша</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="277"/>
        <source>Key:</source>
        <translation>Клавиша:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="282"/>
        <source>{0} modifier</source>
        <translation>Модификатор {0}</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="289"/>
        <source>Modifiers:</source>
        <translation>Модификаторы:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="297"/>
        <source>Consumer usage</source>
        <translation>Код consumer-управления</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="301"/>
        <source>Usage:</source>
        <translation>Код:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="309"/>
        <source>Text to type</source>
        <translation>Текст для набора</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="320"/>
        <source>Shortest delay</source>
        <translation>Наименьшая задержка</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="324"/>
        <source>Longest delay</source>
        <translation>Наибольшая задержка</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="328"/>
        <source>From, ms:</source>
        <translation>От, мс:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="331"/>
        <source>To, ms:</source>
        <translation>До, мс:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="340"/>
        <source>Keyboard route</source>
        <translation>Маршрут клавиатуры</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="345"/>
        <location filename="../../ui/macros.py" line="359"/>
        <source>Route:</source>
        <translation>Маршрут:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="354"/>
        <source>Mouse route command</source>
        <translation>Команда маршрута мыши</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="368"/>
        <source>Profile to switch to</source>
        <translation>Профиль для переключения</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="373"/>
        <source>Profile:</source>
        <translation>Профиль:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="527"/>
        <source>This macro already holds {0} steps.</source>
        <translation>В этом макросе уже {0} шагов.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="530"/>
        <source>The shortest delay cannot exceed the longest.</source>
        <translation>Наименьшая задержка не может превышать наибольшую.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="540"/>
        <source>A text step holds at most {0} characters; this one has {1}.</source>
        <translation>Текстовый шаг вмещает не более {0} символов, а здесь их {1}.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="545"/>
        <source>{0!r} at position {1} cannot be typed on the {2} layout.</source>
        <translation>{0!r} в позиции {1} нельзя набрать в раскладке {2}.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="658"/>
        <source>Macro {0}</source>
        <translation>Макрос {0}</translation>
    </message>
</context>
<context>
    <name>MainWindow</name>
    <message>
        <location filename="../../ui/main_window.py" line="197"/>
        <source>Sections</source>
        <extracomment>How often the shell looks for a device that is not attached yet. Short enough that plugging a board in feels immediate, long enough that the retry costs nothing while the socket stays empty. Navigation rows, in the order the sections appear. Mouse buttons the attached device has actually reported. Set while a device-initiated read is in flight, so its answer - which arrives on ``operation_succeeded`` several chunks later - is only adopted when it is actually the read this window asked for. Set by a connect, cleared by the counter read it asks for. Which devices are on U1&apos;s own bus is a GET_DIAGNOSTICS answer, and the Mouse page cannot tell one device&apos;s press from another&apos;s without it - so the shell asks on every attach rather than waiting for someone to open Diagnostics and press Refresh. The project a write is currently sending, held from the moment it was compiled until the device confirms it. A write is chunks, then WRITE_COMMIT, then a read-back - every step a full event-loop turn, with the editor pages live throughout. What the board ends up holding is this project, not whatever is on screen when the last turn lands, so this is what the baseline becomes. Set once ``try_autoconnect`` has told the operator no device answered, so that message is said once rather than on every retry.</extracomment>
        <translation>Разделы</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="212"/>
        <source>Overview</source>
        <translation>Обзор</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="213"/>
        <source>Profiles</source>
        <translation>Профили</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="214"/>
        <source>Bindings</source>
        <translation>Назначения</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="215"/>
        <source>Macros</source>
        <translation>Макросы</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="216"/>
        <source>Mouse</source>
        <translation>Мышь</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="217"/>
        <source>Общий буфер</source>
        <translation>Общий буфер</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="218"/>
        <source>Diagnostics</source>
        <translation>Диагностика</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="219"/>
        <source>Settings</source>
        <translation>Настройки</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="269"/>
        <source>Load a copy...</source>
        <translation>Загрузить копию…</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="270"/>
        <source>Load a configuration from a file</source>
        <translation>Загрузить конфигурацию из файла</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="273"/>
        <source>Save a copy...</source>
        <translation>Сохранить копию…</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="275"/>
        <source>Save a copy of the configuration to a file</source>
        <translation>Сохранить копию конфигурации в файл</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="604"/>
        <source>Save a copy</source>
        <translation>Сохранение копии</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="638"/>
        <source>Load a copy</source>
        <translation>Загрузка копии</translation>
    </message>
    <message>
        <source>Open</source>
        <translation type="vanished">Открыть</translation>
    </message>
    <message>
        <source>Open a project file</source>
        <translation type="vanished">Открыть файл проекта</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="344"/>
        <source>Problems that block a write</source>
        <translation>Проблемы, мешающие записи</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="354"/>
        <source>Transfer progress</source>
        <translation>Ход передачи</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="239"/>
        <source>Ready</source>
        <translation>Готово</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="256"/>
        <source>Profile:</source>
        <translation>Профиль:</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="259"/>
        <source>Active profile</source>
        <translation>Активный профиль</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="265"/>
        <source>Device connection</source>
        <translation>Подключение устройства</translation>
    </message>
    <message>
        <source>Connect</source>
        <translation type="vanished">Подключить</translation>
    </message>
    <message>
        <source>Connect or disconnect the device</source>
        <translation type="vanished">Подключить или отключить устройство</translation>
    </message>
    <message>
        <source>Save</source>
        <translation type="vanished">Сохранить</translation>
    </message>
    <message>
        <source>Save the project file</source>
        <translation type="vanished">Сохранить файл проекта</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="282"/>
        <source>Write to device</source>
        <translation>Записать в устройство</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="283"/>
        <source>Write the configuration to the device</source>
        <translation>Записать конфигурацию в устройство</translation>
    </message>
    <message>
        <source>Local changes</source>
        <translation type="vanished">Локальные изменения</translation>
    </message>
    <message>
        <source>Project file</source>
        <translation type="vanished">Файл проекта</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="316"/>
        <source>Configuration on the device</source>
        <translation>Конфигурация в устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="335"/>
        <source>Fix these before writing to the device.</source>
        <translation>Исправьте это перед записью в устройство.</translation>
    </message>
    <message>
        <source>a new project</source>
        <translation type="vanished">новый проект</translation>
    </message>
    <message>
        <source>Unsaved work was found</source>
        <translation type="vanished">Найдена несохранённая работа</translation>
    </message>
    <message>
        <source>Duo Input closed with unsaved changes to {0} on {1}. Recover them?</source>
        <translation type="vanished">Duo Input закрылся с несохранёнными изменениями в {0} от {1}. Восстановить их?</translation>
    </message>
    <message>
        <source>Untitled project</source>
        <translation type="vanished">Безымянный проект</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="519"/>
        <source>Device: {0}</source>
        <translation>Устройство: {0}</translation>
    </message>
    <message>
        <source>Disconnect</source>
        <translation type="vanished">Отключить</translation>
    </message>
    <message>
        <source>Local changes: unsaved</source>
        <translation type="vanished">Локальные изменения: не сохранены</translation>
    </message>
    <message>
        <source>Local changes: none</source>
        <translation type="vanished">Локальные изменения: нет</translation>
    </message>
    <message>
        <source>Project file: not created yet</source>
        <translation type="vanished">Файл проекта: ещё не создан</translation>
    </message>
    <message>
        <source>Project file: {0}</source>
        <translation type="vanished">Файл проекта: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="536"/>
        <source>Written to device: no link</source>
        <translation>Записано в устройство: нет связи</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="542"/>
        <source>Written to device: matches the project</source>
        <translation>Записано в устройство: совпадает с проектом</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="539"/>
        <source>Written to device: differs from the project</source>
        <translation>Записано в устройство: отличается от проекта</translation>
    </message>
    <message>
        <source>Save project</source>
        <translation type="vanished">Сохранить проект</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="627"/>
        <source>Save failed</source>
        <translation>Сохранить не удалось</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="630"/>
        <source>Project saved</source>
        <translation>Проект сохранён</translation>
    </message>
    <message>
        <source>Open project</source>
        <translation type="vanished">Открыть проект</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="661"/>
        <source>That project could not be opened: {0}</source>
        <translation>Не удалось открыть проект: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="665"/>
        <source>Project opened</source>
        <translation>Проект открыт</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="677"/>
        <source>No Duo Input device was found</source>
        <translation>Устройство Duo Input не найдено</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="720"/>
        <source>No device found. Load a copy from a file, or plug the device in.</source>
        <translation>Устройство не найдено. Загрузите копию из файла или подключите устройство.</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="930"/>
        <source>The device has a different configuration; your edits were kept.</source>
        <translation>На устройстве другая конфигурация; ваши правки сохранены.</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="938"/>
        <source>The device&apos;s configuration could not be read: {0}</source>
        <translation>Не удалось прочитать конфигурацию устройства: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="942"/>
        <source>Configuration read from the device</source>
        <translation>Конфигурация прочитана с устройства</translation>
    </message>
    <message>
        <source>Unsaved changes</source>
        <translation type="vanished">Несохранённые изменения</translation>
    </message>
    <message>
        <source>The project has unsaved changes. Save them before closing?</source>
        <translation type="vanished">В проекте есть несохранённые изменения. Сохранить их перед закрытием?</translation>
    </message>
</context>
<context>
    <name>MouseSwitchPage</name>
    <message>
        <location filename="../../ui/mouse.py" line="103"/>
        <source>Mouse</source>
        <extracomment>Every action this page can produce, in the order it offers them. A mouse button the device just reported, so the shell can remember it.</extracomment>
        <translation>Мышь</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="105"/>
        <source>Choose the key or mouse button that sends the pointer to PC1 or PC2.</source>
        <translation>Выберите клавишу или кнопку мыши для переключения указателя между ПК1 и ПК2.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="132"/>
        <source>Both devices at once</source>
        <translation>Оба устройства сразу</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="138"/>
        <location filename="../../ui/mouse.py" line="141"/>
        <source>Switch the keyboard and the mouse together</source>
        <translation>Переключать клавиатуру и мышь вместе</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="150"/>
        <source>Switching either the keyboard or the mouse sends both devices to the same computer. This setting applies to every profile.</source>
        <translation>При переключении клавиатуры или мыши оба устройства будут направлены на один компьютер. Настройка действует для всех профилей.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="168"/>
        <source>Switch the mouse</source>
        <translation>Переключение мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="175"/>
        <source>Trigger kind</source>
        <translation>Вид триггера</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="176"/>
        <source>Choose...</source>
        <translation>Выберите...</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="177"/>
        <location filename="../../ui/mouse.py" line="185"/>
        <location filename="../../ui/mouse.py" line="533"/>
        <source>Keyboard key</source>
        <translation>Клавиша клавиатуры</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="178"/>
        <location filename="../../ui/mouse.py" line="201"/>
        <source>Mouse button</source>
        <translation>Кнопка мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="181"/>
        <source>1. What do you press?</source>
        <translation>1. Что вы нажимаете?</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="189"/>
        <source>Key:</source>
        <translation>Клавиша:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="207"/>
        <source>Detect button or key</source>
        <translation>Определить кнопку или клавишу</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="440"/>
        <source>Press the button or key on the mouse you want to use.</source>
        <translation>Нажмите на мыши кнопку или клавишу, которую хотите использовать.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="209"/>
        <source>Detect a button or key on the mouse</source>
        <translation>Определить кнопку или клавишу на мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="179"/>
        <source>Consumer control</source>
        <translation>Мультимедийное управление</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="195"/>
        <location filename="../../ui/mouse.py" line="536"/>
        <source>Consumer usage</source>
        <translation>Код consumer-управления</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="197"/>
        <source>Consumer usage:</source>
        <translation>Код consumer-управления:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="215"/>
        <source>Button:</source>
        <translation>Кнопка:</translation>
    </message>
    <message>
        <source>Selected mouse control</source>
        <translation type="vanished">Выбранный элемент управления мышью</translation>
    </message>
    <message>
        <source>Edit manually</source>
        <translation type="vanished">Изменить вручную</translation>
    </message>
    <message>
        <source>Edit the detected trigger manually</source>
        <translation type="vanished">Изменить обнаруженный триггер вручную</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="222"/>
        <location filename="../../ui/mouse.py" line="544"/>
        <source>{0} modifier</source>
        <translation>Модификатор {0}</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="229"/>
        <source>Modifiers:</source>
        <translation>Модификаторы:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="233"/>
        <source>What the trigger does</source>
        <translation>Что делает триггер</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="234"/>
        <source>Toggle between PC1 and PC2</source>
        <translation>Переключать между ПК1 и ПК2</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="235"/>
        <source>Always PC1</source>
        <translation>Всегда ПК1</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="236"/>
        <source>Always PC2</source>
        <translation>Всегда ПК2</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="238"/>
        <source>2. What should it do?</source>
        <translation>2. Что это должно делать?</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="241"/>
        <source>Binding mode</source>
        <translation>Режим назначения</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="242"/>
        <source>Replace the key</source>
        <translation>Заменить клавишу</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="243"/>
        <source>Add to the key</source>
        <translation>Добавить к клавише</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="244"/>
        <source>Mode:</source>
        <translation>Режим:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="248"/>
        <source>Mouse switching warnings</source>
        <translation>Предупреждения о переключении мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="255"/>
        <source>Bind</source>
        <translation>Назначить</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="256"/>
        <source>Bind this trigger to the mouse route</source>
        <translation>Назначить этот триггер на маршрут мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="265"/>
        <source>Mouse switching in this profile</source>
        <translation>Переключение мыши в этом профиле</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="268"/>
        <source>Existing mouse switch bindings</source>
        <translation>Существующие назначения переключения мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="459"/>
        <location filename="../../ui/mouse.py" line="494"/>
        <source>Side button</source>
        <translation>Боковая кнопка</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="482"/>
        <source>Left button</source>
        <translation>Левая</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="483"/>
        <source>Right button</source>
        <translation>Правая</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="484"/>
        <source>Middle button (wheel)</source>
        <translation>Средняя (колесо)</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="485"/>
        <source>Side button 1</source>
        <translation>Боковая кнопка 1</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="486"/>
        <source>Side button 2</source>
        <translation>Боковая кнопка 2</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="488"/>
        <source>Button {0}</source>
        <translation>Кнопка {0}</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="531"/>
        <source>Not applicable</source>
        <translation>Неприменимо</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="472"/>
        <source>Toggle</source>
        <translation>Переключение</translation>
    </message>
    <message>
        <source>Additional mouse button</source>
        <translation type="vanished">Дополнительная кнопка мыши</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="579"/>
        <location filename="../../ui/mouse.py" line="584"/>
        <source>Choose what you press first.</source>
        <translation>Сначала выберите, что вы нажимаете.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="581"/>
        <source>No mouse is attached, so no mouse button can be bound.</source>
        <translation>Мышь не подключена, поэтому назначить кнопку мыши нельзя.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="588"/>
        <source>{0} is already bound in this profile.</source>
        <translation>{0} уже назначен в этом профиле.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="605"/>
        <source>{0} is bound here but the attached mouse has not reported it.</source>
        <translation>{0} назначена здесь, но подключённая мышь о ней не сообщала.</translation>
    </message>
</context>
<context>
    <name>OverviewPage</name>
    <message>
        <location filename="../../ui/overview.py" line="87"/>
        <source>Overview</source>
        <extracomment>Shown wherever the device or the project genuinely does not supply a value. Fields whose value is a number, a version or a protocol identifier. They are set in the fixed-width face so a column of them can be read downwards. Fields holding a SHA-256 digest: sixty-four characters with nowhere to wrap.</extracomment>
        <translation>Обзор</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="89"/>
        <source>What the device reports, and what this project would send it.</source>
        <translation>Что сообщает устройство и что отправил бы в него этот проект.</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="104"/>
        <source>Device</source>
        <translation>Устройство</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="106"/>
        <source>U1 protocol version</source>
        <translation>Версия протокола U1</translation>
    </message>
    <message>
        <source>U1 firmware version</source>
        <translation type="vanished">Версия прошивки U1</translation>
    </message>
    <message>
        <source>U2 firmware version</source>
        <translation type="vanished">Версия прошивки U2</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="107"/>
        <source>Configuration generation</source>
        <translation>Поколение конфигурации</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="108"/>
        <source>Profile active on device</source>
        <translation>Профиль, активный на устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="116"/>
        <source>Peripherals</source>
        <translation>Периферия</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="118"/>
        <source>Keyboard HID</source>
        <translation>HID клавиатуры</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="119"/>
        <source>Mouse HID</source>
        <translation>HID мыши</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="120"/>
        <source>Consumer HID</source>
        <translation>HID consumer</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="128"/>
        <source>Active profile and routes</source>
        <translation>Активный профиль и маршруты</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="130"/>
        <source>Active profile</source>
        <translation>Активный профиль</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="131"/>
        <source>Keyboard route</source>
        <translation>Маршрут клавиатуры</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="132"/>
        <source>Mouse route</source>
        <translation>Маршрут мыши</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="133"/>
        <source>Text layout</source>
        <translation>Раскладка текста</translation>
    </message>
    <message>
        <source>Memory usage</source>
        <translation type="vanished">Использование памяти</translation>
    </message>
    <message>
        <source>Project package</source>
        <translation type="vanished">Пакет проекта</translation>
    </message>
    <message>
        <source>Package on device</source>
        <translation type="vanished">Пакет на устройстве</translation>
    </message>
    <message>
        <source>Configuration state</source>
        <translation type="vanished">Состояние конфигурации</translation>
    </message>
    <message>
        <source>Saved file</source>
        <translation type="vanished">Сохранённый файл</translation>
    </message>
    <message>
        <source>Compiled project</source>
        <translation type="vanished">Скомпилированный проект</translation>
    </message>
    <message>
        <source>On device</source>
        <translation type="vanished">На устройстве</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="140"/>
        <source>Recent events</source>
        <translation>Последние события</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="144"/>
        <source>Recent device events</source>
        <translation>Последние события устройства</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="253"/>
        <source>advertised</source>
        <translation>заявлено</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="255"/>
        <source>not advertised</source>
        <translation>не заявлено</translation>
    </message>
    <message>
        <source>{0} of {1} bytes ({2:.1f}%)</source>
        <translation type="vanished">{0} из {1} байт ({2:.1f}%)</translation>
    </message>
</context>
<context>
    <name>PairingDialog</name>
    <message>
        <location filename="../../app.py" line="183"/>
        <source>Подтвердите связывание</source>
        <extracomment>Console script target declared in ``pyproject.toml``. Аргумент командной строки, которым автозапуск просит не показывать окно - см. persistence/autostart.py и §4 спецификации.</extracomment>
        <translation>Подтвердите связывание</translation>
    </message>
    <message>
        <location filename="../../app.py" line="184"/>
        <source>Компьютер «{0}» показывает тот же код?

Код: {1}</source>
        <translation>Компьютер «{0}» показывает тот же код?

Код: {1}</translation>
    </message>
    <message>
        <location filename="../../app.py" line="194"/>
        <source>Связать</source>
        <translation>Связать</translation>
    </message>
    <message>
        <location filename="../../app.py" line="198"/>
        <source>Отказать</source>
        <translation>Отказать</translation>
    </message>
</context>
<context>
    <name>ProfilesPage</name>
    <message>
        <location filename="../../ui/profiles.py" line="77"/>
        <source>Profiles</source>
        <extracomment>Side of the square that shows a profile&apos;s colour, in logical pixels. Longest profile name the binary format accepts, mirrored from validation. Item data role carrying the profile ID of a slot row.</extracomment>
        <translation>Профили</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="79"/>
        <source>Eight slots the device switches between. One of them is the one it starts in.</source>
        <translation>Восемь слотов, между которыми переключается устройство. Один из них используется при запуске.</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="92"/>
        <source>Profile slots</source>
        <translation>Слоты профилей</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="110"/>
        <source>Profile</source>
        <translation>Профиль</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="119"/>
        <source>Profile name</source>
        <translation>Имя профиля</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="122"/>
        <source>Name:</source>
        <translation>Имя:</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="125"/>
        <location filename="../../ui/profiles.py" line="316"/>
        <source>Profile colour</source>
        <translation>Цвет профиля</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="129"/>
        <source>Colour:</source>
        <translation>Цвет:</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="132"/>
        <source>Profile routes</source>
        <translation>Маршруты профиля</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="135"/>
        <source>Routes:</source>
        <translation>Маршруты:</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="141"/>
        <source>Copy destination</source>
        <translation>Куда копировать</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="142"/>
        <source>Copy into</source>
        <translation>Копировать в</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="143"/>
        <source>Copy this profile into another slot</source>
        <translation>Скопировать этот профиль в другой слот</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="151"/>
        <source>Make active</source>
        <translation>Сделать активным</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="152"/>
        <source>Start the device in this profile</source>
        <translation>Запускать устройство в этом профиле</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="154"/>
        <source>Clear</source>
        <translation>Очистить</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="155"/>
        <source>Reset this profile slot</source>
        <translation>Сбросить этот слот профиля</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="242"/>
        <source>{0} - {1} ({2} bindings, {3} macros){4}</source>
        <translation>{0} - {1} (назначений: {2}, макросов: {3}){4}</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="269"/>
        <source>{0} — brought together on activation</source>
        <translation>{0} — сводятся при активации</translation>
    </message>
</context>
<context>
    <name>SettingsPage</name>
    <message>
        <location filename="../../ui/settings.py" line="72"/>
        <source>Settings</source>
        <extracomment>Preferences are short answers; a full-window text field only looks empty. How each shipped language names itself, in that language. Log levels the operator may choose, quietest last.</extracomment>
        <translation>Настройки</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="73"/>
        <source>Preferences that belong to you, not to the project.</source>
        <translation>Параметры программы, которые относятся к вам, а не к проекту.</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="86"/>
        <source>Interface</source>
        <translation>Интерфейс</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="92"/>
        <source>Interface language</source>
        <translation>Язык интерфейса</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="96"/>
        <source>Language:</source>
        <translation>Язык:</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="99"/>
        <source>What happens after a language change</source>
        <translation>Что произойдёт после смены языка</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="106"/>
        <source>Projects</source>
        <translation>Проекты</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="115"/>
        <location filename="../../ui/settings.py" line="215"/>
        <source>Default project folder</source>
        <translation>Папка проектов по умолчанию</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="117"/>
        <source>Browse...</source>
        <translation>Обзор...</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="118"/>
        <source>Choose the default project folder</source>
        <translation>Выбрать папку проектов по умолчанию</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="124"/>
        <source>Folder:</source>
        <translation>Папка:</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="128"/>
        <source>Logging</source>
        <translation>Журналирование</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="134"/>
        <source>How much is written to the log</source>
        <translation>Сколько записывается в журнал</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="138"/>
        <source>Level:</source>
        <translation>Уровень:</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="141"/>
        <source>Macro text is never written to the log, at any level.</source>
        <translation>Текст макросов не попадает в журнал ни на одном уровне.</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="205"/>
        <source>The interface language changes the next time Duo Input starts.</source>
        <translation>Язык интерфейса изменится при следующем запуске Duo Input.</translation>
    </message>
</context>
<context>
    <name>TestMacroDialog</name>
    <message>
        <location filename="../../ui/macros.py" line="721"/>
        <source>Test run</source>
        <translation>Тестовый запуск</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="729"/>
        <source>This runs macro #{0} on the device. It generates real key presses on the computer it is routed to.</source>
        <translation>Это запустит макрос №{0} на устройстве. Он создаёт настоящие нажатия клавиш на том компьютере, куда направлен.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="738"/>
        <source>Target computer</source>
        <translation>Целевой компьютер</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="739"/>
        <source>Choose...</source>
        <translation>Выберите...</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="743"/>
        <source>Runs on:</source>
        <translation>Выполняется на:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="747"/>
        <source>I understand this types on a real computer.</source>
        <translation>Я понимаю, что набор пойдёт на настоящий компьютер.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="749"/>
        <source>Confirm the test run</source>
        <translation>Подтвердить тестовый запуск</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="754"/>
        <source>Why this macro cannot be tested</source>
        <translation>Почему этот макрос нельзя протестировать</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="759"/>
        <source>Run</source>
        <translation>Запустить</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="760"/>
        <source>Run the macro now</source>
        <translation>Запустить макрос сейчас</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="763"/>
        <source>Stop everything and release every key</source>
        <translation>Остановить всё и отпустить все клавиши</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="766"/>
        <source>Close</source>
        <translation>Закрыть</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="788"/>
        <source>The device is not connected.</source>
        <translation>Устройство не подключено.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="791"/>
        <source>The device is not holding this project. Write it first, so the macro that runs is the macro on screen.</source>
        <translation>На устройстве записан другой проект. Сначала запишите этот, чтобы запускался именно тот макрос, что на экране.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="796"/>
        <source>Choose which computer the macro should type on.</source>
        <translation>Выберите, на каком компьютере макрос должен печатать.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="800"/>
        <source>This macro is configured to type on {0}. Change its target first.</source>
        <translation>Этот макрос настроен печатать на {0}. Сначала измените его цель.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="803"/>
        <source>Confirm that you expect real key presses.</source>
        <translation>Подтвердите, что вы ожидаете настоящих нажатий клавиш.</translation>
    </message>
</context>
<context>
    <name>TrayIcon</name>
    <message>
        <location filename="../../ui/tray.py" line="15"/>
        <source>Второй компьютер на связи</source>
        <translation>Второй компьютер на связи</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="16"/>
        <source>Нет связи со вторым компьютером</source>
        <translation>Нет связи со вторым компьютером</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="17"/>
        <source>Компьютеры не связаны</source>
        <translation>Компьютеры не связаны</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="18"/>
        <source>Поиск второго компьютера</source>
        <translation>Поиск второго компьютера</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="21"/>
        <source>Windows не разрешила подключение — проверьте брандмауэр</source>
        <translation>Windows не разрешила подключение — проверьте брандмауэр</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="26"/>
        <source>Обновите вторую машину — версии протокола различаются</source>
        <translation>Обновите вторую машину — версии протокола различаются</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="47"/>
        <source>Открыть Duo Input</source>
        <translation>Открыть Duo Input</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="57"/>
        <source>Общий буфер обмена</source>
        <translation>Общий буфер обмена</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="62"/>
        <source>Передача файлов</source>
        <translation>Передача файлов</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="69"/>
        <source>Выход</source>
        <translation>Выход</translation>
    </message>
</context>
<context>
    <name>_ClipboardRuntime</name>
    <message>
        <location filename="../../app.py" line="532"/>
        <source>Брандмауэр Windows</source>
        <translation>Брандмауэр Windows</translation>
    </message>
    <message>
        <location filename="../../app.py" line="537"/>
        <source>Входящие соединения запрещены политикой администратора этого компьютера.</source>
        <translation>Входящие соединения запрещены политикой администратора этого компьютера.</translation>
    </message>
    <message>
        <location filename="../../app.py" line="540"/>
        <source>Понятно</source>
        <translation>Понятно</translation>
    </message>
    <message>
        <location filename="../../app.py" line="545"/>
        <source>Чтобы второй компьютер мог подключиться, Windows должна разрешить Duo Input входящие соединения в локальной сети.</source>
        <translation>Чтобы второй компьютер мог подключиться, Windows должна разрешить Duo Input входящие соединения в локальной сети.</translation>
    </message>
    <message>
        <location filename="../../app.py" line="549"/>
        <source>Разрешить</source>
        <translation>Разрешить</translation>
    </message>
    <message>
        <location filename="../../app.py" line="550"/>
        <source>Позже</source>
        <translation>Позже</translation>
    </message>
    <message>
        <location filename="../../app.py" line="878"/>
        <source>Входящие файлы</source>
        <translation>Входящие файлы</translation>
    </message>
    <message>
        <location filename="../../app.py" line="881"/>
        <source>Другой компьютер хочет передать {0} объект(ов) ({1:.1f} МБ).</source>
        <translation>Другой компьютер хочет передать {0} объект(ов) ({1:.1f} МБ).</translation>
    </message>
</context>
</TS>
