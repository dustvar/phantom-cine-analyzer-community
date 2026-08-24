const path = require('node:path');
const fs = require('fs');
const { app } = require('electron');

class ConfigManager {
    constructor() {
        // app paths and flags
        this._isWin = process.platform === 'win32';
        this._isMac = process.platform === 'darwin';
        this._isLinux = process.platform === 'linux';
        this._isDev = !app.isPackaged;
        this._appDir = this._isDev ? path.resolve(__dirname, '../..') : path.resolve(app.getAppPath(), '../..');
        this._srcDir = this._isDev ? path.join(this._appDir, 'src') : path.join(app.getAppPath(), 'src');
        this._defaultModuleDir = path.resolve(path.join(this._appDir, 'modules'));

        // json config data
        this._configPath = path.join(this._appDir, 'config/config.json');
        this._moduleDirectories = undefined;
        this._envName = undefined;
        this._envRequirements = undefined;
        this._condaCheckForUpdates = false;
        this._anacondaPath = '';

        this._load();
    }

    _load() {
        try {
            if (fs.existsSync(this._configPath)) {
                const jsonConfig = JSON.parse(fs.readFileSync(this._configPath, 'utf-8'));
                let dirs = jsonConfig['module-directories'];
                // Ensure dirs is an array
                if (!Array.isArray(dirs)) {
                    dirs = [];
                }
                // Always add defaultModuleDir if not present or if dirs is empty
                if (!dirs.includes(this._defaultModuleDir)) {
                    dirs.push(this._defaultModuleDir);
                }
                // Remove blanks and deduplicate
                dirs = [...new Set(dirs.filter(d => d && typeof d === 'string'))];
                this._moduleDirectories = dirs;
                
                this._envName = jsonConfig['env-name'];
                this._envRequirements = jsonConfig['env-requirements'];
                this._condaCheckForUpdates = jsonConfig['conda-check-for-updates'];
                this._anacondaPath = jsonConfig['anaconda-path'];
            }
        } catch (err) {
            console.error('Failed to load config.json:', err);
        }
    }

    get moduleDirectories() {
        return this._moduleDirectories;
    }

    set moduleDirectories(newDirs) {
        // Clean up: deduplicate and make absolute
        let cleaned = Array.isArray(newDirs)
            ? [...new Set(newDirs.map(d => path.isAbsolute(d) ? d : path.join(this._appDir, d)).filter(absPath => fs.existsSync(absPath)))]
            : [];
        this._moduleDirectories = cleaned;
        // Persist to disk
        let configData = {};
        try {
            if (fs.existsSync(this._configPath)) {
                configData = JSON.parse(fs.readFileSync(this._configPath, 'utf-8'));
            }
        } catch (err) {
            // If file doesn't exist or is invalid, start with empty config
        }
        configData['module-directories'] = cleaned;
        fs.writeFileSync(this._configPath, JSON.stringify(configData, null, 2));
    }

    get flags() {
        return {
            isWin: this._isWin,
            isMac: this._isMac,
            isLinux: this._isLinux,
            isDev: this._isDev,
            condaCheckForUpdates: this._condaCheckForUpdates
        };
    }

    get paths() {
        return {
            appDir: this._appDir,
            srcDir: this._srcDir,
            defaultModuleDir: this._defaultModuleDir,
            moduleDirectories: this._moduleDirectories,
            anacondaPath: this._anacondaPath,
            envRequirements: this._envRequirements,
            envName: this._envName
        };
    }
}

module.exports = new ConfigManager();
