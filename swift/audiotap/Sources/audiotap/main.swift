// audiotap: capture all system audio output via a Core Audio global process tap
// (macOS 14.2+) and stream it as raw float32 interleaved PCM to stdout.
//
// stderr protocol: first line is "FORMAT <sampleRate> <channels>", errors start with "ERROR".
// Stops cleanly on SIGINT/SIGTERM or when stdout is closed.

import AudioToolbox
import CoreAudio
import Foundation

if CommandLine.arguments.contains("--dump-processes") {
    dumpProcesses()
}
if CommandLine.arguments.contains("--mic") {
    streamMic()
}
if CommandLine.arguments.contains("--mic-users") {
    printMicUsers()
}

func fail(_ msg: String, _ status: OSStatus = 0) -> Never {
    FileHandle.standardError.write("ERROR \(msg) (OSStatus \(status))\n".data(using: .utf8)!)
    exit(1)
}

func getProperty<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
                    _ initial: T) -> T {
    var address = AudioObjectPropertyAddress(mSelector: selector,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var value = initial
    var size = UInt32(MemoryLayout<T>.size)
    let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    if status != noErr { fail("kon eigenschap \(selector) niet lezen", status) }
    return value
}

// Exclude our own process so we never record ourselves.
var ownProcess = AudioObjectID(kAudioObjectUnknown)
do {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var pid = getpid()
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address,
                               UInt32(MemoryLayout<pid_t>.size), &pid, &size, &ownProcess)
}
let excluded = ownProcess == kAudioObjectUnknown ? [] : [ownProcess]

let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: excluded)
tapDescription.uuid = UUID()
tapDescription.isPrivate = true
tapDescription.muteBehavior = .unmuted
tapDescription.name = "Voicenotes"

var tapID = AudioObjectID(kAudioObjectUnknown)
var status = AudioHardwareCreateProcessTap(tapDescription, &tapID)
if status != noErr { fail("kon audio-tap niet maken; geef toestemming voor systeemaudio", status) }

let outputDevice: AudioDeviceID = getProperty(AudioObjectID(kAudioObjectSystemObject),
                                              kAudioHardwarePropertyDefaultSystemOutputDevice,
                                              AudioDeviceID(0))
let outputUID: CFString = getProperty(outputDevice, kAudioDevicePropertyDeviceUID, "" as CFString)

let aggregateDescription: [String: Any] = [
    kAudioAggregateDeviceNameKey: "Voicenotes Tap",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: outputUID as String,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID as String]],
    kAudioAggregateDeviceTapListKey: [[
        kAudioSubTapDriftCompensationKey: true,
        kAudioSubTapUIDKey: tapDescription.uuid.uuidString,
    ]],
]

var aggregateID = AudioObjectID(kAudioObjectUnknown)
status = AudioHardwareCreateAggregateDevice(aggregateDescription as CFDictionary, &aggregateID)
if status != noErr { fail("kon aggregate device niet maken", status) }

var format: AudioStreamBasicDescription = getProperty(tapID, kAudioTapPropertyFormat,
                                                      AudioStreamBasicDescription())
let channels = Int(format.mChannelsPerFrame)
let nonInterleaved = (format.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
FileHandle.standardError.write("FORMAT \(Int(format.mSampleRate)) \(channels)\n".data(using: .utf8)!)

let stdout = FileHandle.standardOutput
let queue = DispatchQueue(label: "audiotap.io")
var scratch = [Float]()

func cleanup() {
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
}

var procID: AudioDeviceIOProcID?
status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) { _, inData, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
    guard let first = buffers.first, let base = first.mData else { return }
    if !nonInterleaved || buffers.count == 1 {
        let data = Data(bytes: base, count: Int(first.mDataByteSize))
        do { try stdout.write(contentsOf: data) } catch { cleanup(); exit(0) }
        return
    }
    // Non-interleaved: interleave the per-channel buffers ourselves.
    let frames = Int(first.mDataByteSize) / MemoryLayout<Float>.size
    if scratch.count < frames * buffers.count { scratch = [Float](repeating: 0, count: frames * buffers.count) }
    for (c, buf) in buffers.enumerated() {
        let src = buf.mData!.assumingMemoryBound(to: Float.self)
        for f in 0..<frames { scratch[f * buffers.count + c] = src[f] }
    }
    let data = scratch.withUnsafeBytes { Data($0.prefix(frames * buffers.count * 4)) }
    do { try stdout.write(contentsOf: data) } catch { cleanup(); exit(0) }
}
if status != noErr { fail("kon IOProc niet maken", status) }

status = AudioDeviceStart(aggregateID, procID)
if status != noErr { fail("kon opname niet starten", status) }

signal(SIGPIPE, SIG_IGN)
var _signalSources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler {
        AudioDeviceStop(aggregateID, procID)
        if let procID { AudioDeviceDestroyIOProcID(aggregateID, procID) }
        cleanup()
        exit(0)
    }
    source.resume()
    _signalSources.append(source)
}
dispatchMain()
