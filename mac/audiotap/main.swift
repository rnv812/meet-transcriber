// meet-audiotap — помощник Meet для macOS (экспериментально).
//
// На Windows системный звук пишется loopback'ом WASAPI, а «кто держит
// микрофон» видно в реестре. На macOS обоих сигналов у Python нет, поэтому
// они здесь — в маленькой программе без зависимостей, которую резидент
// запускает подпроцессом (`meet/audiotap.py`, `meet/mac_audio.py`):
//
//   meet-audiotap --stream [--rate 48000] [--channels 1]
//       ScreenCaptureKit (macOS 13+), только звук. Первая строка stdout —
//       рукопожатие JSON {"meet_audiotap": 1, "rate", "channels", "format":
//       "s16le"}, дальше — сырой PCM s16le, пока не закрыт stdin или не
//       пришёл SIGTERM/SIGINT. Нужно разрешение «Запись экрана».
//   meet-audiotap --mic-users
//       Одна строка JSON: процессы, которые сейчас слушают микрофон или
//       играют звук (CoreAudio, macOS 14+; раньше — "supported": false).
//   meet-audiotap --self-test
//       Версия и возможности одной строкой JSON; разрешений не требует (CI).
//
// Коды выхода: 0 — штатно, 64 — неверные аргументы, 69 — macOS старше 13,
// 70 — сбой захвата, 77 — нет разрешения «Запись экрана».
//
// Сборка (CI, scripts/build_release_macos.sh):
//   swiftc -O -swift-version 5 -target arm64-apple-macos13.0 \
//     -o meet-audiotap mac/audiotap/main.swift

import CoreAudio
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

let protocolVersion = 1
let helperVersion = "0.3.0"

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

@available(macOS 14.0, *)
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

@available(macOS 14.0, *)
func readBundleID(_ object: AudioObjectID) -> String {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioProcessPropertyBundleID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    guard status == noErr, let text = value?.takeRetainedValue() else { return "" }
    return text as String
}

@available(macOS 14.0, *)
func audioProcesses() -> [[String: Any]]? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    let system = AudioObjectID(kAudioObjectSystemObject)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr else {
        return nil
    }
    let count = Int(size) / MemoryLayout<AudioObjectID>.size
    if count == 0 { return [] }
    var objects = [AudioObjectID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &objects) == noErr else {
        return nil
    }
    var found: [[String: Any]] = []
    for object in objects {
        guard let pid = readPID(object) else { continue }
        let input = (readUInt32(object, kAudioProcessPropertyIsRunningInput) ?? 0) != 0
        let output = (readUInt32(object, kAudioProcessPropertyIsRunningOutput) ?? 0) != 0
        found.append([
            "pid": Int(pid),
            "name": processName(pid),
            "bundle": readBundleID(object),
            "input": input,
            "output": output,
        ])
    }
    return found
}

func micUsers() -> Never {
    if #available(macOS 14.0, *) {
        guard let processes = audioProcesses() else {
            printJSON(["meet_audiotap": protocolVersion, "supported": false,
                       "note": "CoreAudio не ответил", "processes": []])
            exit(exitOK)
        }
        printJSON(["meet_audiotap": protocolVersion, "supported": true, "processes": processes])
    } else {
        printJSON(["meet_audiotap": protocolVersion, "supported": false,
                   "note": "нужна macOS 14 или новее", "processes": []])
    }
    exit(exitOK)
}

// MARK: - --stream

@available(macOS 13.0, *)
final class AudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
    let rate: Int
    let channels: Int
    var stream: SCStream?
    private var samples: [Int16] = []
    /// Очередь звука: последовательная, на ней же печатается рукопожатие —
    /// PCM не обгонит первую строку.
    private let audioQueue = DispatchQueue(label: "meet-audiotap.audio")
    private var ready = false

    init(rate: Int, channels: Int) {
        self.rate = rate
        self.channels = channels
    }

    func start() async {
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: true)
        } catch {
            AudioTap.fail(error)
        }
        guard let display = content.displays.first else {
            printError("нет дисплея для захвата")
            exit(exitFailed)
        }
        let filter = SCContentFilter(display: display, excludingApplications: [],
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
        }
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
            printError("буфер звука не прочитан: \(error)")
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
    let tap = AudioTap(rate: rate, channels: channels)
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
case "--mic-users"?:
    micUsers()
case "--stream"?:
    runStream(arguments)
default:
    printError("использование: meet-audiotap --stream [--rate 48000] [--channels 1] | --mic-users | --self-test")
    exit(exitUsage)
}
