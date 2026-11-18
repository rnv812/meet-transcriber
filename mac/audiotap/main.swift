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
//       раза в 5 с (со счётчиком пропущенных).
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
// Коды выхода: 0 — штатно, 64 — неверные аргументы, 69 — macOS старше 13,
// 70 — сбой захвата, 77 — нет разрешения «Запись экрана».
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
    if #available(macOS 13.0, *) { stream = true }
    if #available(macOS 14.0, *) { micUsers = true }
    printJSON([
        "meet_audiotap": protocolVersion,
        "version": helperVersion,
        "macos": "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
        "stream": stream,
        "mic_users": micUsers,
        "exclude_pid": true,
        "permissions": true,
        "watch_devices": true,
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

@available(macOS 13.0, *)
final class AudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
    let rate: Int
    let channels: Int
    /// Процесс приложения, чей звук не писать (0 — писать всё).
    let excludePid: Int
    var stream: SCStream?
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
        let content: SCShareableContent
        do {
            // Все приложения, а не только с окнами на экране: окно Meet может быть
            // свёрнуто в трей, а плеер — звучать.
            content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: false)
        } catch {
            AudioTap.fail(error)
        }
        guard let display = content.displays.first else {
            printError("нет дисплея для захвата")
            exit(exitFailed)
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
        do {
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
            try stream.addStreamOutput(self, type: .screen,
                                       sampleHandlerQueue: DispatchQueue(label: "meet-audiotap.screen"))
            try await stream.startCapture()
        } catch {
            AudioTap.fail(error)
        }
        self.stream = stream
        audioQueue.sync {
            printJSON(["meet_audiotap": protocolVersion, "rate": rate, "channels": channels,
                       "format": "s16le"])
            ready = true
            lastWrite = DispatchTime.now()
        }
        let timer = DispatchSource.makeTimerSource(queue: audioQueue)
        timer.schedule(deadline: .now() + 0.25, repeating: 0.25)
        timer.setEventHandler { [weak self] in self?.fillSilence() }
        timer.resume()
        heartbeat = timer
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

    /// Float32 (по буферу на канал или чередованием) → s16le с нужным числом
    /// каналов: лишние каналы усредняются в моно.
    func convert(_ list: UnsafeMutableAudioBufferListPointer) {
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
        samples.removeAll(keepingCapacity: true)
        samples.reserveCapacity(frames * channels)
        for frame in 0..<frames {
            if channels == 1 {
                var sum: Float32 = 0
                for channel in 0..<sourceChannels { sum += sample(frame, channel) }
                samples.append(AudioTap.int16(sum / Float32(sourceChannels)))
            } else {
                for channel in 0..<channels { samples.append(AudioTap.int16(sample(frame, channel))) }
            }
        }
        samples.withUnsafeBytes { writeAll($0) }
        lastWrite = DispatchTime.now()
    }

    static func int16(_ value: Float32) -> Int16 {
        let clipped = max(-1, min(1, value))
        return Int16(clipped * 32767).littleEndian
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        AudioTap.fail(error)
    }

    func stop() {
        guard let stream = stream else { exit(exitOK) }
        stream.stopCapture { _ in exit(exitOK) }
        // stopCapture не ответил — выходим всё равно.
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { exit(exitOK) }
    }
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
    guard [8000, 16000, 24000, 48000].contains(rate), channels == 1 || channels == 2 else {
        printError("поддерживаются частоты 8000/16000/24000/48000 и 1–2 канала")
        exit(exitUsage)
    }
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
    signal(SIGPIPE, SIG_IGN)
    let tap = AudioTap(rate: rate, channels: channels, excludePid: excludePid)
    tapHolder = tap
    for sig in [SIGTERM, SIGINT] {
        signal(sig, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        source.setEventHandler { tap.stop() }
        source.resume()
        signalSources.append(source)
    }
    // Конец stdin — резидент закончил запись (или умер): останавливаемся.
    Thread.detachNewThread {
        var byte: UInt8 = 0
        while Darwin.read(STDIN_FILENO, &byte, 1) > 0 {}
        DispatchQueue.main.async { tap.stop() }
    }
    Task { await tap.start() }
    dispatchMain()
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
default:
    printError("использование: meet-audiotap --stream [--rate 48000] [--channels 1] [--exclude-pid N] | --mic-users | --watch-devices | --preflight | --mic-status | --request-mic | --request-screen | --self-test")
    exit(exitUsage)
}
