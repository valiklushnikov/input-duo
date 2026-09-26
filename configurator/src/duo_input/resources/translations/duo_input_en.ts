<?xml version='1.0' encoding='utf-8'?>
<TS version="2.1" language="en_US">
<context>
    <name>BindingTable</name>
    <message>
        <location filename="../../ui/models/binding_table.py" line="192" />
        <source>Switch the keyboard between PC1 and PC2</source>
        <extracomment>Modifier key of the editor, in the order the labels are joined. Buttons every HID mouse reports; 4 and 5 have to be seen before they exist. Highest button number the binary format can store, mirrored from validation. HID usage to the legend the operator sees. Missing usages read as a number. Usages this program can name but must not offer. A binding on a key the operator has no way to press is one that silently never fires, and the chooser is where that mistake would be made. They stay in KEY_NAMES so a configuration read back from the device is still legible. Usages the key chooser offers, in HID order. What each action does, said the way the operator would say it. The protocol name stays available in a tooltip: a screenshot has to be readable against the diagnostics and the documentation, which both speak in identifiers.</extracomment>
        <translation>Switch the keyboard between PC1 and PC2</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="193" />
        <source>Each press sends the keyboard to the other computer.</source>
        <translation>Each press sends the keyboard to the other computer.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="199" />
        <source>Switch the mouse between PC1 and PC2</source>
        <translation>Switch the mouse between PC1 and PC2</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="200" />
        <source>Each press sends the mouse to the other computer.</source>
        <translation>Each press sends the mouse to the other computer.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="205" />
        <source>Send the keyboard to one computer</source>
        <translation>Send the keyboard to one computer</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="206" />
        <location filename="../../ui/models/binding_table.py" line="213" />
        <source>Always the same computer, whichever one was being used before.</source>
        <translation>Always the same computer, whichever one was being used before.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="212" />
        <source>Send the mouse to one computer</source>
        <translation>Send the mouse to one computer</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="219" />
        <source>Switch to another profile</source>
        <translation>Switch to another profile</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="220" />
        <source>Loads a different set of bindings on the device.</source>
        <translation>Loads a different set of bindings on the device.</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="225" />
        <source>Run a macro</source>
        <translation>Run a macro</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="226" />
        <source>Plays a recorded sequence of keys and pauses.</source>
        <translation>Plays a recorded sequence of keys and pauses.</translation>
    </message>
</context>
<context>
    <name>BindingTableModel</name>
    <message>
        <location filename="../../ui/models/binding_table.py" line="491" />
        <source>Trigger</source>
        <extracomment>``(vendor_id, product_id)`` of every device U1 reported on its own bus. ``None`` while no report has said - which is not the same as an empty set, and is why an unread device greys nothing out. The one of them that enumerated as a mouse, when U1 named one.</extracomment>
        <translation>Trigger</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="491" />
        <source>Mode</source>
        <translation>Mode</translation>
    </message>
    <message>
        <location filename="../../ui/models/binding_table.py" line="491" />
        <source>Action</source>
        <translation>Action</translation>
    </message>
</context>
<context>
    <name>BindingsPage</name>
    <message>
        <location filename="../../ui/bindings.py" line="240" />
        <source>Bindings</source>
        <extracomment>A mouse button the device just reported, so the shell can remember it.</extracomment>
        <translation>Bindings</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="241" />
        <source>Choose what a key or mouse button does in this profile.</source>
        <translation>Choose what a key or mouse button does in this profile.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="248" />
        <source>Bindings of the active profile</source>
        <translation>Bindings of the active profile</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="263" />
        <source>Binding</source>
        <translation>Binding</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="271" />
        <source>Trigger kind</source>
        <translation>Trigger kind</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="272" />
        <location filename="../../ui/bindings.py" line="279" />
        <source>Keyboard key</source>
        <translation>Keyboard key</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="273" />
        <location filename="../../ui/bindings.py" line="295" />
        <source>Mouse button</source>
        <translation>Mouse button</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="274" />
        <source>Consumer control</source>
        <translation>Consumer control</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="276" />
        <source>Trigger:</source>
        <translation>Trigger:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="283" />
        <source>Key:</source>
        <translation>Key:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="289" />
        <source>Consumer usage</source>
        <translation>Consumer usage</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="291" />
        <source>Consumer usage:</source>
        <translation>Consumer usage:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="297" />
        <source>Button:</source>
        <translation>Button:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="304" />
        <source>{0} modifier</source>
        <translation>{0} modifier</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="309" />
        <source>Modifiers:</source>
        <translation>Modifiers:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="312" />
        <source>Binding mode</source>
        <translation>Binding mode</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="313" />
        <source>Replace</source>
        <translation>Replace</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="314" />
        <location filename="../../ui/bindings.py" line="349" />
        <source>Add</source>
        <translation>Add</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="315" />
        <source>Mode:</source>
        <translation>Mode:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="318" />
        <source>Action</source>
        <translation>Action</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="330" />
        <source>Action:</source>
        <translation>Action:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="333" />
        <source>Action target</source>
        <translation>Action target</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="335" />
        <source>Target:</source>
        <translation>Target:</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="339" />
        <source>Why this binding cannot be used</source>
        <translation>Why this binding cannot be used</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="346" />
        <source>Detect</source>
        <translation>Detect</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="347" />
        <source>Detect the trigger on the device</source>
        <translation>Detect the trigger on the device</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="350" />
        <source>Add this binding</source>
        <translation>Add this binding</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="353" />
        <source>Apply</source>
        <translation>Apply</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="354" />
        <source>Apply the changes to the selected binding</source>
        <translation>Apply the changes to the selected binding</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="356" />
        <source>Remove</source>
        <translation>Remove</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="357" />
        <source>Remove the selected binding</source>
        <translation>Remove the selected binding</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="553" />
        <source>Button {0}</source>
        <translation>Button {0}</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="615" />
        <source>No mouse is attached, so no mouse button can be bound.</source>
        <translation>No mouse is attached, so no mouse button can be bound.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="618" />
        <source>Choose a trigger first.</source>
        <translation>Choose a trigger first.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="621" />
        <source>This profile has no macros to run.</source>
        <translation>This profile has no macros to run.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="623" />
        <source>Choose what the trigger should do.</source>
        <translation>Choose what the trigger should do.</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="628" />
        <source>{0} is already bound in this profile.</source>
        <translation>{0} is already bound in this profile.</translation>
    </message>
</context>
<context>
    <name>CaptureDialog</name>
    <message>
        <location filename="../../ui/bindings.py" line="114" />
        <source>Detect a key or button</source>
        <extracomment>Seconds the device keeps capture mode open, mirrored from the design spec. Actions whose argument is fixed at zero.</extracomment>
        <translation>Detect a key or button</translation>
    </message>
    <message>
        <location filename="../../ui/bindings.py" line="119" />
        <source>Press the key or mouse button you want to bind.</source>
        <translation>Press the key or mouse button you want to bind.</translation>
    </message>
    <message>
        <source>Time left to press a key</source>
        <translation>Time left to press a key</translation>
    </message>
    <message>
        <source>{0} s left</source>
        <translation>{0} s left</translation>
    </message>
</context>
<context>
    <name>ClipboardPage</name>
    <message>
        <location filename="../../ui/clipboard_page.py" line="59" />
        <location filename="../../ui/clipboard_page.py" line="301" />
        <source>Компьютер не выбран</source>
        <extracomment>Сколько последних событий держим на экране - не журнал целиком, а то, что помогает понять, что произошло только что (§12).</extracomment>
        <translation>No computer selected</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="63" />
        <source>Связать компьютеры</source>
        <translation>Pair computers</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="66" />
        <source>Забыть компьютер</source>
        <translation>Forget computer</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="70" />
        <source>Общий буфер обмена</source>
        <translation>Shared clipboard</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="73" />
        <source>Передача файлов</source>
        <translation>File transfer</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="78" />
        <source>Загружать входящие файлы автоматически</source>
        <translation>Download incoming files automatically</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="85" />
        <source>Запускать вместе с Windows</source>
        <translation>Start with Windows</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="89" />
        <source>Отменить</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="112" />
        <source>Адрес второго компьютера, если поиск не нашёл</source>
        <extracomment>Последний адрес, который показал show_address_in_use. Нужен, чтобы отличить настоящую правку от editingFinished на голой потере фокуса - оно срабатывает и без единого нажатия клавиши. Последний текст, который действительно ушёл сигналом address_changed. Нужен, чтобы один и тот же выбор не улетал дважды: клик по элементу списка шлёт activated, а следующая обычная потеря фокуса - editingFinished с тем же текстом. None - ещё ни разу не отправляли, поэтому пустая строка не гасится сама.</extracomment>
        <translation>The other computer's address, if search could not find it</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="122" />
        <source>Windows не разрешает Duo Input входящие соединения в локальной сети - второй компьютер не сможет подключиться.</source>
        <translation>Windows does not allow Duo Input incoming connections on the local network - the other computer will not be able to connect.</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="128" />
        <source>Разрешить</source>
        <translation>Allow</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="144" />
        <source>Второй компьютер</source>
        <translation>Second computer</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="192" />
        <source>Получение {0} / {1}</source>
        <translation>Receiving {0} / {1}</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="254" />
        <location filename="../../ui/clipboard_page.py" line="293" />
        <source>введён вручную</source>
        <translation>entered manually</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="260" />
        <source>найден автоматически</source>
        <translation>found automatically</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="306" />
        <source>Отпечаток: {0}</source>
        <translation>Fingerprint: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/clipboard_page.py" line="138" />
        <location filename="../../ui/clipboard_page.py" line="140" />
        <source>Последние события</source>
        <translation>Recent events</translation>
    </message>
</context>
<context>
    <name>CountdownRing</name>
    <message>
        <location filename="../../ui/theme.py" line="411" />
        <source>{0} s left</source>
        <extracomment>Body text: headings, values, anything the operator reads to get an answer. Field labels and secondary text. Readable, deliberately not the loudest. Absence of data. Quieter than a label so ``unknown`` cannot pass for a value. Cards, tables and text inputs. The page behind the cards. Toolbar, state strip and status bar - the frame around the pages. Hairlines between and around things. The heavier line an input or a button draws around itself. The navigation rail: dark, so the chrome never competes with the content. Interactive. Save, selection, focus. Agreement: saved, in sync, connected. Divergence: unsaved edits, a device holding something else. Failure, and the one button that overwrites the device. Which wash sits behind each signal colour when it is used as a chip. Chips, captions and small print. Controls, labels and values - everything by default. The title of one card. The title of a page. Weights. Qt maps these onto the faces the family actually ships. What the interface is set in - bundled, not borrowed from the machine. Where digits have to line up: hashes, counters, byte sizes, IDs. Font files Windows ships that the offscreen database has to be handed. Everything that is a distance is a multiple of this. Minimum height of anything the operator clicks. Corner radius, one value for everything that has corners. Cards sit a little softer than the controls they contain. Object names the stylesheet reaches for by name rather than by role. Where the two image assets the stylesheet needs live on disk. The one drawn asset: a chevron for the combo boxes, at 1x and 2x.</extracomment>
        <translation>{0} s left</translation>
    </message>
</context>
<context>
    <name>DiagnosticsPage</name>
    <message>
        <location filename="../../ui/diagnostics.py" line="63" />
        <source>Versions</source>
        <extracomment>Field key to the label beside it. Keys are stable identifiers.</extracomment>
        <translation>Versions</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="65" />
        <source>Configurator</source>
        <translation>Configurator</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="66" />
        <source>CDC protocol</source>
        <translation>CDC protocol</translation>
    </message>
    <message>
        <source>U1 firmware</source>
        <translation>U1 firmware</translation>
    </message>
    <message>
        <source>U2 firmware</source>
        <translation>U2 firmware</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="67" />
        <source>Chip ID</source>
        <translation>Chip ID</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="71" />
        <source>Health</source>
        <translation>Health</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="73" />
        <source>Reset reason</source>
        <translation>Reset reason</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="74" />
        <source>Watchdog resets</source>
        <translation>Watchdog resets</translation>
    </message>
    <message>
        <source>CH375 state</source>
        <translation>CH375 state</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="75" />
        <source>Second board answering</source>
        <translation>Second board answering</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="76" />
        <source>Second board USB</source>
        <translation>Second board USB</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="77" />
        <source>SPI frames sent</source>
        <translation>SPI frames sent</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="78" />
        <source>SPI CRC errors</source>
        <translation>SPI CRC errors</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="79" />
        <source>SPI frames echoed back</source>
        <translation>SPI frames echoed back</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="80" />
        <source>SPI timeouts</source>
        <translation>SPI timeouts</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="81" />
        <source>Link drops seen by U2</source>
        <translation>Link drops seen by U2</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="82" />
        <source>U2 released after (ms)</source>
        <translation>U2 released after (ms)</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="83" />
        <source>Input commands never delivered</source>
        <translation>Input commands never delivered</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="84" />
        <source>Interfaces refused: source capacity</source>
        <translation>Interfaces refused: source capacity</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="85" />
        <source>Input source decoding</source>
        <translation>Input source decoding</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="86" />
        <source>HID report sets</source>
        <translation>HID report sets</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="87" />
        <source>Capture reception and filter</source>
        <translation>Capture reception and filter</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="91" />
        <source>CDC counters</source>
        <translation>CDC counters</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="93" />
        <source>Bad CRC</source>
        <translation>Bad CRC</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="94" />
        <source>Bad sequence</source>
        <translation>Bad sequence</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="95" />
        <source>Timeouts</source>
        <translation>Timeouts</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="96" />
        <source>Disconnects</source>
        <translation>Disconnects</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="97" />
        <source>Aborted writes</source>
        <translation>Aborted writes</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="101" />
        <source>Configuration</source>
        <translation>Configuration</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="103" />
        <source>Generation on device</source>
        <translation>Generation on device</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="104" />
        <source>Hash on device</source>
        <translation>Hash on device</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="105" />
        <source>Advertised capabilities</source>
        <translation>Advertised capabilities</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="112" />
        <source>Input backend</source>
        <translation>Input backend</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="113" />
        <source>Input backend counters</source>
        <translation>Input backend counters</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="118" />
        <source>Input host stack</source>
        <translation>Input host stack</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="126" />
        <source>Reference queue overflows</source>
        <translation>Reference queue overflows</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="130" />
        <source>Reference ignored interfaces</source>
        <translation>Reference ignored interfaces</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="134" />
        <source>Reference keyboard role ready</source>
        <translation>Reference keyboard role ready</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="138" />
        <source>Reference mouse role ready</source>
        <translation>Reference mouse role ready</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="140" />
        <source>Peripherals</source>
        <translation>Peripherals</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="160" />
        <source>Diagnostics</source>
        <translation>Diagnostics</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="162" />
        <source>What the device reports about itself, and how to send it on.</source>
        <translation>What the device reports about itself, and how to send it on.</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="233" />
        <source>A report never contains your macro text. Including the saved project adds that file, and everything you typed into it.</source>
        <translation>A report never contains your macro text. Including the saved project adds that file, and everything you typed into it.</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="245" />
        <source>Refresh</source>
        <translation>Refresh</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="246" />
        <source>Ask the device for its counters</source>
        <translation>Ask the device for its counters</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="249" />
        <source>Include the saved project</source>
        <translation>Include the saved project</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="251" />
        <source>Include the saved project in the report</source>
        <translation>Include the saved project in the report</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="255" />
        <source>Export report...</source>
        <translation>Export report...</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="256" />
        <source>Save a diagnostic report</source>
        <translation>Save a diagnostic report</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="335" />
        <source>Export diagnostic report</source>
        <translation>Export diagnostic report</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="342" />
        <source>Export failed</source>
        <translation>Export failed</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="345" />
        <source>Report saved</source>
        <translation>Report saved</translation>
    </message>
    <message>
        <location filename="../../ui/diagnostics.py" line="345" />
        <source>Saved to {0}</source>
        <translation>Saved to {0}</translation>
    </message>
</context>
<context>
    <name>MacroSteps</name>
    <message>
        <location filename="../../ui/models/macro_steps.py" line="153" />
        <source>From the device: {0} keystrokes</source>
        <extracomment>How much of a text step is shown in the list before it is elided. A default step of every type, so the editor can offer all of them.</extracomment>
        <translation>From the device: {0} keystrokes</translation>
    </message>
</context>
<context>
    <name>MacrosPage</name>
    <message>
        <location filename="../../ui/macros.py" line="145" />
        <location filename="../../ui/macros.py" line="156" />
        <source>Macros</source>
        <extracomment>The route a profile keyboard follows, as the macro target it corresponds to. Editor pages, one per step shape.</extracomment>
        <translation>Macros</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="146" />
        <source>Build a sequence once, then run it from any assigned trigger.</source>
        <translation>Build a sequence once, then run it from any assigned trigger.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="162" />
        <source>Macros of the active profile</source>
        <translation>Macros of the active profile</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="168" />
        <source>Macro name</source>
        <translation>Macro name</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="171" />
        <source>Name:</source>
        <translation>Name:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="174" />
        <source>Macro target</source>
        <translation>Macro target</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="176" />
        <source>Inherit from profile</source>
        <translation>Inherit from profile</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="179" />
        <source>Both</source>
        <translation>Both</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="183" />
        <source>Types on:</source>
        <translation>Types on:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="188" />
        <source>New macro</source>
        <translation>New macro</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="189" />
        <source>Add a macro to this profile</source>
        <translation>Add a macro to this profile</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="191" />
        <source>Delete macro</source>
        <translation>Delete macro</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="192" />
        <source>Delete the selected macro</source>
        <translation>Delete the selected macro</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="194" />
        <source>Test run...</source>
        <translation>Test run...</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="195" />
        <source>Run this macro on the device</source>
        <translation>Run this macro on the device</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="203" />
        <source>Steps</source>
        <translation>Steps</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="209" />
        <source>Steps of the selected macro</source>
        <translation>Steps of the selected macro</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="223" />
        <source>Step type</source>
        <translation>Step type</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="228" />
        <source>Add step</source>
        <translation>Add step</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="229" />
        <source>Add a step of this type</source>
        <translation>Add a step of this type</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="231" />
        <source>Delete step</source>
        <translation>Delete step</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="232" />
        <source>Delete the selected step</source>
        <translation>Delete the selected step</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="234" />
        <source>Up</source>
        <translation>Up</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="235" />
        <source>Move the selected step earlier</source>
        <translation>Move the selected step earlier</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="237" />
        <source>Down</source>
        <translation>Down</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="238" />
        <source>Move the selected step later</source>
        <translation>Move the selected step later</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="252" />
        <source>Why this step cannot be stored</source>
        <translation>Why this step cannot be stored</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="258" />
        <source>Apply to step</source>
        <translation>Apply to step</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="259" />
        <source>Store the edited step</source>
        <translation>Store the edited step</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="273" />
        <source>Key</source>
        <translation>Key</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="277" />
        <source>Key:</source>
        <translation>Key:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="282" />
        <source>{0} modifier</source>
        <translation>{0} modifier</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="289" />
        <source>Modifiers:</source>
        <translation>Modifiers:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="297" />
        <source>Consumer usage</source>
        <translation>Consumer usage</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="301" />
        <source>Usage:</source>
        <translation>Usage:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="309" />
        <source>Text to type</source>
        <translation>Text to type</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="320" />
        <source>Shortest delay</source>
        <translation>Shortest delay</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="324" />
        <source>Longest delay</source>
        <translation>Longest delay</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="328" />
        <source>From, ms:</source>
        <translation>From, ms:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="331" />
        <source>To, ms:</source>
        <translation>To, ms:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="340" />
        <source>Keyboard route</source>
        <translation>Keyboard route</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="345" />
        <location filename="../../ui/macros.py" line="359" />
        <source>Route:</source>
        <translation>Route:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="354" />
        <source>Mouse route command</source>
        <translation>Mouse route command</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="368" />
        <source>Profile to switch to</source>
        <translation>Profile to switch to</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="373" />
        <source>Profile:</source>
        <translation>Profile:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="527" />
        <source>This macro already holds {0} steps.</source>
        <translation>This macro already holds {0} steps.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="530" />
        <source>The shortest delay cannot exceed the longest.</source>
        <translation>The shortest delay cannot exceed the longest.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="540" />
        <source>A text step holds at most {0} characters; this one has {1}.</source>
        <translation>A text step holds at most {0} characters; this one has {1}.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="545" />
        <source>{0!r} at position {1} cannot be typed on the {2} layout.</source>
        <translation>{0!r} at position {1} cannot be typed on the {2} layout.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="658" />
        <source>Macro {0}</source>
        <translation>Macro {0}</translation>
    </message>
</context>
<context>
    <name>MainWindow</name>
    <message>
        <location filename="../../ui/main_window.py" line="197" />
        <source>Sections</source>
        <extracomment>How often the shell looks for a device that is not attached yet. Short enough that plugging a board in feels immediate, long enough that the retry costs nothing while the socket stays empty. Navigation rows, in the order the sections appear. Mouse buttons the attached device has actually reported. Set while a device-initiated read is in flight, so its answer - which arrives on ``operation_succeeded`` several chunks later - is only adopted when it is actually the read this window asked for. Set by a connect, cleared by the counter read it asks for. Which devices are on U1's own bus is a GET_DIAGNOSTICS answer, and the Mouse page cannot tell one device's press from another's without it - so the shell asks on every attach rather than waiting for someone to open Diagnostics and press Refresh. The project a write is currently sending, held from the moment it was compiled until the device confirms it. A write is chunks, then WRITE_COMMIT, then a read-back - every step a full event-loop turn, with the editor pages live throughout. What the board ends up holding is this project, not whatever is on screen when the last turn lands, so this is what the baseline becomes. Set once ``try_autoconnect`` has told the operator no device answered, so that message is said once rather than on every retry.</extracomment>
        <translation>Sections</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="212" />
        <source>Overview</source>
        <translation>Overview</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="213" />
        <source>Profiles</source>
        <translation>Profiles</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="214" />
        <source>Bindings</source>
        <translation>Bindings</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="215" />
        <source>Macros</source>
        <translation>Macros</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="216" />
        <source>Mouse</source>
        <translation>Mouse</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="217" />
        <source>Общий буфер</source>
        <translation>Shared clipboard</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="218" />
        <source>Diagnostics</source>
        <translation>Diagnostics</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="219" />
        <source>Settings</source>
        <translation>Settings</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="269" />
        <source>Load a copy...</source>
        <translation>Load a copy...</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="270" />
        <source>Load a configuration from a file</source>
        <translation>Load a configuration from a file</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="273" />
        <source>Save a copy...</source>
        <translation>Save a copy...</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="275" />
        <source>Save a copy of the configuration to a file</source>
        <translation>Save a copy of the configuration to a file</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="604" />
        <source>Save a copy</source>
        <translation>Save a copy</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="638" />
        <source>Load a copy</source>
        <translation>Load a copy</translation>
    </message>
    <message>
        <source>Open</source>
        <translation>Open</translation>
    </message>
    <message>
        <source>Open a project file</source>
        <translation>Open a project file</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="344" />
        <source>Problems that block a write</source>
        <translation>Problems that block a write</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="354" />
        <source>Transfer progress</source>
        <translation>Transfer progress</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="239" />
        <source>Ready</source>
        <translation>Ready</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="256" />
        <source>Profile:</source>
        <translation>Profile:</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="259" />
        <source>Active profile</source>
        <translation>Active profile</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="265" />
        <source>Device connection</source>
        <translation>Device connection</translation>
    </message>
    <message>
        <source>Connect</source>
        <translation>Connect</translation>
    </message>
    <message>
        <source>Connect or disconnect the device</source>
        <translation>Connect or disconnect the device</translation>
    </message>
    <message>
        <source>Save</source>
        <translation>Save</translation>
    </message>
    <message>
        <source>Save the project file</source>
        <translation>Save the project file</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="282" />
        <source>Write to device</source>
        <translation>Write to device</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="283" />
        <source>Write the configuration to the device</source>
        <translation>Write the configuration to the device</translation>
    </message>
    <message>
        <source>Local changes</source>
        <translation>Local changes</translation>
    </message>
    <message>
        <source>Project file</source>
        <translation>Project file</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="316" />
        <source>Configuration on the device</source>
        <translation>Configuration on the device</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="335" />
        <source>Fix these before writing to the device.</source>
        <translation>Fix these before writing to the device.</translation>
    </message>
    <message>
        <source>a new project</source>
        <translation>a new project</translation>
    </message>
    <message>
        <source>Unsaved work was found</source>
        <translation>Unsaved work was found</translation>
    </message>
    <message>
        <source>Duo Input closed with unsaved changes to {0} on {1}. Recover them?</source>
        <translation>Duo Input closed with unsaved changes to {0} on {1}. Recover them?</translation>
    </message>
    <message>
        <source>Untitled project</source>
        <translation>Untitled project</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="519" />
        <source>Device: {0}</source>
        <translation>Device: {0}</translation>
    </message>
    <message>
        <source>Disconnect</source>
        <translation>Disconnect</translation>
    </message>
    <message>
        <source>Local changes: unsaved</source>
        <translation>Local changes: unsaved</translation>
    </message>
    <message>
        <source>Local changes: none</source>
        <translation>Local changes: none</translation>
    </message>
    <message>
        <source>Project file: not created yet</source>
        <translation>Project file: not created yet</translation>
    </message>
    <message>
        <source>Project file: {0}</source>
        <translation>Project file: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="536" />
        <source>Written to device: no link</source>
        <translation>Written to device: no link</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="542" />
        <source>Written to device: matches the project</source>
        <translation>Written to device: matches the project</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="539" />
        <source>Written to device: differs from the project</source>
        <translation>Written to device: differs from the project</translation>
    </message>
    <message>
        <source>Save project</source>
        <translation>Save project</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="627" />
        <source>Save failed</source>
        <translation>Save failed</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="630" />
        <source>Project saved</source>
        <translation>Project saved</translation>
    </message>
    <message>
        <source>Open project</source>
        <translation>Open project</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="661" />
        <source>That project could not be opened: {0}</source>
        <translation>That project could not be opened: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="665" />
        <source>Project opened</source>
        <translation>Project opened</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="677" />
        <source>No Duo Input device was found</source>
        <translation>No Duo Input device was found</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="720" />
        <source>No device found. Load a copy from a file, or plug the device in.</source>
        <translation>No device found. Load a copy from a file, or plug the device in.</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="930" />
        <source>The device has a different configuration; your edits were kept.</source>
        <translation>The device has a different configuration; your edits were kept.</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="938" />
        <source>The device's configuration could not be read: {0}</source>
        <translation>The device's configuration could not be read: {0}</translation>
    </message>
    <message>
        <location filename="../../ui/main_window.py" line="942" />
        <source>Configuration read from the device</source>
        <translation>Configuration read from the device</translation>
    </message>
    <message>
        <source>Unsaved changes</source>
        <translation>Unsaved changes</translation>
    </message>
    <message>
        <source>The project has unsaved changes. Save them before closing?</source>
        <translation>The project has unsaved changes. Save them before closing?</translation>
    </message>
</context>
<context>
    <name>MouseSwitchPage</name>
    <message>
        <location filename="../../ui/mouse.py" line="103" />
        <source>Mouse</source>
        <extracomment>Every action this page can produce, in the order it offers them. A mouse button the device just reported, so the shell can remember it.</extracomment>
        <translation>Mouse</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="105" />
        <source>Choose the key or mouse button that sends the pointer to PC1 or PC2.</source>
        <translation>Choose the key or mouse button that sends the pointer to PC1 or PC2.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="132" />
        <source>Both devices at once</source>
        <translation>Both devices at once</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="138" />
        <location filename="../../ui/mouse.py" line="141" />
        <source>Switch the keyboard and the mouse together</source>
        <translation>Switch the keyboard and the mouse together</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="150" />
        <source>Switching either the keyboard or the mouse sends both devices to the same computer. This setting applies to every profile.</source>
        <translation>Switching either the keyboard or the mouse sends both devices to the same computer. This setting applies to every profile.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="168" />
        <source>Switch the mouse</source>
        <translation>Switch the mouse</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="175" />
        <source>Trigger kind</source>
        <translation>Trigger kind</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="176" />
        <source>Choose...</source>
        <translation>Choose...</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="177" />
        <location filename="../../ui/mouse.py" line="185" />
        <location filename="../../ui/mouse.py" line="533" />
        <source>Keyboard key</source>
        <translation>Keyboard key</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="178" />
        <location filename="../../ui/mouse.py" line="201" />
        <source>Mouse button</source>
        <translation>Mouse button</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="181" />
        <source>1. What do you press?</source>
        <translation>1. What do you press?</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="189" />
        <source>Key:</source>
        <translation>Key:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="207" />
        <source>Detect button or key</source>
        <translation>Detect button or key</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="440" />
        <source>Press the button or key on the mouse you want to use.</source>
        <translation>Press the button or key on the mouse you want to use.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="209" />
        <source>Detect a button or key on the mouse</source>
        <translation>Detect a button or key on the mouse</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="179" />
        <source>Consumer control</source>
        <translation>Consumer control</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="195" />
        <location filename="../../ui/mouse.py" line="536" />
        <source>Consumer usage</source>
        <translation>Consumer usage</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="197" />
        <source>Consumer usage:</source>
        <translation>Consumer usage:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="215" />
        <source>Button:</source>
        <translation>Button:</translation>
    </message>
    <message>
        <source>Selected mouse control</source>
        <translation>Selected mouse control</translation>
    </message>
    <message>
        <source>Edit manually</source>
        <translation>Edit manually</translation>
    </message>
    <message>
        <source>Edit the detected trigger manually</source>
        <translation>Edit the detected trigger manually</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="222" />
        <location filename="../../ui/mouse.py" line="544" />
        <source>{0} modifier</source>
        <translation>{0} modifier</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="229" />
        <source>Modifiers:</source>
        <translation>Modifiers:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="233" />
        <source>What the trigger does</source>
        <translation>What the trigger does</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="234" />
        <source>Toggle between PC1 and PC2</source>
        <translation>Toggle between PC1 and PC2</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="235" />
        <source>Always PC1</source>
        <translation>Always PC1</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="236" />
        <source>Always PC2</source>
        <translation>Always PC2</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="238" />
        <source>2. What should it do?</source>
        <translation>2. What should it do?</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="241" />
        <source>Binding mode</source>
        <translation>Binding mode</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="242" />
        <source>Replace the key</source>
        <translation>Replace the key</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="243" />
        <source>Add to the key</source>
        <translation>Add to the key</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="244" />
        <source>Mode:</source>
        <translation>Mode:</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="248" />
        <source>Mouse switching warnings</source>
        <translation>Mouse switching warnings</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="255" />
        <source>Bind</source>
        <translation>Bind</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="256" />
        <source>Bind this trigger to the mouse route</source>
        <translation>Bind this trigger to the mouse route</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="265" />
        <source>Mouse switching in this profile</source>
        <translation>Mouse switching in this profile</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="268" />
        <source>Existing mouse switch bindings</source>
        <translation>Existing mouse switch bindings</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="459" />
        <location filename="../../ui/mouse.py" line="494" />
        <source>Side button</source>
        <translation>Side button</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="482" />
        <source>Left button</source>
        <translation>Left button</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="483" />
        <source>Right button</source>
        <translation>Right button</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="484" />
        <source>Middle button (wheel)</source>
        <translation>Middle button (wheel)</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="485" />
        <source>Side button 1</source>
        <translation>Side button 1</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="486" />
        <source>Side button 2</source>
        <translation>Side button 2</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="488" />
        <source>Button {0}</source>
        <translation>Button {0}</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="531" />
        <source>Not applicable</source>
        <translation>Not applicable</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="472" />
        <source>Toggle</source>
        <translation>Toggle</translation>
    </message>
    <message>
        <source>Additional mouse button</source>
        <translation>Additional mouse button</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="579" />
        <location filename="../../ui/mouse.py" line="584" />
        <source>Choose what you press first.</source>
        <translation>Choose what you press first.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="581" />
        <source>No mouse is attached, so no mouse button can be bound.</source>
        <translation>No mouse is attached, so no mouse button can be bound.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="588" />
        <source>{0} is already bound in this profile.</source>
        <translation>{0} is already bound in this profile.</translation>
    </message>
    <message>
        <location filename="../../ui/mouse.py" line="605" />
        <source>{0} is bound here but the attached mouse has not reported it.</source>
        <translation>{0} is bound here but the attached mouse has not reported it.</translation>
    </message>
</context>
<context>
    <name>OverviewPage</name>
    <message>
        <location filename="../../ui/overview.py" line="87" />
        <source>Overview</source>
        <extracomment>Shown wherever the device or the project genuinely does not supply a value. Fields whose value is a number, a version or a protocol identifier. They are set in the fixed-width face so a column of them can be read downwards. Fields holding a SHA-256 digest: sixty-four characters with nowhere to wrap.</extracomment>
        <translation>Overview</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="89" />
        <source>What the device reports, and what this project would send it.</source>
        <translation>What the device reports, and what this project would send it.</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="104" />
        <source>Device</source>
        <translation>Device</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="106" />
        <source>U1 protocol version</source>
        <translation>U1 protocol version</translation>
    </message>
    <message>
        <source>U1 firmware version</source>
        <translation>U1 firmware version</translation>
    </message>
    <message>
        <source>U2 firmware version</source>
        <translation>U2 firmware version</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="107" />
        <source>Configuration generation</source>
        <translation>Configuration generation</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="108" />
        <source>Profile active on device</source>
        <translation>Profile active on device</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="116" />
        <source>Peripherals</source>
        <translation>Peripherals</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="118" />
        <source>Keyboard HID</source>
        <translation>Keyboard HID</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="119" />
        <source>Mouse HID</source>
        <translation>Mouse HID</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="120" />
        <source>Consumer HID</source>
        <translation>Consumer HID</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="128" />
        <source>Active profile and routes</source>
        <translation>Active profile and routes</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="130" />
        <source>Active profile</source>
        <translation>Active profile</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="131" />
        <source>Keyboard route</source>
        <translation>Keyboard route</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="132" />
        <source>Mouse route</source>
        <translation>Mouse route</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="133" />
        <source>Text layout</source>
        <translation>Text layout</translation>
    </message>
    <message>
        <source>Memory usage</source>
        <translation>Memory usage</translation>
    </message>
    <message>
        <source>Project package</source>
        <translation>Project package</translation>
    </message>
    <message>
        <source>Package on device</source>
        <translation>Package on device</translation>
    </message>
    <message>
        <source>Configuration state</source>
        <translation>Configuration state</translation>
    </message>
    <message>
        <source>Saved file</source>
        <translation>Saved file</translation>
    </message>
    <message>
        <source>Compiled project</source>
        <translation>Compiled project</translation>
    </message>
    <message>
        <source>On device</source>
        <translation>On device</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="140" />
        <source>Recent events</source>
        <translation>Recent events</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="144" />
        <source>Recent device events</source>
        <translation>Recent device events</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="253" />
        <source>advertised</source>
        <translation>advertised</translation>
    </message>
    <message>
        <location filename="../../ui/overview.py" line="255" />
        <source>not advertised</source>
        <translation>not advertised</translation>
    </message>
    <message>
        <source>{0} of {1} bytes ({2:.1f}%)</source>
        <translation>{0} of {1} bytes ({2:.1f}%)</translation>
    </message>
</context>
<context>
    <name>PairingDialog</name>
    <message>
        <location filename="../../app.py" line="177" />
        <source>Подтвердите связывание</source>
        <extracomment>Console script target declared in ``pyproject.toml``. Аргумент командной строки, которым автозапуск просит не показывать окно - см. persistence/autostart.py и §4 спецификации. Ключи, которыми установщик и деинсталлятор ставят и убирают правила брандмауэра, - см. persistence/firewall.py. Человек ответил "Позже" (или отказал в UAC): окно о брандмауэре больше не всплывает, остаётся строка с кнопкой на странице общего буфера.</extracomment>
        <translation>Confirm pairing</translation>
    </message>
    <message>
        <location filename="../../app.py" line="178" />
        <source>Компьютер «{0}» показывает тот же код?

Код: {1}</source>
        <translation>Does “{0}” show the same code?

Code: {1}</translation>
    </message>
    <message>
        <location filename="../../app.py" line="188" />
        <source>Связать</source>
        <translation>Pair</translation>
    </message>
    <message>
        <location filename="../../app.py" line="192" />
        <source>Отказать</source>
        <translation>Reject</translation>
    </message>
</context>
<context>
    <name>ProfilesPage</name>
    <message>
        <location filename="../../ui/profiles.py" line="77" />
        <source>Profiles</source>
        <extracomment>Side of the square that shows a profile's colour, in logical pixels. Longest profile name the binary format accepts, mirrored from validation. Item data role carrying the profile ID of a slot row.</extracomment>
        <translation>Profiles</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="79" />
        <source>Eight slots the device switches between. One of them is the one it starts in.</source>
        <translation>Eight slots the device switches between. One of them is the one it starts in.</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="92" />
        <source>Profile slots</source>
        <translation>Profile slots</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="110" />
        <source>Profile</source>
        <translation>Profile</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="119" />
        <source>Profile name</source>
        <translation>Profile name</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="122" />
        <source>Name:</source>
        <translation>Name:</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="125" />
        <location filename="../../ui/profiles.py" line="316" />
        <source>Profile colour</source>
        <translation>Profile colour</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="129" />
        <source>Colour:</source>
        <translation>Colour:</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="132" />
        <source>Profile routes</source>
        <translation>Profile routes</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="135" />
        <source>Routes:</source>
        <translation>Routes:</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="141" />
        <source>Copy destination</source>
        <translation>Copy destination</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="142" />
        <source>Copy into</source>
        <translation>Copy into</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="143" />
        <source>Copy this profile into another slot</source>
        <translation>Copy this profile into another slot</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="151" />
        <source>Make active</source>
        <translation>Make active</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="152" />
        <source>Start the device in this profile</source>
        <translation>Start the device in this profile</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="154" />
        <source>Clear</source>
        <translation>Clear</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="155" />
        <source>Reset this profile slot</source>
        <translation>Reset this profile slot</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="242" />
        <source>{0} - {1} ({2} bindings, {3} macros){4}</source>
        <translation>{0} - {1} ({2} bindings, {3} macros){4}</translation>
    </message>
    <message>
        <location filename="../../ui/profiles.py" line="269" />
        <source>{0} — brought together on activation</source>
        <translation>{0} — brought together on activation</translation>
    </message>
</context>
<context>
    <name>SettingsPage</name>
    <message>
        <location filename="../../ui/settings.py" line="72" />
        <source>Settings</source>
        <extracomment>Preferences are short answers; a full-window text field only looks empty. How each shipped language names itself, in that language. Log levels the operator may choose, quietest last.</extracomment>
        <translation>Settings</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="73" />
        <source>Preferences that belong to you, not to the project.</source>
        <translation>Preferences that belong to you, not to the project.</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="86" />
        <source>Interface</source>
        <translation>Interface</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="92" />
        <source>Interface language</source>
        <translation>Interface language</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="96" />
        <source>Language:</source>
        <translation>Language:</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="99" />
        <source>What happens after a language change</source>
        <translation>What happens after a language change</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="106" />
        <source>Projects</source>
        <translation>Projects</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="115" />
        <location filename="../../ui/settings.py" line="215" />
        <source>Default project folder</source>
        <translation>Default project folder</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="117" />
        <source>Browse...</source>
        <translation>Browse...</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="118" />
        <source>Choose the default project folder</source>
        <translation>Choose the default project folder</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="124" />
        <source>Folder:</source>
        <translation>Folder:</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="128" />
        <source>Logging</source>
        <translation>Logging</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="134" />
        <source>How much is written to the log</source>
        <translation>How much is written to the log</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="138" />
        <source>Level:</source>
        <translation>Level:</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="141" />
        <source>Macro text is never written to the log, at any level.</source>
        <translation>Macro text is never written to the log, at any level.</translation>
    </message>
    <message>
        <location filename="../../ui/settings.py" line="205" />
        <source>The interface language changes the next time Duo Input starts.</source>
        <translation>The interface language changes the next time Duo Input starts.</translation>
    </message>
</context>
<context>
    <name>TestMacroDialog</name>
    <message>
        <location filename="../../ui/macros.py" line="721" />
        <source>Test run</source>
        <translation>Test run</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="729" />
        <source>This runs macro #{0} on the device. It generates real key presses on the computer it is routed to.</source>
        <translation>This runs macro #{0} on the device. It generates real key presses on the computer it is routed to.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="738" />
        <source>Target computer</source>
        <translation>Target computer</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="739" />
        <source>Choose...</source>
        <translation>Choose...</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="743" />
        <source>Runs on:</source>
        <translation>Runs on:</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="747" />
        <source>I understand this types on a real computer.</source>
        <translation>I understand this types on a real computer.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="749" />
        <source>Confirm the test run</source>
        <translation>Confirm the test run</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="754" />
        <source>Why this macro cannot be tested</source>
        <translation>Why this macro cannot be tested</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="759" />
        <source>Run</source>
        <translation>Run</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="760" />
        <source>Run the macro now</source>
        <translation>Run the macro now</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="763" />
        <source>Stop everything and release every key</source>
        <translation>Stop everything and release every key</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="766" />
        <source>Close</source>
        <translation>Close</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="788" />
        <source>The device is not connected.</source>
        <translation>The device is not connected.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="791" />
        <source>The device is not holding this project. Write it first, so the macro that runs is the macro on screen.</source>
        <translation>The device is not holding this project. Write it first, so the macro that runs is the macro on screen.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="796" />
        <source>Choose which computer the macro should type on.</source>
        <translation>Choose which computer the macro should type on.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="800" />
        <source>This macro is configured to type on {0}. Change its target first.</source>
        <translation>This macro is configured to type on {0}. Change its target first.</translation>
    </message>
    <message>
        <location filename="../../ui/macros.py" line="803" />
        <source>Confirm that you expect real key presses.</source>
        <translation>Confirm that you expect real key presses.</translation>
    </message>
</context>
<context>
    <name>TrayIcon</name>
    <message>
        <location filename="../../ui/tray.py" line="15" />
        <source>Второй компьютер на связи</source>
        <translation>Second computer connected</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="16" />
        <source>Нет связи со вторым компьютером</source>
        <translation>No connection to the second computer</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="17" />
        <source>Компьютеры не связаны</source>
        <translation>Computers not paired</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="18" />
        <source>Поиск второго компьютера</source>
        <translation>Searching for the second computer</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="21" />
        <source>Windows не разрешила подключение — проверьте брандмауэр</source>
        <translation>Windows blocked the connection - check the firewall</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="26" />
        <source>Обновите вторую машину — версии протокола различаются</source>
        <translation>Update the other computer - protocol versions differ</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="47" />
        <source>Открыть Duo Input</source>
        <translation>Open Duo Input</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="57" />
        <source>Общий буфер обмена</source>
        <translation>Shared clipboard</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="62" />
        <source>Передача файлов</source>
        <translation>File transfer</translation>
    </message>
    <message>
        <location filename="../../ui/tray.py" line="69" />
        <source>Выход</source>
        <translation>Exit</translation>
    </message>
</context>
<context>
    <name>_ClipboardRuntime</name>
    <message>
        <location filename="../../app.py" line="512" />
        <source>Брандмауэр Windows</source>
        <extracomment>Результаты фоновой работы с брандмауэром - доставляются в поток интерфейса очередью Qt, потому что излучаются из другого потока. Окно о брандмауэре уже показывали в этом запуске - выключение и включение общего буфера не повод спрашивать снова. The macOS receiver, when the platform branch of ``_start_files`` built one - ``None`` on win32, where the receiver role is played by ``self.transfer`` itself (driven by Explorer through the callback gateway) rather than by a standalone object.</extracomment>
        <translation>Windows Firewall</translation>
    </message>
    <message>
        <location filename="../../app.py" line="516" />
        <source>Чтобы второй компьютер мог подключиться, Windows должна разрешить Duo Input входящие соединения в локальной сети.</source>
        <translation>For the other computer to be able to connect, Windows must allow Duo Input incoming connections on the local network.</translation>
    </message>
    <message>
        <location filename="../../app.py" line="520" />
        <source>Разрешить</source>
        <translation>Allow</translation>
    </message>
    <message>
        <location filename="../../app.py" line="521" />
        <source>Позже</source>
        <translation>Later</translation>
    </message>
    <message>
        <location filename="../../app.py" line="844" />
        <source>Входящие файлы</source>
        <translation>Incoming files</translation>
    </message>
    <message>
        <location filename="../../app.py" line="847" />
        <source>Другой компьютер хочет передать {0} объект(ов) ({1:.1f} МБ).</source>
        <translation>The other computer wants to send {0} item(s) ({1:.1f} MB).</translation>
    </message>
</context>
</TS>