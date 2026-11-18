// meet-audiotap — помощник Meet для macOS (экспериментально).
//
// На Windows системный звук пишется loopback'ом WASAPI, а «кто держит
// микрофон» видно в реестре. На macOS обоих сигналов у Python нет, поэтому
// они здесь — в маленькой программе без зависимостей, которую резидент
// запускает подпроцессом (`meet/audiotap.py`, `meet/mac_audio.py`):
//
//   meet-audiotap --stream [--rate 48000] [--channels 1] [--exclude-pid N]
//       ScreenCaptureKit (macOS 13+), только звук; --exclude-pid — без звука
//       приложения с этим процессом (оболочка Meet: плеер записи, 0.5). Первая строка stdout —
//       рукопожатие JSON {"meet_audiotap": 1, "rate", "channels", "format":
//       "s16le"}, дальше — сырой PCM s16le, пока не закрыт stdin или не
//       пришёл SIGTERM/SIGINT. Нужно разрешение «Запись экрана». Поток не
//       пустеет: если ScreenCaptureKit молчит дольше 0,5 с, помощник сам
//       дописывает тишину по часам — резидент считает помощника зависшим,
//       только когда байтов нет совсем. Ошибки буферов — в stderr не чаще
//       раза в 5 с (со счётчиком пропущенных). Фильтр захвата — по основному
//       дисплею (CGMainDisplayID); сменился основной дисплей (внешний монитор
//       отключили, закрыли крышку) или ScreenCaptureKit остановил поток —
//       захват пересобирается внутри процесса, без нового рукопожатия (пока его
//       нет, дописывается тишина). Выход с кодом 77 — только если отозвали
//       разрешение.
//   meet-audiotap --stream ... --source process_tap
//       Core Audio process tap (macOS 14.4+; по выбору в настройках «Звук»):
//       глобальный перехват без процессов Meet (дерево --exclude-pid и службы,
//       за которые оно отвечает) в частном aggregate-устройстве на встроенном
//       выходе. То же рукопожатие и тот же PCM; разрешение — «Запись системного
//       звука», без значка записи экрана. Без разрешения буферы приходят
//       нулями — это не ошибка (нули считает резидент); перехват не
//       запустился — код 70.
//   meet-audiotap --check-tap [--seconds 5] [--exclude-pid N]
//       Проверка process tap при включении: столько секунд записи и одна
//       строка JSON {"meet_audiotap": 1, "check": "process_tap", "frames",
//       "peak"} (пик по модулю s16). Отказ CoreAudio сразу — код 77.
//   meet-audiotap --mic-users
//       Одна строка JSON: процессы, которые сейчас слушают микрофон или
//       играют звук (CoreAudio, macOS 14+; раньше — "supported": false), у
//       каждого — "input_devices": UID устройств ввода, которые он слушает.
//   meet-audiotap --watch-devices
//       Пока открыт stdin: строка JSON сразу и на каждую перемену —
//       {"meet_audiotap": 1, "watch": "start" | "default" | "devices" | "alive",
//       "default_input": {"uid", "name", "transport", "alive"} | null,
//       "inputs": [...]}: устройство ввода по умолчанию и все устройства ввода,
//       тип подключения ("builtin", "bluetooth", "usb"…). Повод — смена
//       устройства по умолчанию, списка устройств или «живости» устройства по
//       умолчанию (AudioObjectAddPropertyListenerBlock); одинаковые строки
//       подряд не повторяются.
//   meet-audiotap --self-test
//       Версия и возможности одной строкой JSON; разрешений не требует (CI).
//   meet-audiotap --preflight
//       Дано ли разрешение «Запись экрана»: код 0 — да, 77 — нет. Без захвата
//       и без системного запроса — резидент спрашивает раз в 15 с, пока
//       запись идёт без звука собеседников.
//   meet-audiotap --mic-status
//       Одна строка JSON {"microphone": "granted" | "denied" | "notDetermined"
//       | "restricted"} — разрешение «Микрофон», без системного запроса.
//   meet-audiotap --request-mic
//       Системный запрос «Микрофон» (уже решённое macOS не переспрашивает);
//       ждёт ответа человека и печатает ту же строку, что --mic-status.
//   meet-audiotap --request-screen
//       Системный запрос «Запись экрана»: {"screen": "granted" | "denied"}
//       (данное разрешение macOS применит после перезапуска Meet).
//   Помощника запускает цепочка Meet.app → резидент, поэтому запросы macOS
//   относит к Meet (текст — NSMicrophoneUsageDescription приложения).
//
// Коды выхода: 0 — штатно, 64 — неверные аргументы, 69 — macOS старше 13 (у
// process tap — старше 14.4),
// 70 — сбой захвата, 77 — нет разрешения «Запись экрана» (у --check-tap —
// отказ CoreAudio в записи системного звука).
//
// Сборка (CI, scripts/build_release_macos.sh):
//   swiftc -O -swift-version 5 -target arm64-apple-macos13.0 \
//     -o meet-audiotap mac/audiotap/main.swift

import AVFoundation
import CoreAudio
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

let protocolVersion = 1
let helperVersion = "0.5.0"

let exitOK: Int32 = 0
let exitUsage: Int32 = 64
let exitUnsupported: Int32 = 69
let exitFailed: Int32 = 70
let exitPermission: Int32 = 77

/// SCStreamError.userDeclined: человек не дал разрешение «Запись экрана».
let userDeclinedCode = -3801

struct NoDisplayError: LocalizedError {
    var errorDescription: String? { "нет дисплея для захвата" }
}

func printError(_ text: String) {
    FileHandle.standardError.write((text + "\n").data(using: .utf8)!)
}

func printJSON(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
          let line = String(data: data, encoding: .utf8)
    else {
        printError("не удалось собрать JSON")
        exit(exitFailed)
    }
    print(line)
    fflush(stdout)
}

/// Записать всё в stdout. Читатель ушёл (EPIPE) — запись больше никому не
/// нужна: выходим штатно.
func writeAll(_ bytes: UnsafeRawBufferPointer) {
    guard var pointer = bytes.baseAddress else { return }
    var left = bytes.count
    while left > 0 {
        let written = Darwin.write(STDOUT_FILENO, pointer, left)
        if written < 0 {
            if errno == EINTR { continue }
            exit(exitOK)
        }
        left -= written
        pointer = pointer.advanced(by: written)
    }
}

// MARK: - --preflight

func preflight() -> Never {
    exit(CGPreflightScreenCaptureAccess() ? exitOK : exitPermission)
}

// MARK: - --mic-status, --request-mic, --request-screen

func microphoneState() -> String {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: return "granted"
    case .denied: return "denied"
    case .notDetermined: return "notDetermined"
    case .restricted: return "restricted"
    @unknown default: return "unknown"
    }
}

func micStatus() -> Never {
    printJSON(["microphone": microphoneState()])
    exit(exitOK)
}

/// Ответ приходит на произвольной очереди — печатаем и выходим там же, а
/// главная очередь ждёт в dispatchMain.
func requestMic() -> Never {
    AVCaptureDevice.requestAccess(for: .audio) { _ in
        printJSON(["microphone": microphoneState()])
        exit(exitOK)
    }
    dispatchMain()
}

func requestScreen() -> Never {
    printJSON(["screen": CGRequestScreenCaptureAccess() ? "granted" : "denied"])
    exit(exitOK)
}

// MARK: - --self-test

func selfTest() -> Never {
    let os = ProcessInfo.processInfo.operatingSystemVersion
    var stream = false
    var micUsers = false
    var processTap = false
    if #available(macOS 13.0, *) { stream = true }
    if #available(macOS 14.0, *) { micUsers = true }
    if #available(macOS 14.4, *) { processTap = true }
    printJSON([
        "meet_audiotap": protocolVersion,
        "version": helperVersion,
        "macos": "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
        "stream": stream,
        "mic_users": micUsers,
        "exclude_pid": true,
        "permissions": true,
        "watch_devices": true,
        "process_tap": processTap,
        "format": "s16le",
    ])
    exit(exitOK)
}

// MARK: - --mic-users

func processPath(_ pid: pid_t) -> String {
    var buffer = [CChar](repeating: 0, count: 4096)
    let length = proc_pidpath(pid, &buffer, UInt32(buffer.count))
    return length > 0 ? String(cString: buffer) : ""
}

/// Имя процесса, как его видит psutil: последний компонент пути к программе.
func processName(_ pid: pid_t) -> String {
    let path = processPath(pid)
    if !path.isEmpty {
        return (path as NSString).lastPathComponent
    }
    var buffer = [CChar](repeating: 0, count: 256)
    let length = proc_name(pid, &buffer, UInt32(buffer.count))
    return length > 0 ? String(cString: buffer) : ""
}

func readUInt32(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
    var address = AudioObjectPropertyAddress(
        mSelector: selector,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var value: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    return status == noErr ? value : nil
}

@available(macOS 14.0, *)
func readPID(_ object: AudioObjectID) -> pid_t? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioProcessPropertyPID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var value: pid_t = 0
    var size = UInt32(MemoryLayout<pid_t>.size)
    let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    return status == noErr ? value : nil
}

/// Свойство-строка объекта CoreAudio (UID, имя, bundle id процесса); нет — "".
func readString(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var address = AudioObjectPropertyAddress(
        mSelector: selector,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    guard status == noErr, let text = value?.takeRetainedValue() else { return "" }
    return text as String
}

/// Массив объектов CoreAudio в свойстве (устройства системы, процессы,
/// устройства процесса); не прочитался — nil.
func readObjects(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
                 scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> [AudioObjectID]? {
    var address = AudioObjectPropertyAddress(
        mSelector: selector,
        mScope: scope,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(object, &address, 0, nil, &size) == noErr else {
        return nil
    }
    let count = Int(size) / MemoryLayout<AudioObjectID>.size
    if count == 0 { return [] }
    var objects = [AudioObjectID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, &objects) == noErr else {
        return nil
    }
    return Array(objects.prefix(Int(size) / MemoryLayout<AudioObjectID>.size))
}

@available(macOS 14.0, *)
func audioProcesses() -> [[String: Any]]? {
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard let objects = readObjects(system, kAudioHardwarePropertyProcessObjectList) else {
        return nil
    }
    var found: [[String: Any]] = []
    for object in objects {
        guard let pid = readPID(object) else { continue }
        let input = (readUInt32(object, kAudioProcessPropertyIsRunningInput) ?? 0) != 0
        let output = (readUInt32(object, kAudioProcessPropertyIsRunningOutput) ?? 0) != 0
        // Какие устройства ввода процесс слушает: резидент так отличает звонок
        // со встроенного микрофона от звонка с микрофона Bluetooth-наушников.
        let inputDevices = (readObjects(object, kAudioProcessPropertyDevices,
                                        scope: kAudioObjectPropertyScopeInput) ?? [])
            .map { readString($0, kAudioDevicePropertyDeviceUID) }
            .filter { !$0.isEmpty }
        found.append([
            "pid": Int(pid),
            "name": processName(pid),
            "bundle": readString(object, kAudioProcessPropertyBundleID),
            "input": input,
            "output": output,
            "input_devices": inputDevices,
        ])
    }
    return found
}

func micUsers() -> Never {
    if #available(macOS 14.0, *) {
        guard let processes = audioProcesses() else {
            printJSON(["meet_audiotap": protocolVersion, "supported": false,
                       "note": "CoreAudio не ответил", "processes": [Any]()])
            exit(exitOK)
        }
        printJSON(["meet_audiotap": protocolVersion, "supported": true, "processes": processes])
    } else {
        printJSON(["meet_audiotap": protocolVersion, "supported": false,
                   "note": "нужна macOS 14 или новее", "processes": [Any]()])
    }
    exit(exitOK)
}

// MARK: - --watch-devices

/// Тип подключения устройства (kAudioDevicePropertyTransportType) — словом.
func transportName(_ device: AudioObjectID) -> String {
    switch readUInt32(device, kAudioDevicePropertyTransportType) ?? kAudioDeviceTransportTypeUnknown {
    case kAudioDeviceTransportTypeBuiltIn: return "builtin"
    case kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE: return "bluetooth"
    case kAudioDeviceTransportTypeUSB: return "usb"
    case kAudioDeviceTransportTypeVirtual: return "virtual"
    case kAudioDeviceTransportTypeAggregate: return "aggregate"
    case kAudioDeviceTransportTypeAirPlay: return "airplay"
    case kAudioDeviceTransportTypeHDMI, kAudioDeviceTransportTypeDisplayPort: return "display"
    case kAudioDeviceTransportTypeThunderbolt: return "thunderbolt"
    case kAudioDeviceTransportTypeUnknown: return "unknown"
    default: return "other"
    }
}

/// Есть ли у устройства потоки ввода (микрофон, BlackHole), а не только вывод.
func hasInput(_ device: AudioObjectID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreams,
        mScope: kAudioObjectPropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    return AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr && size > 0
}

func deviceInfo(_ device: AudioObjectID) -> [String: Any] {
    return [
        "uid": readString(device, kAudioDevicePropertyDeviceUID),
        "name": readString(device, kAudioObjectPropertyName),
        "transport": transportName(device),
        "alive": (readUInt32(device, kAudioDevicePropertyDeviceIsAlive) ?? 0) != 0,
    ]
}

func defaultInputDevice() -> AudioObjectID? {
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard let device = readUInt32(system, kAudioHardwarePropertyDefaultInputDevice),
          device != kAudioObjectUnknown
    else { return nil }
    return device
}

/// Слежение за устройствами ввода: все обратные вызовы CoreAudio и печать —
/// на одной последовательной очереди.
final class DeviceWatch {
    private let queue = DispatchQueue(label: "meet-audiotap.devices")
    private let system = AudioObjectID(kAudioObjectSystemObject)
    /// Устройство по умолчанию, чью «живость» слушаем, и его подписка.
    private var aliveDevice = AudioObjectID(kAudioObjectUnknown)
    private var aliveListener: AudioObjectPropertyListenerBlock?
    private var lastState: NSDictionary?

    func start() {
        queue.async {
            self.listen(kAudioHardwarePropertyDefaultInputDevice, "default")
            self.listen(kAudioHardwarePropertyDevices, "devices")
            self.followDefault()
            self.report("start")
        }
    }

    private func listen(_ selector: AudioObjectPropertySelector, _ reason: String) {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        let status = AudioObjectAddPropertyListenerBlock(system, &address, queue) { [weak self] _, _ in
            self?.changed(reason)
        }
        if status != noErr {
            printError("CoreAudio: подписка на устройства не удалась (\(status))")
        }
    }

    private func changed(_ reason: String) {
        followDefault()
        report(reason)
    }

    /// Слушать «живость» устройства ввода по умолчанию; сменилось — переподписаться.
    private func followDefault() {
        let current = defaultInputDevice() ?? AudioObjectID(kAudioObjectUnknown)
        guard current != aliveDevice else { return }
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceIsAlive,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        if let block = aliveListener {
            _ = AudioObjectRemovePropertyListenerBlock(aliveDevice, &address, queue, block)
        }
        aliveDevice = current
        aliveListener = nil
        guard current != kAudioObjectUnknown else { return }
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.changed("alive") }
        if AudioObjectAddPropertyListenerBlock(current, &address, queue, block) == noErr {
            aliveListener = block
        }
    }

    /// Строка о текущих устройствах; такая же, как прошлая, — не печатается.
    private func report(_ reason: String) {
        let inputs = (readObjects(system, kAudioHardwarePropertyDevices) ?? [])
            .filter(hasInput).map(deviceInfo)
        var state: [String: Any] = ["inputs": inputs]
        if let device = defaultInputDevice() {
            state["default_input"] = deviceInfo(device)
        } else {
            state["default_input"] = NSNull()
        }
        let snapshot = NSDictionary(dictionary: state)
        if let last = lastState, last.isEqual(snapshot) { return }
        lastState = snapshot
        state["meet_audiotap"] = protocolVersion
        state["watch"] = reason
        printJSON(state)
    }
}

var watchHolder: DeviceWatch?

func watchDevices() -> Never {
    signal(SIGPIPE, SIG_IGN)
    // Уведомления HAL — на его собственном потоке, а не на главном цикле
    // событий (у программы командной строки он не крутится).
    var runLoop: CFRunLoop? = nil
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyRunLoop,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    _ = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil,
                                   UInt32(MemoryLayout<CFRunLoop?>.size), &runLoop)
    let watch = DeviceWatch()
    watchHolder = watch
    watch.start()
    // Конец stdin — запись кончилась (или резидент умер): выходим.
    Thread.detachNewThread {
        var byte: UInt8 = 0
        while Darwin.read(STDIN_FILENO, &byte, 1) > 0 {}
        exit(exitOK)
    }
    dispatchMain()
}

// MARK: - --stream

/// Рукопожатие `--stream` — у обоих источников одно: первая строка stdout,
/// дальше — PCM.
func printHandshake(rate: Int, channels: Int) {
    printJSON(["meet_audiotap": protocolVersion, "rate": rate, "channels": channels,
               "format": "s16le"])
}

func int16Sample(_ value: Float32) -> Int16 {
    let clipped = max(-1, min(1, value))
    return Int16(clipped * 32767).littleEndian
}

/// Float32 (по буферу на канал или чередованием) → s16le с нужным числом
/// каналов: лишние каналы усредняются в моно. Общая для ScreenCaptureKit и
/// process tap — PCM у обоих источников один.
func appendPCM16(_ list: UnsafeMutableAudioBufferListPointer, channels: Int,
                 into samples: inout [Int16]) {
    var planes: [UnsafeBufferPointer<Float32>] = []
    var interleaved = 1
    for buffer in list {
        guard let data = buffer.mData else { continue }
        let count = Int(buffer.mDataByteSize) / MemoryLayout<Float32>.size
        planes.append(UnsafeBufferPointer(
            start: data.assumingMemoryBound(to: Float32.self), count: count))
        interleaved = max(1, Int(buffer.mNumberChannels))
    }
    guard let first = planes.first else { return }
    let sourceChannels = planes.count > 1 ? planes.count : interleaved
    let frames = planes.count > 1 ? first.count : first.count / interleaved
    if frames == 0 { return }
    func sample(_ frame: Int, _ channel: Int) -> Float32 {
        if planes.count > 1 {
            let plane = planes[min(channel, planes.count - 1)]
            return frame < plane.count ? plane[frame] : 0
        }
        return first[frame * interleaved + min(channel, interleaved - 1)]
    }
    samples.reserveCapacity(samples.count + frames * channels)
    for frame in 0..<frames {
        if channels == 1 {
            var sum: Float32 = 0
            for channel in 0..<sourceChannels { sum += sample(frame, channel) }
            samples.append(int16Sample(sum / Float32(sourceChannels)))
        } else {
            for channel in 0..<channels { samples.append(int16Sample(sample(frame, channel))) }
        }
    }
}

@available(macOS 13.0, *)
final class AudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
    let rate: Int
    let channels: Int
    /// Процесс приложения, чей звук не писать (0 — писать всё).
    let excludePid: Int
    var stream: SCStream?
    /// Дисплей, по которому построен фильтр захвата.
    private var displayID: CGDirectDisplayID = 0
    /// Пересборка захвата идёт / после неё нужна ещё одна; остановка начата.
    /// Всё это — на главной очереди.
    private var rebuilding = false
    private var rebuildAgain = false
    private var stopping = false
    private var displayGeneration = 0
    /// Пауза перед повтором неудавшейся пересборки: 0,5 → 1 → 2 … 15 с.
    private var retryDelay = 0.5
    private let retryMax = 15.0
    private let screenQueue = DispatchQueue(label: "meet-audiotap.screen")
    private var samples: [Int16] = []
    /// Очередь звука: последовательная, на ней же печатается рукопожатие —
    /// PCM не обгонит первую строку.
    private let audioQueue = DispatchQueue(label: "meet-audiotap.audio")
    private var ready = false
    /// Когда в stdout ушли последние кадры (звук или долитая тишина).
    private var lastWrite = DispatchTime.now()
    private var heartbeat: DispatchSourceTimer?
    /// Ошибки буферов: в stderr не чаще раза в errorEvery секунд.
    private var errorsSkipped = 0
    private var lastErrorAt: DispatchTime?
    private let errorEvery = 5.0
    /// Тишина дописывается, когда звука нет дольше этого (секунды). Меньше
    /// порога резидента (recorder.TICK_PAD_S = 1 с): иначе тишину дописали бы
    /// оба, и дорожка ушла бы вперёд стенных часов.
    private let silenceAfter = 0.5
    /// Пауза длиннее этого (секунды) тишиной не заполняется — см. fillSilence.
    private let fillLimit = 0.75

    init(rate: Int, channels: Int, excludePid: Int) {
        self.rate = rate
        self.channels = channels
        self.excludePid = excludePid
    }

    func start() async {
        let made: (SCStream, CGDirectDisplayID)
        do {
            made = try await makeStream()
        } catch {
            AudioTap.fail(error)
        }
        let (stream, display) = made
        await MainActor.run {
            self.stream = stream
            self.displayID = display
        }
        audioQueue.sync {
            printHandshake(rate: rate, channels: channels)
            ready = true
            lastWrite = DispatchTime.now()
        }
        let timer = DispatchSource.makeTimerSource(queue: audioQueue)
        timer.schedule(deadline: .now() + 0.25, repeating: 0.25)
        timer.setEventHandler { [weak self] in self?.fillSilence() }
        timer.resume()
        heartbeat = timer
        // Смена мониторов: фильтр привязан к дисплею — проверить, не пора ли
        // пересобрать его на новом основном.
        let status = CGDisplayRegisterReconfigurationCallback(
            displayReconfigured, Unmanaged.passUnretained(self).toOpaque())
        if status != .success {
            printError("смена дисплеев не отслеживается (\(status.rawValue))")
        }
    }

    /// Фильтр по основному дисплею (CGMainDisplayID; его нет в списке — первый
    /// дисплей), поток и запуск захвата. Сбой — исключение: на старте это выход
    /// с кодом, при пересборке — повтор.
    func makeStream() async throws -> (SCStream, CGDirectDisplayID) {
        // Все приложения, а не только с окнами на экране: окно Meet может быть
        // свёрнуто в трей, а плеер — звучать.
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        let main = CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == main })
            ?? content.displays.first
        else {
            throw NoDisplayError()
        }
        let excluded = excludePid > 0
            ? content.applications.filter { Int($0.processID) == excludePid } : []
        if excludePid > 0 && excluded.isEmpty {
            printError("приложение \(excludePid) не найдено — пишу весь системный звук")
        }
        let filter = SCContentFilter(display: display, excludingApplications: excluded,
                                     exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = rate
        config.channelCount = channels
        // Видео не нужно, но поток без него не запускается: минимальный кадр,
        // раз в секунду.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: screenQueue)
        try await stream.startCapture()
        return (stream, display.displayID)
    }

    /// Пересобрать захват внутри процесса: старый поток остановить, новый — на
    /// основном дисплее. Рукопожатие не повторяется; пока захвата нет, тишину
    /// дописывает fillSilence, и резидент видит живой поток. Главная очередь.
    func scheduleRebuild() {
        guard !stopping else { return }
        if rebuilding {
            rebuildAgain = true
            return
        }
        rebuilding = true
        let old = stream
        stream = nil
        Task {
            if let old = old {
                try? await old.stopCapture()
            }
            await self.rebuild()
        }
    }

    private func rebuild() async {
        while true {
            do {
                let (stream, display) = try await makeStream()
                await MainActor.run {
                    self.stream = stream
                    self.displayID = display
                    self.retryDelay = 0.5
                    self.rebuilding = false
                    if self.rebuildAgain {
                        self.rebuildAgain = false
                        self.scheduleRebuild()
                    }
                }
                return
            } catch {
                let nsError = error as NSError
                if nsError.code == userDeclinedCode || !CGPreflightScreenCaptureAccess() {
                    AudioTap.fail(error)
                }
                let delay = await MainActor.run { () -> Double in
                    let current = self.retryDelay
                    self.retryDelay = min(self.retryDelay * 2, self.retryMax)
                    return current
                }
                printError("захват не пересобран: \(nsError.domain) \(nsError.code) — повтор через \(delay) с")
                try? await Task.sleep(nanoseconds: UInt64(delay * 1e9))
            }
        }
    }

    /// Конфигурация дисплеев сменилась (главная очередь). Уведомления идут
    /// пачкой — решаем через секунду после последнего: основной дисплей уже не
    /// тот, по которому построен фильтр, или тот отключён — пересобрать.
    func displaysChanged() {
        displayGeneration += 1
        let generation = displayGeneration
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            guard generation == self.displayGeneration, !self.stopping else { return }
            if self.rebuilding {
                self.scheduleRebuild()
                return
            }
            if CGMainDisplayID() != self.displayID || CGDisplayIsOnline(self.displayID) == 0 {
                printError("сменился основной дисплей — пересобираю захват")
                self.scheduleRebuild()
            }
        }
    }

    /// ScreenCaptureKit молчит дольше silenceAfter — дописать тишину за
    /// прошедшее время: поток живой, и резидент отличит тишину от зависания.
    private func fillSilence() {
        guard ready else { return }
        let now = DispatchTime.now()
        let gap = Double(now.uptimeNanoseconds - lastWrite.uptimeNanoseconds) / 1e9
        guard gap > silenceAfter else { return }
        lastWrite = now
        // Длинная пауза (сон машины, задержка таймера) — не наша: её по стенным
        // часам дольёт резидент, а дописав её и здесь, дорожка ушла бы вперёд.
        guard gap <= fillLimit else { return }
        let frames = Int(gap * Double(rate))
        let zeros = [Int16](repeating: 0, count: frames * channels)
        zeros.withUnsafeBytes { writeAll($0) }
    }

    /// Ошибка буфера — в stderr не чаще раза в errorEvery секунд.
    private func reportBufferError(_ error: Error) {
        let now = DispatchTime.now()
        if let last = lastErrorAt,
           Double(now.uptimeNanoseconds - last.uptimeNanoseconds) / 1e9 < errorEvery {
            errorsSkipped += 1
            return
        }
        let skipped = errorsSkipped
        errorsSkipped = 0
        lastErrorAt = now
        printError("буфер звука не прочитан: \(error)" + (skipped > 0 ? " (и ещё \(skipped))" : ""))
    }

    static func fail(_ error: Error) -> Never {
        let nsError = error as NSError
        printError("ScreenCaptureKit: \(nsError.domain) \(nsError.code) \(nsError.localizedDescription)")
        if nsError.code == userDeclinedCode || !CGPreflightScreenCaptureAccess() {
            exit(exitPermission)
        }
        exit(exitFailed)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, ready, sampleBuffer.isValid else { return }
        do {
            try sampleBuffer.withAudioBufferList { list, _ in
                self.convert(list)
            }
        } catch {
            reportBufferError(error)
        }
    }

    func convert(_ list: UnsafeMutableAudioBufferListPointer) {
        samples.removeAll(keepingCapacity: true)
        appendPCM16(list, channels: channels, into: &samples)
        if samples.isEmpty { return }
        samples.withUnsafeBytes { writeAll($0) }
        lastWrite = DispatchTime.now()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        let nsError = error as NSError
        // Отозвали разрешение — пересборка не поможет: выход с кодом 77,
        // резидент скажет человеку, что делать.
        if nsError.code == userDeclinedCode || !CGPreflightScreenCaptureAccess() {
            AudioTap.fail(error)
        }
        printError("ScreenCaptureKit остановил захват: \(nsError.domain) \(nsError.code) — пересобираю")
        DispatchQueue.main.async { self.scheduleRebuild() }
    }

    func stop() {
        stopping = true
        guard let stream = stream else { exit(exitOK) }
        stream.stopCapture { _ in exit(exitOK) }
        // stopCapture не ответил — выходим всё равно.
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { exit(exitOK) }
    }
}

/// Обратный вызов CGDisplayRegisterReconfigurationCallback (функция C): контекст —
/// AudioTap; конец смены конфигурации — на его главную очередь.
func displayReconfigured(_ display: CGDirectDisplayID, _ flags: CGDisplayChangeSummaryFlags,
                         _ context: UnsafeMutableRawPointer?) {
    guard !flags.contains(.beginConfigurationFlag), let context = context else { return }
    if #available(macOS 13.0, *) {
        let tap = Unmanaged<AudioTap>.fromOpaque(context).takeUnretainedValue()
        DispatchQueue.main.async { tap.displaysChanged() }
    }
}

// MARK: - --stream --source process_tap, --check-tap

let screenSource = "screencapturekit"
let processTapSource = "process_tap"

struct CoreAudioError: LocalizedError {
    let call: String
    let status: OSStatus
    var errorDescription: String? { "\(call): \(status)" }
}

struct TapFormatError: LocalizedError {
    var errorDescription: String? { "перехват отдаёт не Float32 PCM" }
}

func require(_ status: OSStatus, _ call: String) throws {
    if status != noErr { throw CoreAudioError(call: call, status: status) }
}

/// Отказ CoreAudio вместо нулей в буферах — так macOS отвечает и без
/// разрешения «Запись системного звука» ('nope' или 'what').
func deniedByCoreAudio(_ error: Error) -> Bool {
    guard let status = (error as? CoreAudioError)?.status else { return false }
    return Int(status) == Int(kAudioHardwareIllegalOperationError)
        || Int(status) == Int(kAudioHardwareUnspecifiedError)
}

func hasOutput(_ device: AudioObjectID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreams,
        mScope: kAudioObjectPropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    return AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr && size > 0
}

/// UID встроенного выхода (динамики, разъём наушников). Aggregate-устройство
/// перехвата держится на нём, а не на выходе по умолчанию: тот меняется при
/// подключении Bluetooth-наушников.
func builtInOutputUID() -> String? {
    let system = AudioObjectID(kAudioObjectSystemObject)
    for device in readObjects(system, kAudioHardwarePropertyDevices) ?? [] {
        guard hasOutput(device),
              readUInt32(device, kAudioDevicePropertyTransportType) == kAudioDeviceTransportTypeBuiltIn
        else { continue }
        let uid = readString(device, kAudioDevicePropertyDeviceUID)
        if !uid.isEmpty { return uid }
    }
    return nil
}

/// Встроенного выхода нет (Mac без динамиков) — выход по умолчанию.
func defaultOutputUID() -> String? {
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard let device = readUInt32(system, kAudioHardwarePropertyDefaultOutputDevice),
          device != AudioObjectID(kAudioObjectUnknown)
    else { return nil }
    let uid = readString(device, kAudioDevicePropertyDeviceUID)
    return uid.isEmpty ? nil : uid
}

func parentPID(_ pid: pid_t) -> pid_t? {
    var info = proc_bsdinfo()
    let size = Int32(MemoryLayout<proc_bsdinfo>.size)
    guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
    return pid_t(info.pbi_ppid)
}

/// responsibility_get_pid_responsible_for_pid (libsystem, без заголовка): кто
/// «отвечает» за процесс. Звук окна Meet играет служба WebKit — её родитель
/// launchd, а отвечает за неё оболочка Meet. Функции нет — nil.
let responsibleFor: (@convention(c) (pid_t) -> pid_t)? = {
    guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2),
                             "responsibility_get_pid_responsible_for_pid")
    else { return nil }
    return unsafeBitCast(symbol, to: (@convention(c) (pid_t) -> pid_t).self)
}()

/// Процесс из дерева `root` (сам он, его потомки) или тот, за кого он отвечает.
func belongsTo(_ pid: pid_t, root: pid_t) -> Bool {
    if let responsible = responsibleFor, responsible(pid) == root { return true }
    var current = pid
    for _ in 0..<64 {
        if current == root { return true }
        guard current > 1, let parent = parentPID(current), parent != current else { return false }
        current = parent
    }
    return false
}

/// Процессы CoreAudio, чей звук перехват не пишет: процессы Meet (дерево
/// `--exclude-pid` и службы, за которые оно отвечает) и сам помощник.
@available(macOS 14.2, *)
func excludedProcesses(root: Int) -> [AudioObjectID] {
    let system = AudioObjectID(kAudioObjectSystemObject)
    let me = getpid()
    return (readObjects(system, kAudioHardwarePropertyProcessObjectList) ?? []).filter { object in
        guard let pid = readPID(object) else { return false }
        return pid == me || (root > 0 && belongsTo(pid, root: pid_t(root)))
    }
}

/// Линейная передискретизация s16 (выход перехвата не на запрошенной частоте,
/// например 44,1 кГц): для речи довольно. Кадр перед куском помнится, шов
/// между кусками не щёлкает.
struct LinearResampler {
    let step: Double
    let channels: Int
    private var position = 0.0
    private var last: [Int16]

    init(from source: Double, to target: Double, channels: Int) {
        step = source / target
        self.channels = channels
        last = [Int16](repeating: 0, count: channels)
    }

    mutating func run(_ input: [Int16], into output: inout [Int16]) {
        let frames = input.count / channels
        guard frames > 0 else { return }
        let previous = last
        let width = channels
        func value(_ frame: Int, _ channel: Int) -> Double {
            frame < 0 ? Double(previous[channel]) : Double(input[frame * width + channel])
        }
        while position <= Double(frames - 1) {
            let index = Int(position.rounded(.down))
            let frac = position - Double(index)
            for channel in 0..<channels {
                let a = value(index, channel)
                let b = index + 1 < frames ? value(index + 1, channel) : a
                output.append(Int16(max(-32768, min(32767, (a + (b - a) * frac).rounded()))))
            }
            position += step
        }
        position -= Double(frames)
        for channel in 0..<channels { last[channel] = input[(frames - 1) * channels + channel] }
    }
}

/// Core Audio process tap (macOS 14.2+, Meet включает с 14.4): глобальный
/// перехват системного звука без процессов Meet в частном aggregate-устройстве
/// на встроенном выходе. Разрешение — «Запись системного звука»; API, чтобы
/// узнать его, нет: без него вызовы проходят, а буферы приходят нулями.
/// Буферы → s16le на `rate`/`channels` → `sink` на очереди `queue`.
@available(macOS 14.2, *)
final class ProcessTap {
    let rate: Int
    let channels: Int
    /// Процесс Meet, чьё дерево не писать (0 — писать всё, кроме помощника).
    let excludePid: Int
    /// Очередь звука: на ней приходят буферы, зовётся sink и меняется список
    /// исключённых процессов.
    let queue = DispatchQueue(label: "meet-audiotap.tap")
    private let sink: ([Int16]) -> Void
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var procID: AudioDeviceIOProcID?
    private var description: CATapDescription?
    private var excluded: [AudioObjectID] = []
    private var processListener: AudioObjectPropertyListenerBlock?
    private var resampler: LinearResampler?
    private var samples: [Int16] = []
    private var resampled: [Int16] = []

    init(rate: Int, channels: Int, excludePid: Int, sink: @escaping ([Int16]) -> Void) {
        self.rate = rate
        self.channels = channels
        self.excludePid = excludePid
        self.sink = sink
    }

    /// Перехват, aggregate-устройство, IOProc и запуск. Сбой — исключение,
    /// созданное до него уже убрано. Системный вопрос о разрешении macOS
    /// задаёт на AudioDeviceStart.
    func start() throws {
        let processes = excludedProcesses(root: excludePid)
        let tap = CATapDescription(stereoGlobalTapButExcludeProcesses: processes)
        tap.uuid = UUID()
        tap.name = "Meet"
        tap.isPrivate = true
        tap.muteBehavior = .unmuted
        excluded = processes
        description = tap
        do {
            try require(AudioHardwareCreateProcessTap(tap, &tapID), "AudioHardwareCreateProcessTap")
            guard let output = builtInOutputUID() ?? defaultOutputUID() else {
                throw CoreAudioError(call: "нет устройства вывода", status: 0)
            }
            let aggregate: [String: Any] = [
                kAudioAggregateDeviceNameKey: "Meet — системный звук",
                kAudioAggregateDeviceUIDKey: UUID().uuidString,
                kAudioAggregateDeviceMainSubDeviceKey: output,
                kAudioAggregateDeviceIsPrivateKey: true,
                kAudioAggregateDeviceIsStackedKey: false,
                kAudioAggregateDeviceTapAutoStartKey: true,
                kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: output]],
                kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                                   kAudioSubTapUIDKey: tap.uuid.uuidString] as [String: Any]],
            ]
            try require(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID),
                        "AudioHardwareCreateAggregateDevice")
            let format = try tapFormat()
            if Int(format.mSampleRate.rounded()) != rate {
                resampler = LinearResampler(from: format.mSampleRate, to: Double(rate), channels: channels)
            }
            try require(AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) {
                [weak self] _, input, _, _, _ in self?.received(input)
            }, "AudioDeviceCreateIOProcIDWithBlock")
            try require(AudioDeviceStart(aggregateID, procID), "AudioDeviceStart")
        } catch {
            stop()
            throw error
        }
        listenForProcesses()
    }

    private func tapFormat() throws -> AudioStreamBasicDescription {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var format = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try require(AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &format),
                    "kAudioTapPropertyFormat")
        guard format.mFormatID == kAudioFormatLinearPCM,
              (format.mFormatFlags & kAudioFormatFlagIsFloat) != 0,
              format.mBitsPerChannel == 32, format.mSampleRate > 0
        else { throw TapFormatError() }
        return format
    }

    /// Буфер aggregate-устройства (очередь `queue`) → s16le → sink.
    private func received(_ input: UnsafePointer<AudioBufferList>) {
        let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        samples.removeAll(keepingCapacity: true)
        appendPCM16(list, channels: channels, into: &samples)
        if samples.isEmpty { return }
        guard var converter = resampler else {
            sink(samples)
            return
        }
        resampled.removeAll(keepingCapacity: true)
        converter.run(samples, into: &resampled)
        resampler = converter
        if !resampled.isEmpty { sink(resampled) }
    }

    /// Процесс Meet, начавший звук после старта (плеер записи), тоже мимо
    /// перехвата: список процессов CoreAudio сменился — обновить исключения.
    private func listenForProcesses() {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyProcessObjectList,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.processesChanged() }
        if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address,
                                               queue, block) == noErr {
            processListener = block
        }
    }

    private func processesChanged() {
        guard let tap = description, tapID != AudioObjectID(kAudioObjectUnknown) else { return }
        let current = excludedProcesses(root: excludePid)
        guard Set(current) != Set(excluded) else { return }
        excluded = current
        tap.processes = current
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyDescription,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var value = tap
        let target = tapID
        let status = withUnsafeMutablePointer(to: &value) { pointer in
            AudioObjectSetPropertyData(target, &address, 0, nil,
                                       UInt32(MemoryLayout<CATapDescription>.size), pointer)
        }
        if status != noErr {
            printError("process tap: исключения не обновлены (\(status))")
        }
    }

    /// Остановить и убрать всё созданное: у каждого create — парный destroy.
    func stop() {
        if let block = processListener {
            var address = AudioObjectPropertyAddress(
                mSelector: kAudioHardwarePropertyProcessObjectList,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain)
            _ = AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject),
                                                       &address, queue, block)
            processListener = nil
        }
        if let proc = procID {
            _ = AudioDeviceStop(aggregateID, proc)
            _ = AudioDeviceDestroyIOProcID(aggregateID, proc)
            procID = nil
        }
        if aggregateID != AudioObjectID(kAudioObjectUnknown) {
            _ = AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = AudioObjectID(kAudioObjectUnknown)
        }
        if tapID != AudioObjectID(kAudioObjectUnknown) {
            _ = AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }
}

/// `--stream --source process_tap`: то же рукопожатие и тот же PCM, что у
/// ScreenCaptureKit, тишина дописывается так же. Нули в буферах (нет
/// разрешения или тишина) — не повод выходить: их считает резидент.
@available(macOS 14.4, *)
final class ProcessTapStream {
    let rate: Int
    let channels: Int
    private var tap: ProcessTap!
    private var ready = false
    private var lastWrite = DispatchTime.now()
    private var heartbeat: DispatchSourceTimer?
    /// Как у AudioTap: меньше порога резидента (recorder.TICK_PAD_S = 1 с).
    private let silenceAfter = 0.5
    private let fillLimit = 0.75

    init(rate: Int, channels: Int, excludePid: Int) {
        self.rate = rate
        self.channels = channels
        tap = ProcessTap(rate: rate, channels: channels, excludePid: excludePid) { [weak self] samples in
            self?.write(samples)
        }
    }

    /// Главная очередь. Перехват не запустился — выход с кодом 70: резидент
    /// повторит с нарастающей паузой.
    func start() {
        do {
            try tap.start()
        } catch {
            printError("process tap: \(error.localizedDescription)")
            exit(exitFailed)
        }
        tap.queue.sync {
            printHandshake(rate: rate, channels: channels)
            ready = true
            lastWrite = DispatchTime.now()
        }
        let timer = DispatchSource.makeTimerSource(queue: tap.queue)
        timer.schedule(deadline: .now() + 0.25, repeating: 0.25)
        timer.setEventHandler { [weak self] in self?.fillSilence() }
        timer.resume()
        heartbeat = timer
    }

    private func write(_ samples: [Int16]) {
        guard ready else { return }
        samples.withUnsafeBytes { writeAll($0) }
        lastWrite = DispatchTime.now()
    }

    /// Как AudioTap.fillSilence: буферов нет дольше silenceAfter — нули за
    /// прошедшее время, длинная пауза — резиденту.
    private func fillSilence() {
        guard ready else { return }
        let now = DispatchTime.now()
        let gap = Double(now.uptimeNanoseconds - lastWrite.uptimeNanoseconds) / 1e9
        guard gap > silenceAfter else { return }
        lastWrite = now
        guard gap <= fillLimit else { return }
        let zeros = [Int16](repeating: 0, count: Int(gap * Double(rate)) * channels)
        zeros.withUnsafeBytes { writeAll($0) }
    }

    func stop() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { exit(exitOK) }
        heartbeat?.cancel()
        tap.stop()
        exit(exitOK)
    }
}

var checkHolder: AnyObject?

/// `--check-tap [--seconds 5] [--exclude-pid N]`: проверка при включении
/// process tap. Пишет столько секунд (человек включил любой звук) и печатает
/// {"meet_audiotap", "check": "process_tap", "frames", "peak"} — пик по модулю
/// s16. Есть ли разрешение, решает резидент по пику: без него буферы — нули.
/// Отказ CoreAudio сразу — код 77, macOS старше 14.4 — 69.
func checkTap(_ args: [String]) -> Never {
    let seconds = option("--seconds", in: args, default: 5)
    let excludePid = option("--exclude-pid", in: args, default: 0)
    guard (1...30).contains(seconds) else {
        printError("--seconds: от 1 до 30")
        exit(exitUsage)
    }
    guard #available(macOS 14.4, *) else {
        printError("process tap: нужна macOS 14.4 или новее")
        exit(exitUnsupported)
    }
    var frames = 0
    var peak = 0
    let tap = ProcessTap(rate: 48000, channels: 1, excludePid: excludePid) { samples in
        frames += samples.count
        for sample in samples { peak = max(peak, abs(Int(sample))) }
    }
    checkHolder = tap
    do {
        try tap.start()
    } catch {
        printError("process tap: \(error.localizedDescription)")
        exit(deniedByCoreAudio(error) ? exitPermission : exitFailed)
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(seconds)) {
        tap.stop()
        let (count, loudest) = tap.queue.sync { (frames, peak) }
        printJSON(["meet_audiotap": protocolVersion, "check": "process_tap", "frames": count,
                   "peak": loudest])
        exit(exitOK)
    }
    dispatchMain()
}

func stringOption(_ name: String, in args: [String], default value: String) -> String {
    guard let index = args.firstIndex(of: name) else { return value }
    guard index + 1 < args.count else {
        printError("\(name): нужно значение")
        exit(exitUsage)
    }
    return args[index + 1]
}

func option(_ name: String, in args: [String], default value: Int) -> Int {
    guard let index = args.firstIndex(of: name) else { return value }
    guard index + 1 < args.count, let parsed = Int(args[index + 1]) else {
        printError("\(name): нужно число")
        exit(exitUsage)
    }
    return parsed
}

var tapHolder: AnyObject?
var signalSources: [DispatchSourceSignal] = []

func runStream(_ args: [String]) -> Never {
    let rate = option("--rate", in: args, default: 48000)
    let channels = option("--channels", in: args, default: 1)
    let excludePid = option("--exclude-pid", in: args, default: 0)
    let capture = stringOption("--source", in: args, default: screenSource)
    guard [8000, 16000, 24000, 48000].contains(rate), channels == 1 || channels == 2 else {
        printError("поддерживаются частоты 8000/16000/24000/48000 и 1–2 канала")
        exit(exitUsage)
    }
    guard capture == screenSource || capture == processTapSource else {
        printError("--source: \(screenSource) или \(processTapSource)")
        exit(exitUsage)
    }
    let stop: () -> Void
    let begin: () -> Void
    if capture == processTapSource {
        // Process tap: разрешение «Запись экрана» не нужно и не проверяется.
        guard #available(macOS 14.4, *) else {
            printError("process tap: нужна macOS 14.4 или новее")
            exit(exitUnsupported)
        }
        let tap = ProcessTapStream(rate: rate, channels: channels, excludePid: excludePid)
        tapHolder = tap
        stop = { tap.stop() }
        begin = { tap.start() }
    } else {
        guard #available(macOS 13.0, *) else {
            printError("нужна macOS 13 или новее")
            exit(exitUnsupported)
        }
        // Разрешения нет — попросить (macOS покажет запрос и откроет настройки)
        // и выйти с кодом 77: резидент скажет человеку, что делать.
        if !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()
            printError("нет разрешения «Запись экрана»")
            exit(exitPermission)
        }
        let tap = AudioTap(rate: rate, channels: channels, excludePid: excludePid)
        tapHolder = tap
        stop = { tap.stop() }
        begin = { _ = Task { await tap.start() } }
    }
    signal(SIGPIPE, SIG_IGN)
    for sig in [SIGTERM, SIGINT] {
        signal(sig, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        source.setEventHandler { stop() }
        source.resume()
        signalSources.append(source)
    }
    // Конец stdin — резидент закончил запись (или умер): останавливаемся.
    Thread.detachNewThread {
        var byte: UInt8 = 0
        while Darwin.read(STDIN_FILENO, &byte, 1) > 0 {}
        DispatchQueue.main.async { stop() }
    }
    DispatchQueue.main.async { begin() }
    // Главный цикл событий, а не dispatchMain: через него приходят обратные
    // вызовы смены дисплеев, им же крутится главная очередь (сигналы, конец
    // stdin, пересборка). Порт держит цикл, пока не выйдем сами.
    RunLoop.main.add(Port(), forMode: .default)
    while true {
        RunLoop.main.run()
    }
}

// MARK: - main

let arguments = Array(CommandLine.arguments.dropFirst())
switch arguments.first {
case "--self-test"?:
    selfTest()
case "--preflight"?:
    preflight()
case "--mic-users"?:
    micUsers()
case "--mic-status"?:
    micStatus()
case "--request-mic"?:
    requestMic()
case "--request-screen"?:
    requestScreen()
case "--watch-devices"?:
    watchDevices()
case "--stream"?:
    runStream(arguments)
case "--check-tap"?:
    checkTap(arguments)
default:
    printError("использование: meet-audiotap --stream [--rate 48000] [--channels 1] [--exclude-pid N] [--source screencapturekit|process_tap] | --check-tap [--seconds 5] [--exclude-pid N] | --mic-users | --watch-devices | --preflight | --mic-status | --request-mic | --request-screen | --self-test")
    exit(exitUsage)
}
