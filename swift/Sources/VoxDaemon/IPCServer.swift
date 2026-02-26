import Foundation

/// Unix domain socket IPC server for communication with the Python intelligence layer.
/// Listens at a configurable socket path, accepts a single client connection,
/// reads JSON-over-newline messages, and provides send(message:) for responses.
final class IPCServer {

    // MARK: - Properties

    let socketPath: String
    private var listenFD: Int32 = -1
    private var clientFD: Int32 = -1
    private var listenSource: DispatchSourceRead?
    private var clientSource: DispatchSourceRead?
    private var readBuffer = Data()
    private let queue = DispatchQueue(label: "com.vox.ipc", qos: .userInitiated)

    /// Called on the IPC queue when a complete JSON message line is received.
    var onMessage: ((Data) -> Void)?

    // MARK: - Lifecycle

    init(socketPath: String) {
        self.socketPath = socketPath
    }

    deinit {
        shutdown()
    }

    /// Start listening for a client connection.
    func start() throws {
        // Clean up any leftover socket file from a previous run
        unlink(socketPath)

        // Create Unix domain socket
        listenFD = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listenFD >= 0 else {
            throw IPCError.socketCreationFailed(errno: errno)
        }

        // Bind to socket path
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = socketPath.utf8CString
        guard pathBytes.count <= MemoryLayout.size(ofValue: addr.sun_path) else {
            close(listenFD)
            listenFD = -1
            throw IPCError.socketPathTooLong
        }
        withUnsafeMutablePointer(to: &addr.sun_path) { sunPathPtr in
            sunPathPtr.withMemoryRebound(to: CChar.self, capacity: pathBytes.count) { dest in
                for i in 0..<pathBytes.count {
                    dest[i] = pathBytes[i]
                }
            }
        }

        let bindResult = withUnsafePointer(to: &addr) { addrPtr in
            addrPtr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPtr in
                bind(listenFD, sockaddrPtr, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bindResult == 0 else {
            let err = errno
            close(listenFD)
            listenFD = -1
            throw IPCError.bindFailed(errno: err)
        }

        // Listen with backlog of 1 (single client)
        guard listen(listenFD, 1) == 0 else {
            let err = errno
            close(listenFD)
            listenFD = -1
            throw IPCError.listenFailed(errno: err)
        }

        log(.info, "IPC server listening at \(socketPath)")

        // Set up dispatch source for incoming connections
        let source = DispatchSource.makeReadSource(fileDescriptor: listenFD, queue: queue)
        source.setEventHandler { [weak self] in
            self?.acceptClient()
        }
        source.setCancelHandler { [weak self] in
            if let fd = self?.listenFD, fd >= 0 {
                close(fd)
                self?.listenFD = -1
            }
        }
        listenSource = source
        source.resume()
    }

    /// Send a Codable message to the connected client as JSON + newline.
    /// Returns false if no client is connected.
    @discardableResult
    func send<T: Encodable>(message: T) -> Bool {
        guard clientFD >= 0 else {
            log(.warning, "IPC send failed — no client connected")
            return false
        }

        do {
            let encoder = JSONEncoder()
            var data = try encoder.encode(message)
            data.append(contentsOf: [UInt8(ascii: "\n")])
            let written = data.withUnsafeBytes { bufferPtr in
                Darwin.write(clientFD, bufferPtr.baseAddress!, data.count)
            }
            if written < 0 {
                log(.warning, "IPC send failed — write error errno=\(errno)")
                disconnectClient()
                return false
            }
            return true
        } catch {
            log(.warning, "IPC send failed — encoding error")
            return false
        }
    }

    /// Whether a client is currently connected.
    var isClientConnected: Bool {
        return clientFD >= 0
    }

    /// Shut down the server: disconnect client, stop listening, unlink socket.
    func shutdown() {
        queue.sync {
            disconnectClientInternal()
            listenSource?.cancel()
            listenSource = nil
            if listenFD >= 0 {
                close(listenFD)
                listenFD = -1
            }
            unlink(socketPath)
        }
        log(.info, "IPC server shut down — socket removed")
    }

    // MARK: - Private

    private func acceptClient() {
        var clientAddr = sockaddr_un()
        var clientAddrLen = socklen_t(MemoryLayout<sockaddr_un>.size)

        let newFD = withUnsafeMutablePointer(to: &clientAddr) { addrPtr in
            addrPtr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPtr in
                accept(listenFD, sockaddrPtr, &clientAddrLen)
            }
        }

        guard newFD >= 0 else {
            log(.warning, "IPC accept failed — errno=\(errno)")
            return
        }

        // If a client is already connected, disconnect the old one (allows reconnect)
        if clientFD >= 0 {
            log(.info, "IPC client reconnecting — disconnecting previous client")
            disconnectClientInternal()
        }

        clientFD = newFD
        readBuffer = Data()
        log(.info, "IPC client connected")

        // Set up dispatch source for reading from client
        let source = DispatchSource.makeReadSource(fileDescriptor: newFD, queue: queue)
        source.setEventHandler { [weak self] in
            self?.readFromClient()
        }
        source.setCancelHandler { [weak self] in
            if let fd = self?.clientFD, fd >= 0 {
                close(fd)
                self?.clientFD = -1
            }
        }
        clientSource = source
        source.resume()
    }

    private func readFromClient() {
        var buffer = [UInt8](repeating: 0, count: 4096)
        let bytesRead = read(clientFD, &buffer, buffer.count)

        if bytesRead <= 0 {
            // Client disconnected or error
            if bytesRead == 0 {
                log(.info, "IPC client disconnected")
            } else {
                log(.warning, "IPC client read error — errno=\(errno)")
            }
            disconnectClientInternal()
            return
        }

        readBuffer.append(contentsOf: buffer[0..<bytesRead])
        processLines()
    }

    /// Extract complete newline-delimited JSON messages from the read buffer.
    private func processLines() {
        let newline = UInt8(ascii: "\n")
        while let newlineIndex = readBuffer.firstIndex(of: newline) {
            let lineData = readBuffer[readBuffer.startIndex..<newlineIndex]
            readBuffer = Data(readBuffer[(newlineIndex + 1)...])

            // Skip empty lines
            guard !lineData.isEmpty else { continue }

            // Validate that it's parseable JSON before passing to handler
            guard JSONSerialization.isValidJSON(lineData) else {
                log(.warning, "IPC received malformed JSON — skipping message (\(lineData.count) bytes)")
                continue
            }

            onMessage?(Data(lineData))
        }
    }

    private func disconnectClient() {
        queue.async { [weak self] in
            self?.disconnectClientInternal()
        }
    }

    private func disconnectClientInternal() {
        clientSource?.cancel()
        clientSource = nil
        if clientFD >= 0 {
            close(clientFD)
            clientFD = -1
        }
        readBuffer = Data()
    }
}

// MARK: - JSON Validation Helper

private extension JSONSerialization {
    static func isValidJSON(_ data: Data) -> Bool {
        do {
            _ = try JSONSerialization.jsonObject(with: data)
            return true
        } catch {
            return false
        }
    }
}

// MARK: - IPC Errors

enum IPCError: Error, CustomStringConvertible {
    case socketCreationFailed(errno: Int32)
    case socketPathTooLong
    case bindFailed(errno: Int32)
    case listenFailed(errno: Int32)

    var description: String {
        switch self {
        case .socketCreationFailed(let err):
            return "Failed to create Unix domain socket: \(String(cString: strerror(err)))"
        case .socketPathTooLong:
            return "Socket path exceeds maximum length for sockaddr_un"
        case .bindFailed(let err):
            return "Failed to bind socket: \(String(cString: strerror(err)))"
        case .listenFailed(let err):
            return "Failed to listen on socket: \(String(cString: strerror(err)))"
        }
    }
}
