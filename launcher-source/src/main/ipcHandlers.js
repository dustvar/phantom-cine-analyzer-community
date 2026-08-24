// Handles all ipcMain registrations and handler functions as a class

const { ipcMain } = require('electron');


class IpcHandlers {
    constructor(messageBus) {
        this.messageBus = messageBus;

        // Override console.log and console.error to forward to renderer via messageBus
        const originalLog = console.log;
        const originalError = console.error;
        const originalWarn = console.warn;
        const originalInfo = console.info;

        console.log = (...args) => {
            if (this.messageBus) {
                this.messageBus.fire('sendUI', { channel: 'console_log', args: [{ type: 'log', args }] });
            }
            originalLog.apply(console, args);
        };

        console.warn = (...args) => {
            if (this.messageBus) {
                this.messageBus.fire('sendUI', { channel: 'console_log', args: [{ type: 'warn', args }] });
            }
            originalWarn.apply(console, args);
        };

        console.info = (...args) => {
            if (this.messageBus) {
                this.messageBus.fire('sendUI', { channel: 'console_log', args: [{ type: 'info', args }] });
            }
            originalInfo.apply(console, args);
        };

        console.error = (...args) => {
            if (this.messageBus) {
                this.messageBus.fire('sendUI', { channel: 'console_log', args: [{ type: 'error', args }] });
            }
            originalError.apply(console, args);
        };
    }

    registerIpcHandlers(handlers) {
        ipcMain.handle('refresh_modules', handlers.refreshModules);
        ipcMain.handle('env_pip_list', handlers.envPipList);
        ipcMain.handle('conda_list', handlers.condaList);
        ipcMain.handle('launch_module', (event, ...args) => handlers.handleLaunchModule(event, ...args));
        ipcMain.handle('console_input', handlers.onConsoleInput);
        ipcMain.handle('add_module', handlers.addModule);
        ipcMain.handle('openDoc', handlers.openDoc);
        ipcMain.handle('open_cine_file', handlers.openCineFileDialog);
    }
}

module.exports = IpcHandlers;