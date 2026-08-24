// Cross-platform Named Pipe Server for IPC with C++ client
const net = require('net');
const os = require('os');
const path = require('path');
const fs = require('fs');

// MESSAGES
const MESSAGES = {
    MSG_NEW_FILE_PATH: 'new_file_path',
    MSG_NEW_FILE_PATH_ACK: 'new_file_path_ack',
    MSG_MODULE_READY: 'module_ready',
    MSG_LIST_MODULES: 'list_modules',
    MSG_LIST_MODULES_ACK: 'list_modules_ack'
}

// ERRORS
const ERRORS = {
    ERROR_CODE_GENERIC: 100,
    ERROR_CODE_INVALID_FILE_PATH: 101
}

// Message packing and unpacking utilities
function packMessage(type, payload) {
    const typeBuf = Buffer.from(type);
    const payloadBuf = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
    const buf = Buffer.alloc(4 + typeBuf.length + 4 + payloadBuf.length);
    buf.writeInt32LE(typeBuf.length, 0);
    typeBuf.copy(buf, 4);
    buf.writeInt32LE(payloadBuf.length, 4 + typeBuf.length);
    payloadBuf.copy(buf, 4 + typeBuf.length + 4);
    return buf;
}

function unpackMessages(buffer) {
    const messages = [];
    let offset = 0;
    while (buffer.length - offset >= 8) {
        const typeLen = buffer.readInt32LE(offset);
        if (buffer.length - offset < 4 + typeLen + 4) break;
        const type = buffer.subarray(offset + 4, offset + 4 + typeLen).toString();
        const payloadLen = buffer.readInt32LE(offset + 4 + typeLen);
        if (buffer.length - offset < 4 + typeLen + 4 + payloadLen) break;
        const payload = buffer.subarray(offset + 4 + typeLen + 4, offset + 4 + typeLen + 4 + payloadLen);
        messages.push({ type, payload });
        offset += 4 + typeLen + 4 + payloadLen;
    }
    return { messages, remaining: buffer.subarray(offset) };
}

class NamedPipeServer {
    constructor(messageBus, pipeName = 'PCA', verbose = false) {
        this.messageBus = messageBus;
        this.pipeName = pipeName;
        this.server = null;
        this.connections = [];
        this.handlers = {};
        this.verbose = verbose;
    }

    on(type, handler) {
        this.handlers[type] = handler;
    }

    _handleMessage(type, payload, socket) {
        if (this.handlers[type]) {
            this.handlers[type](payload, socket);
        } else {
            console.warn('[NamedPipeServer] Unhandled message type:', type);
        }
    }

    getPipePath() {
        if (os.platform() === 'win32') {
            return `\\\\.\\pipe\\${this.pipeName}`;
        } else {
            // Unix: use /tmp/PCA.sock
            return path.join('/tmp', `${this.pipeName}.sock`);
        }
    }

    start() {
        const pipePath = this.getPipePath();
        // Clean up old socket file on Unix
        if (os.platform() !== 'win32') {
            if (fs.existsSync(pipePath)) {
                try { fs.unlinkSync(pipePath); } catch (e) { /* ignore if not exist */ }
            }
        }
        try {
            this.server = net.createServer((socket) => {
                this.connections.push(socket);
                this._log('[NamedPipeServer] New client connection');
                let buffer = Buffer.alloc(0);
                socket.on('data', (data) => {
                    try {
                        buffer = Buffer.concat([buffer, data]);
                        const result = unpackMessages(buffer);
                        for (const msg of result.messages) {
                            this._log(`[NamedPipeServer] Message received: type='${msg.type}', payloadLen=${msg.payload.length}`);
                            this._handleMessage(msg.type, msg.payload, socket);
                        }
                        buffer = result.remaining;
                    } catch (err) {
                        console.error('[NamedPipeServer] Exception in socket data handler:', err);
                    }
                });
                socket.on('close', () => {
                    this._log('[NamedPipeServer] Client connection closed');
                    this.connections = this.connections.filter(s => s !== socket);
                });
                socket.on('error', (err) => {
                    console.error('[NamedPipeServer] Socket error:', err);
                });
            });
            this.server.on('error', (err) => {
                console.error('[NamedPipeServer] Server error:', err);
            });
            this.server.listen(pipePath);
        } catch (err) {
            console.error('[NamedPipeServer] Exception starting NamedPipeServer:', err);
        }
    }

    stop() {
        if (this.server) {
            this.server.close();
            this.server = null;
        }
        this.connections.forEach(s => s.destroy());
        this.connections = [];
    }

    send(socket, type, payload) {
        const buf = packMessage(type, payload);
        socket.write(buf);
        this._log(`[NamedPipeServer] Message sent: type='${type}', payloadLen=${Buffer.isBuffer(payload) ? payload.length : Buffer.from(payload).length}`);
    }

    _log(...args) {
        if (this.verbose) {
            console.log(...args);
        }
    }
}


class NamedPipeClient {
    constructor(messageBus, pipeName = 'PCA-portal', verbose = false) {
        this.messageBus = messageBus;
        this.pipeName = pipeName;
        this.verbose = verbose;
        this.client = null;
        this.buffer = Buffer.alloc(0);
        this.handlers = {};
    }

    getPipePath() {
        if (os.platform() === 'win32') {
            return `\\\\.\\pipe\\${this.pipeName}`;
        } else {
            // Unix: use /tmp/PCA.sock
            return path.join('/tmp', `${this.pipeName}.sock`);
        }
    }

    connect(onConnect) {
        const pipePath = this.getPipePath();
        this.client = net.createConnection(pipePath, () => {
            this._log('[NamedPipeClient] Connected to server');
            if (onConnect) onConnect();
        });
        this.client.on('data', (data) => {
            this.buffer = Buffer.concat([this.buffer, data]);
            const result = unpackMessages(this.buffer);
            for (const msg of result.messages) {
                this._log(`[NamedPipeClient] Message received: type='${msg.type}', payloadLen=${msg.payload.length}`);
                if (this.handlers[msg.type]) {
                    this.handlers[msg.type](msg.payload);
                }
            }
            this.buffer = result.remaining;
        });
        // Only log errors if this.client is a real object
        this.client.on('error', (err) => {
            if (this.client.readyState === 'open') {
                console.error('[NamedPipeClient] Client error:', err);
            } else {            
                // Client is not connected
                this.messageBus.fire('IPCClientNotConnected');
            }
        });
    }

    on(type, handler) {
        this.handlers[type] = handler;
    }

    send(type, payload) {
        if (!this.client) throw new Error('Client not connected');
        const buf = packMessage(type, payload);
        this.client.write(buf);
        this._log(`[NamedPipeClient] Message sent: type='${type}', payloadLen=${Buffer.isBuffer(payload) ? payload.length : Buffer.from(payload).length}`);
    }

    close() {
        if (this.client) {
            this.client.end();
            this.client = null;
        }
    }

    _log(...args) {
        if (this.verbose) {
            console.log(...args);
        }
    }
}

async function handleNewFilePathIPC(messageBus, filePath) {
    // Step 1: Register with IPC-Controller
    let newPipeName = null;
    let controllerClient;
    try {
        controllerClient = new NamedPipeClient(messageBus, 'IPC-Controller', false);
        await new Promise((resolve) => {
            let resolved = false;
            controllerClient.on('register_client_ack', (payload) => {
                newPipeName = payload.toString();
                controllerClient.close();
                if (!resolved) { resolved = true; resolve(); }
            });
            controllerClient.connect(() => {
                controllerClient.client.on('error', () => {
                    controllerClient.close();
                    if (!resolved) { resolved = true; resolve(); }
                });
                controllerClient.send('register_client', Buffer.from('portal', 'utf8'));
            });
            setTimeout(() => {
                controllerClient.close();
                if (!resolved) { resolved = true; resolve(); }
            }, 1000);
        });
    } catch (err) {
        if (controllerClient) controllerClient.close();
        return;
    }
    // Step 2: Send new_file_path to new pipe
    let moduleClient;
    try {
        moduleClient = new NamedPipeClient(messageBus, newPipeName, false);
        await new Promise((resolve) => {
            let resolved = false;
            moduleClient.on('new_file_path_ack', (payload) => {
                moduleClient.close();
                if (!resolved) { resolved = true; resolve(); }
            });
            moduleClient.connect(() => {
                moduleClient.client.on('error', () => {
                    moduleClient.close();
                    if (!resolved) { resolved = true; resolve(); }
                });
                moduleClient.send('new_file_path', Buffer.from(filePath, 'utf8'));
            });
            setTimeout(() => {
                moduleClient.close();
                if (!resolved) { resolved = true; resolve(); }
            }, 1000);
        });
    } catch (err) {
        if (moduleClient) moduleClient.close();
        return;
    }
}

async function createPCCPipeServer(messageBus, pipe_name, verbose = false) {
    const server = new NamedPipeServer(messageBus, pipe_name, verbose);

    server.on(MESSAGES.MSG_NEW_FILE_PATH, (payload, socket) => {
        const filePath = payload.toString();
        console.log('Received new file path:', filePath);
        let errorCode = 0;
        if (typeof filePath !== 'string' || !filePath.toLowerCase().endsWith('.cine')) {
            errorCode = ERRORS.ERROR_CODE_INVALID_FILE_PATH;
        } else {
            messageBus.fire('updateCinePath', filePath);
        }
        const ackPayload = Buffer.alloc(4);
        ackPayload.writeInt32LE(errorCode, 0);
        server.send(socket, MESSAGES.MSG_NEW_FILE_PATH_ACK, ackPayload);
        // --- Begin IPC client chain for registering and sending new_file_path ---
        handleNewFilePathIPC(messageBus, filePath);
        // --- End IPC client chain ---
    });

    server.on(MESSAGES.MSG_LIST_MODULES, async (payload, socket) => {
        try {
            const modules = await messageBus.query('refreshModules', null);
            // If modules is already a string, use it directly. If not, stringify it.
            const responsePayload = Buffer.from(JSON.stringify(modules), 'utf8');
            server.send(socket, MESSAGES.MSG_LIST_MODULES_ACK, responsePayload);
        } catch (error) {
            console.error('[NamedPipeServer] Error handling MSG_LIST_MODULES:', error);
            const ackPayload = Buffer.alloc(4);
            ackPayload.writeInt32LE(ERRORS.ERROR_CODE_GENERIC, 0);
            server.send(socket, MESSAGES.MSG_LIST_MODULES_ACK, ackPayload);
        }
    });

    server.start();
    return server;
}



module.exports = { createPCCPipeServer, NamedPipeServer, NamedPipeClient, MESSAGES, ERRORS };