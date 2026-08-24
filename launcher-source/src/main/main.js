const { app } = require('electron');
const { createPCCPipeServer } = require('./messageServer');

const WindowManager = require('./windowManager');
const EnvManager = require('./envManager');
const IpcHandlers = require('./ipcHandlers');
const MessageBus = require('./messageBus');

let pccPipeServer = null;
const messageBus = new MessageBus();

app.whenReady().then(async () => {
    const ipcHandlers = new IpcHandlers(messageBus);
    const envManager = new EnvManager(messageBus);
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