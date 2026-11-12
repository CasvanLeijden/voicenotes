// `audiotap --mic-users`: print which processes currently use audio input, as one JSON line:
//   [{"pid":123,"bundle":"com.microsoft.teams2"}, ...]
// `audiotap --dump-processes`: debug listing of all Core Audio process objects.
//
// Both read Core Audio process metadata only (macOS 14+) and need no recording permission.
// Run a fresh process per query: a long-running HAL client does not see changes reliably.

import CoreAudio
import Foundation

private func readProperty<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
                             _ initial: T) -> T? {
    var address = AudioObjectPropertyAddress(mSelector: selector,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var value = initial
    var size = UInt32(MemoryLayout<T>.size)
    let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    return status == noErr ? value : nil
}

private func processObjects() -> [AudioObjectID] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr, size > 0 else {
        return []
    }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

private func info(_ obj: AudioObjectID) -> (pid: Int32, bundle: String) {
    let pid: pid_t = readProperty(obj, kAudioProcessPropertyPID, pid_t(-1)) ?? -1
    let bundle: CFString = readProperty(obj, kAudioProcessPropertyBundleID, "" as CFString) ?? ("" as CFString)
    return (pid, bundle as String)
}

private func jsonEscape(_ s: String) -> String {
    s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
}

/// Core Audio process object for a pid, without touching that process object's properties.
private func processObject(for pid: pid_t) -> AudioObjectID? {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var p = pid
    var obj = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address,
                                            UInt32(MemoryLayout<pid_t>.size), &p, &size, &obj)
    return status == noErr && obj != kAudioObjectUnknown ? obj : nil
}

func printMicUsers() -> Never {
    let own = getpid()
    // Never read properties of the caller's own process object: doing so while the caller
    // starts/stops an audio stream can deadlock that stream for seconds.
    var skip = Set<AudioObjectID>()
    let args = CommandLine.arguments
    if let i = args.firstIndex(of: "--exclude-pid"), i + 1 < args.count, let pid = pid_t(args[i + 1]),
       let obj = processObject(for: pid) {
        skip.insert(obj)
    }
    let items = processObjects().compactMap { obj -> String? in
        if skip.contains(obj) { return nil }
        guard let input: UInt32 = readProperty(obj, kAudioProcessPropertyIsRunningInput, UInt32(0)),
              input != 0 else { return nil }
        let p = info(obj)
        if p.pid == own { return nil }
        return "{\"pid\":\(p.pid),\"bundle\":\"\(jsonEscape(p.bundle))\"}"
    }
    print("[" + items.joined(separator: ",") + "]")
    exit(0)
}

func dumpProcesses() -> Never {
    for obj in processObjects() {
        let p = info(obj)
        let input: UInt32 = readProperty(obj, kAudioProcessPropertyIsRunningInput, UInt32(0)) ?? 99
        let output: UInt32 = readProperty(obj, kAudioProcessPropertyIsRunningOutput, UInt32(0)) ?? 99
        print("pid=\(p.pid) bundle=\(p.bundle) input=\(input) output=\(output)")
    }
    exit(0)
}
