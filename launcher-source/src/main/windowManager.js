// Handles Electron window creation and management as a class

const { BrowserWindow, app, dialog, shell } = require('electron');
const path = require('node:path');
const fs = require('fs');
const config = require('./configManager');

class WindowManager {
    constructor(messageBus) {
        this.window = null;
        this.messageBus = messageBus;

        // message handlers
        if (this.messageBus) {
            this.messageBus.on('sendUI', (payload) => {
                if (this.window && this.window.webContents && payload && payload.channel) {
                    this.window.webContents.send(payload.channel, ...(payload.args || []));
                }
            });
        }

        if (this.messageBus) {
            this.messageBus.on('updateCinePath', (filePath) => {
                if (this.window && this.window.webContents) {
                    this.window.webContents.send('update_cine_path', filePath);
                }
            });

            this.messageBus.on('IPCClientNotConnected', () => {
                this.bringToFront();
            });
        }
    }

    createWindow() {
        const appDir = config.paths.appDir;
        const srcDir = config.paths.srcDir;
        const iconPath = config.flags.isDev
            ? path.resolve(appDir, 'public/images/portal.png')
            : path.join(app.getAppPath(), 'public/images/portal.png');
        const preloadPath = path.join(srcDir, 'preload/preload.js');
        const indexPath = path.join(srcDir, 'renderer/index.html');
        this.window = new BrowserWindow({
            width: 1000,
            height: 700,
            minWidth: 640,
            minHeight: 480,
            autoHideMenuBar: true,
            title: 'Phantom Cine Analyzer Playback v' + app.getVersion(),
            icon: iconPath,
            webPreferences: {
                preload: preloadPath
            }
        });
        // this.window.webContents.openDevTools();

        this.window.loadFile(indexPath)
            .then(() => { this.window.show(); })
            .then(() => { this.messageBus.fire('environmentInit'); })
            .then(() => { this.messageBus.fire('refreshModules'); });

        return this.window;
    }

    async openCineFileDialog() {
        const { canceled, filePaths } = await dialog.showOpenDialog(this.window, {
            properties: ['openFile', 'multiSelections'],
            filters: [{ name: 'Cine Files', extensions: ['cine'] }]
        });
        if (canceled || filePaths.length === 0) return null;
        if (filePaths.length > 4) {
            await dialog.showMessageBox(this.window, {
                type: 'warning',
                title: 'Too many Cine files',
                message: 'Select up to four Cine files.',
                detail: 'The first four selected files will be used.'
            });
        }
        return filePaths.slice(0, 4);
    }

    openDoc(event, filename) {
        const docPaths = [
            path.join(config.paths.appDir, 'docs', filename),
            path.join(config.paths.appDir, 'docs', 'docs_final', filename)
        ];
        const foundPath = docPaths.find(p => fs.existsSync(p));
        if (foundPath) {
            shell.openPath(foundPath);
        } else {
            if (this.window) {
                console.error(`Couldn't find document to open.\nName: ${filename}\nPaths tried: ${docPaths.join(' | ')}`);
            }
        }
    }

    bringToFront() {
        if (this.window && this.window.webContents) {
            this.window.show();
            this.window.focus();
        }
    }

}

module.exports = WindowManager;
