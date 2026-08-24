
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('API', {
    refreshModules: () => ipcRenderer.invoke('refresh_modules'),
    launchModule: (fn, args) => ipcRenderer.invoke('launch_module', fn, args),
    consoleInput: (str) => ipcRenderer.invoke('console_input', str),
    envPipList: () => ipcRenderer.invoke('env_pip_list'),
    condaList: () => ipcRenderer.invoke('conda_list'),
    addModule: () => ipcRenderer.invoke('add_module'),
    openDoc: (filename) => ipcRenderer.invoke('openDoc', filename),
    onDialogMsg: (cb) => ipcRenderer.on('dialog_msg', cb),
    onConsoleLog: (cb) => ipcRenderer.on('console_log', cb),
    onUpdateModuleList: (cb) => ipcRenderer.on('update_module_list', cb),
    onChangeElementState: (cb) => ipcRenderer.on('change_element_state', cb),
    onUpdateCinePath: (cb) => ipcRenderer.on('update_cine_path', cb),
    openCineFiles: () => ipcRenderer.invoke('open_cine_file'),
    // Kept for compatibility with older renderer builds.
    openCineFile: () => ipcRenderer.invoke('open_cine_file')
});
