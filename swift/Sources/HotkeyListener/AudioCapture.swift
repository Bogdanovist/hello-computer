import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Captures microphone audio to a temporary WAV file.
///
/// Records at 16kHz, mono, 16-bit PCM — the format required by whisper.cpp.
/// Audio is written to `/tmp/vox_audio_XXXX.wav` where XXXX is a unique identifier.
public final class AudioCapture {

    // MARK: - Types

    public enum CaptureError: Error {
        case recorderSetupFailed
        case recordingFailed
    }

    // MARK: - Properties

    /// Optional logging callback. Messages are content-free (no audio content).
    public var logHandler: ((String) -> Void)?

    /// Whether audio is currently being captured.
    public private(set) var isRecording: Bool = false

    /// URL of the current recording's temp file, if recording.
    public private(set) var currentOutputURL: URL?

    #if canImport(AVFoundation)
    private var audioRecorder: AVAudioRecorder?
    #endif

    // MARK: - Lifecycle

    public init() {}

    deinit {
        if isRecording {
            stopRecording()
        }
    }

    // MARK: - Public Methods

    /// Begin recording audio from the microphone.
    ///
    /// Creates a temp file at `/tmp/vox_audio_XXXX.wav` and starts recording.
    /// Recording format: 16kHz, mono, 16-bit linear PCM (WAV).
    ///
    /// - Throws: `CaptureError.recorderSetupFailed` if the audio recorder cannot be configured.
    public func startRecording() throws {
        guard !isRecording else {
            logHandler?("AudioCapture: already recording")
            return
        }

        let outputURL = generateTempFileURL()
        currentOutputURL = outputURL

        #if canImport(AVFoundation)
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16000.0,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]

        do {
            let recorder = try AVAudioRecorder(url: outputURL, settings: settings)
            recorder.prepareToRecord()

            guard recorder.record() else {
                logHandler?("AudioCapture: recorder.record() returned false")
                currentOutputURL = nil
                throw CaptureError.recordingFailed
            }

            audioRecorder = recorder
            isRecording = true
            logHandler?("AudioCapture: recording started")
        } catch let error as CaptureError {
            throw error
        } catch {
            logHandler?("AudioCapture: failed to create recorder")
            currentOutputURL = nil
            throw CaptureError.recorderSetupFailed
        }
        #else
        logHandler?("AudioCapture: AVFoundation not available — recording not supported")
        currentOutputURL = nil
        throw CaptureError.recorderSetupFailed
        #endif
    }

    /// Stop recording and return the URL of the recorded audio file.
    ///
    /// - Returns: URL of the temporary WAV file, or `nil` if not currently recording.
    @discardableResult
    public func stopRecording() -> URL? {
        guard isRecording else {
            logHandler?("AudioCapture: not recording")
            return nil
        }

        #if canImport(AVFoundation)
        audioRecorder?.stop()
        audioRecorder = nil
        #endif

        isRecording = false
        let url = currentOutputURL
        currentOutputURL = nil
        logHandler?("AudioCapture: recording stopped")
        return url
    }

    /// Delete a temporary audio file.
    ///
    /// Call this after whisper.cpp has finished processing the file.
    public static func deleteTempFile(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    // MARK: - Private

    /// Generate a unique temporary file path for audio recording.
    private func generateTempFileURL() -> URL {
        let uniqueID = UUID().uuidString.prefix(8)
        return URL(fileURLWithPath: "/tmp/vox_audio_\(uniqueID).wav")
    }
}
