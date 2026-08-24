
const path = require('node:path');
const fs = require('fs');
const os = require('os');
const { dialog } = require('electron');
const config = require('./configManager')

class EnvManager {
    constructor(messageBus) {
        this.messageBus = messageBus;

        this.tmpDir = path.join(require('os').tmpdir(), 'PhantomCineAnalyzer');
        this.pip_reqs = 'config/pips.txt';
        this.pyArgsFile = 'args.json';
        this.pythonExec = config.flags.isLinux ? 'python3' : 'python';

        this.cmd = null;
        this.initCmdShell();

        // message handlers
        this.messageBus.on('environmentInit', () => this.environmentInit());
        this.messageBus.on('refreshModules', (payload, callback) => {
            const result = this.refreshModules();
            if (callback) callback(result);
        });
    }

    addModule() {
        dialog.showOpenDialog({ properties: ['openDirectory', 'multiSelections'] })
            .then(result => {
                if (result.canceled || !result.filePaths || result.filePaths.length === 0) {
                    return;
                }
                // always get latest config
                var mods = config.moduleDirectories;
                if (mods != undefined) {
                    mods.push(...result.filePaths)
                    config.moduleDirectories = [...new Set(mods)];
                }
                else {
                    // doesn't exist, add new folder + default directory
                    result.filePaths.push(config.paths.defaultModuleDir)
                    config.moduleDirectories = [...new Set(result.filePaths)];
                }
                // force refreshModules
                this.refreshModules();
            });
    }

    async environmentInit() {
        const val = this.getCondaPath();
        const conda_path = val.conda_path;
        if (fs.existsSync(conda_path)) {
            console.log(`Using Conda installation: ${conda_path} \n`);
        } else {
            console.warn(`Conda application is not found. Please download it for free from "https://conda-forge.org/download" and install before continuing - restart CineAnalyzer.\nCineAnalyzer searched these locations:\n${val.search_paths.join('\n')}`);
            return;
        }

        const env_name = config.paths.envName;
        const env_reqs = config.paths.envRequirements;
        const conda_check_for_updates = config.flags.condaCheckForUpdates;

        console.log(`Activating conda env \"${env_name}\"... \n`);
        this.messageBus.fire('sendUI', { channel: 'change_element_state', args: ['runModule', 'disable'] });

        // create shell and register events
        const readySentinel = '__PCA_ENV_READY__';
        let activate;
        if (config.flags.isWin) {
            activate = 'Scripts\\activate.bat';
        } else {
            activate = 'bin/activate';
        }
        this.cmd.stdout.on('data', (d) => {
            const d_str = String(d);
            const regex = new RegExp(`${env_name}\\s+\\*`);
            const activationConfirmed = d_str.includes(readySentinel);
            const displayText = d_str.replaceAll(readySentinel, '').trim();
            if (displayText) {
                console.log(`${displayText} \n`);
            }
            if (activationConfirmed) {
                console.log(`PCA environment \"${env_name}\" is ready. \n`);
            }
            if (activationConfirmed || d_str.match(regex) || d_str.includes(`(${env_name})`)) {
                this.messageBus.fire('sendUI', { channel: 'change_element_state', args: ['runModule', 'enable'] });
            }
        });
        this.cmd.stderr.on('data', d => console.error(`${String(d)}`));
        this.cmd.on('error', (err) => console.error(`Error: ${err.stack}`));
        this.cmd.on('close', (code) => {
            if (code != 0) console.log(`Return code: ${code}`);
        });

        // activate base conda
        if (config.flags.isWin) {
            await this.cmd.stdin.write(`cd ${conda_path}\n`);
            await this.cmd.stdin.write(`${activate}\n`);
            await this.cmd.stdin.write(`cd ${config.paths.appDir}\n`);
        } else {
            await this.cmd.stdin.write(`source "${path.join(conda_path, activate)}"\n`);
            await this.cmd.stdin.write(`cd "${config.paths.appDir}"\n`);
        }
        if (conda_check_for_updates) {
            // in base env. create/update env_name
            console.log(`Checking for updates to the \"${env_name}\" env...`);
            await this.cmd.stdin.write(`conda env create -f "${env_reqs}"\n`);
            // update from yml file
            await this.cmd.stdin.write(`conda env update -f "${env_reqs}"\n`);
        }
        await this.cmd.stdin.write(`conda activate ${env_name} && echo ${readySentinel}\n`);

        if (conda_check_for_updates) {
            // update the pip packages
            await this.cmd.stdin.write(`${config.flags.isWin ? 'python' : 'python3'} -m pip install -r "${this.pip_reqs}"\n`);
        }
    }

    getCondaPath() {
        let obj = { conda_path: '', search_paths: [] };
        let conda_path = config.paths.anacondaPath;

        if (fs.existsSync(conda_path)) {
            obj.conda_path = conda_path;
            obj.search_paths = [conda_path];
            return obj;
        } else if (config.flags.isWin) {
            const user_home = os.homedir();
            const user_mf = path.join(user_home, 'miniforge3');
            const user_mc = path.join(user_home, 'miniconda3');
            const user_ac = path.join(user_home, 'anaconda3');
            const user_mf2 = path.join(user_home, 'AppData\\Local\\miniforge3');
            const user_mc2 = path.join(user_home, 'AppData\\Local\\miniconda3');
            const user_ac2 = path.join(user_home, 'AppData\\Local\\anaconda3');
            const admin_mf = 'C:\\ProgramData\\miniforge3';
            const admin_mc = 'C:\\ProgramData\\miniconda3';
            const admin_ac = 'C:\\ProgramData\\anaconda3';

            var path_list = [user_mf, user_mf2, user_mc, user_mc2, user_ac, user_ac2, admin_mf, admin_mc, admin_ac];
        } else {
            const { execSync } = require('child_process');
            let user_home = os.homedir();
            try {
                const condaPath = execSync('which conda 2>/dev/null', { encoding: 'utf8' }).trim();
                if (condaPath) {
                    const conda_directory = path.dirname(path.dirname(condaPath));
                    if (fs.existsSync(conda_directory)) {
                        console.log(`Found conda in PATH at: ${conda_directory} \n`);
                        obj.conda_path = conda_directory;
                        obj.search_paths = [conda_directory];
                        return obj;
                    }
                }
            } catch (e) {
                // Finder launches GUI apps with a minimal PATH. This is expected on
                // macOS, so falling back to the standard install locations is not
                // an error unless none of those locations contains Conda.
                console.log('Conda is not on the launcher PATH; checking standard install locations...');
            }
            if (config.flags.isLinux) { user_home = '/home/' + (process.env.USER); }
            if (config.flags.isMac) { user_home = '/Users/' + (process.env.USER); }
            var path_list = [
                path.join(user_home, 'miniforge3'),
                path.join(user_home, 'miniconda3'),
                path.join(user_home, 'anaconda3'),
                '/opt/miniforge3',
                '/opt/miniconda3',
                '/opt/anaconda3',
                '/usr/local/miniforge3',
                '/usr/local/miniconda3',
                '/usr/local/anaconda3'
            ];
        }
        for (let i = 0; i < (path_list || []).length; i++) {
            let p = path_list[i];
            if (fs.existsSync(p)) {
                conda_path = p;
                console.log(`Found conda at: ${conda_path} \n`);
                break;
            }
        }
        obj.conda_path = conda_path;
        obj.search_paths = path_list;
        return obj;
    }

    refreshModules() {
        let all_py_files = [];
        const moduleDirs = config.paths.moduleDirectories;
        for (const modDir of moduleDirs) {
            if (fs.existsSync(modDir)) {
                let dirs = fs.readdirSync(modDir, { withFileTypes: true })
                    .filter(dirent => dirent.isDirectory())
                    .map(dirent => dirent.name);
                dirs.push('');
                for (const d of dirs) {
                    let dir = path.join(modDir, d);
                    let moduleConfigPath = path.join(dir, 'config.json');
                    try {
                        let moduleJsonConfig = JSON.parse(fs.readFileSync(moduleConfigPath));
                        let fn = moduleJsonConfig.run_main;
                        let py_file = path.join(dir, `${fn}.py`);
                        let resolved_file = null;
                        if (fs.existsSync(py_file)) {
                            resolved_file = py_file;
                        } else {
                            console.error(`No valid file found for run_main: ${fn} in ${dir}`);
                            continue;
                        }
                        let n = fn;
                        if ('main_alias' in moduleJsonConfig) {
                            n = moduleJsonConfig.main_alias;
                        }
                        all_py_files.push({ path: resolved_file, name: n });
                    } catch (error) {
                        continue;
                    }
                }
            }
        }
        const unique_py_files = Array.from(
            new Map(all_py_files.map(f => [f.path, f])).values()
        );
        this.messageBus.fire('sendUI', { channel: 'update_module_list', args: [unique_py_files] });
        return unique_py_files;
    }

    runPythonScript(script, py_args, show_logs = true) {
        try {
            let py_args_obj = JSON.parse(py_args);
            py_args_obj.current_working_dir = path.dirname(script);
            py_args = JSON.stringify(py_args_obj);
        } catch (error) {
            py_args = '';
        }
        if (!fs.existsSync(this.tmpDir)) fs.mkdirSync(this.tmpDir);
        const py_args_path = path.join(this.tmpDir, this.pyArgsFile);
        fs.writeFileSync(py_args_path, py_args);
        this.messageBus.fire('sendUI', { channel: 'change_element_state', args: ['runModule', 'disable'] });

        const SENTINEL = 'Closing module';
        let pythonCmd;
        if(config.flags.isWin) {
            pythonCmd = `${this.pythonExec} "${script}" "${py_args_path}"`;
        } else {
            pythonCmd = `${this.pythonExec} "${script}" "${py_args_path}" ; echo ${SENTINEL}\n`;
        }

        const onStdout = (data) => {
            const text = String(data);
            if (text.includes(SENTINEL)) {
                // re-enable UI
                this.messageBus.fire('sendUI', { channel: 'change_element_state', args: ['runModule', 'enable'] });
                // remove this listener
                this.cmd.stdout.removeListener('data', onStdout);
            }
        };
        this.cmd.stdout.on('data', onStdout);
        this.cmd.stdin.write(pythonCmd + '\n');
        if (config.flags.isLinux || config.flags.isMac) {
            try {
                const configPath = path.join(path.dirname(script), 'config.json');
                const moduleConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'));
                const moduleName = moduleConfig.main_alias || path.basename(script, '.py');
                console.log(`Running module: ${moduleName} \n`);
            } catch (err) {
                const moduleName = path.basename(script, '.py');
                console.log(`Running module: ${moduleName} \n`);
            }
        }
    }

    handleLaunchModule(event, fn, mod_args) {
        this.runPythonScript(fn, mod_args);
    }

    initCmdShell() {
        // Spawn shell based on platform
        if (config.flags.isWin) {
            this.cmd = require('child_process').spawn('cmd');
        } else {
            const userShell = process.env.SHELL || '/bin/bash';
            this.cmd = require('child_process').spawn(userShell, config.flags.isLinux ? ['-l'] : [], { stdio: 'pipe' });
        }
    }

    async envPipList() {
        await this.cmd.stdin.write('pip list\n');
    }

    async condaList() {
        await this.cmd.stdin.write('conda env list\n');
    }

    onConsoleInput(evt, str) {
        this.cmd.stdin.write(`${str.trim()}\n`);
    }


}

module.exports = EnvManager;
