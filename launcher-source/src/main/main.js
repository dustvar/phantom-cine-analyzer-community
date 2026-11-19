const { app, dialog } = require('electron');
const config = require('./configManager');
const { jobLaunchSpec, openJob } = require('./jobOpener');
const { createPCCPipeServer } = require('./messageServer');

const WindowManager = require('./windowManager');
const EnvManager = require('./envManager');
const IpcHandlers = require('./ipcHandlers');
const MessageBus = require('./messageBus');

let pccPipeServer = null;
let jobEnvironment = null;
const pendingJobs = [];
function launchPendingJobs() {
    if (!jobEnvironment) return;
    while (pendingJobs.length) {
        const file = pendingJobs.shift();
        try {
            const { conda_path } = jobEnvironment.getCondaPath();
            const spec = jobLaunchSpec(file, conda_path, config.paths.defaultModuleDir, config.paths.envName || 'PCA');
            openJob(spec, error => dialog.showErrorBox('Open Phantom job', error.message));
        } catch (error) {
            dialog.showErrorBox('Open Phantom job', error.message);
        }
    }
}
// macOS may deliver the document before Electron's ready event.
app.on('open-file', (event, file) => {
    event.preventDefault();
    if (file.toLowerCase().endsWith('.pcajob')) {
        pendingJobs.push(file);
        launchPendingJobs();
    }
});
if (process.platform !== 'darwin') {
    pendingJobs.push(...process.argv.slice(1).filter(arg => arg.toLowerCase().endsWith('.pcajob')));
}
const messageBus = new MessageBus();

app.whenReady().then(async () => {
    const ipcHandlers = new IpcHandlers(messageBus);
    const envManager = new EnvManager(messageBus);
    jobEnvironment = envManager;
    launchPendingJobs();
    const windowManager = new WindowManager(messageBus);

    ipcHandlers.registerIpcHandlers({
        addModule: envManager.addModule.bind(envManager),
        openDoc: windowManager.openDoc.bind(windowManager),
        refreshModules: envManager.refreshModules.bind(envManager),
        envPipList: envManager.envPipList.bind(envManager),
        condaList: envManager.condaList.bind(envManager),
        handleLaunchModule: envManager.handleLaunchModule.bind(envManager),
        onConsoleInput: envManager.onConsoleInput.bind(envManager),
        openCineFileDialog: windowManager.openCineFileDialog.bind(windowManager)
    });

    // Start the message server and pass the main window if needed
    pccPipeServer = await createPCCPipeServer(messageBus, 'PCA-portal');
    windowManager.createWindow();
}).catch(err => {
    console.error('Error during app initialization:', err);
});

app.on('before-quit', () => {
    if (pccPipeServer) pccPipeServer.stop();
});