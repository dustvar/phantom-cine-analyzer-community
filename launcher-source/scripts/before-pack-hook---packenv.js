const child_process = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('node:path');

function getCondaPath() {
    let conda_path = ''
    let env_cfg = {}
    try {
        env_cfg = JSON.parse(fs.readFileSync("config.json"))
    } catch (e) {
        env_cfg = {}
    }
    let obj = { conda_path: '', search_paths: [] }
    if (env_cfg['anaconda-path']) {
        conda_path = env_cfg['anaconda-path']
    }
    if (fs.existsSync(conda_path)) {
        obj.conda_path = conda_path
        obj.search_paths = [conda_path]
        return obj;
    } else {
        const user_home = os.homedir()
        let path_list = []
        const platform = process.platform
        if (platform === 'win32') {
            // Windows paths
            const user_mf = path.join(user_home, 'miniforge3')
            const user_mc = path.join(user_home, 'miniconda3')
            const user_ac = path.join(user_home, 'anaconda3')
            const user_mf2 = path.join(user_home, 'AppData', 'Local', 'miniforge3')
            const user_mc2 = path.join(user_home, 'AppData', 'Local', 'miniconda3')
            const user_ac2 = path.join(user_home, 'AppData', 'Local', 'anaconda3')
            const admin_mf = 'C:\\ProgramData\\miniforge3'
            const admin_mc = 'C:\\ProgramData\\miniconda3'
            const admin_ac = 'C:\\ProgramData\\anaconda3'
            path_list = [user_mf, user_mf2, admin_mf, user_mc, user_mc2, admin_mc, user_ac, user_ac2, admin_ac]
        } else if (platform === 'darwin') {
            // MacOS paths
            const user_mf = path.join(user_home, 'miniforge3')
            const user_mc = path.join(user_home, 'miniconda3')
            const user_ac = path.join(user_home, 'anaconda3')
            const user_opt_mf = '/opt/miniforge3'
            const user_opt_mc = '/opt/miniconda3'
            const user_opt_ac = '/opt/anaconda3'
            path_list = [user_mf, user_mc, user_ac, user_opt_mf, user_opt_mc, user_opt_ac]
        } else {
            // Linux paths
            const user_mf = path.join(user_home, 'miniforge3')
            const user_mc = path.join(user_home, 'miniconda3')
            const user_ac = path.join(user_home, 'anaconda3')
            const usr_local_mf = '/usr/local/miniforge3'
            const usr_local_mc = '/usr/local/miniconda3'
            const usr_local_ac = '/usr/local/anaconda3'
            const home_dev_mf = '/home/dev/miniforge3'
            const home_dev_mc = '/home/dev/miniconda3'
            const home_dev_ac = '/home/dev/anaconda3'
            path_list = [user_mf, user_mc, user_ac, usr_local_mf, usr_local_mc, usr_local_ac, home_dev_mf, home_dev_mc, home_dev_ac]
        }
        for (let i = 0; i < path_list.length; i++) {
            p = path_list[i];
            if (fs.existsSync(p)){
                conda_path = p
                break
            }
        }
        obj.conda_path = conda_path
        obj.search_paths = path_list
        return obj
    }
}

exports.default = async function(context) 
{
    console.log("\n\n========================== Getting Track&Measure Module ============================\n\n");
    
    // Prefer the public-repository copy of Track & Measure. A different source
    // repository can be supplied explicitly for internal build systems without
    // embedding private hosts or credentials in this source tree.
    const rootDir = path.resolve(__dirname, '..');
    const localModuleDir = path.resolve(rootDir, '../app-source/modules/trackmeasure');
    const repoDir = path.join(rootDir, "temp/trackmeasure_repo");
    const tempDir = path.join(rootDir, "temp");
    const destDir = path.join(rootDir, "modules/trackmeasure");
    let srcDir = localModuleDir;

    if (!fs.existsSync(localModuleDir)) {
        const repoUrl = process.env.TRACKMEASURE_REPO_URL;
        if (!repoUrl) {
            throw new Error(
                `Track & Measure source was not found at ${localModuleDir}. ` +
                'Set TRACKMEASURE_REPO_URL to use an alternate source repository.'
            );
        }
        console.log(`Cloning the configured Track & Measure repository to ${repoDir} ...`);
        fs.rmSync(repoDir, { recursive: true, force: true });
        child_process.execFileSync(
            'git', ['clone', '--depth', '1', repoUrl, repoDir], { stdio: 'inherit' }
        );
        srcDir = fs.existsSync(path.join(repoDir, 'src'))
            ? path.join(repoDir, 'src')
            : repoDir;
    }

    // Copy src to build location
    if (fs.existsSync(srcDir)) {
        // Remove old destDir if exists
        if (fs.existsSync(destDir)) {
            fs.rmSync(destDir, { recursive: true, force: true });
        }
        fs.mkdirSync(destDir, { recursive: true });
        // Copy all files from srcDir to destDir
        const ncp = require('ncp').ncp;
        await new Promise((resolve, reject) => {
            ncp.limit = 16;
            ncp(srcDir, destDir, function (err) {
                if (err) {
                    reject(err);
                } else {
                    resolve();
                }
            });
        });
        console.log(`Copied Track & Measure source from ${srcDir} to ${destDir}`);
    } else {
        console.error(`ERROR: Track & Measure source directory not found at ${srcDir}.`);
        throw new Error('Track & Measure source not found');
    }

    // Sanity check: ensure destDir exists and contains at least one file
    try {
        if (!fs.existsSync(destDir) || fs.readdirSync(destDir).length === 0) {
            throw new Error('modules/trackmeasure is empty after clone');
        }
    } catch (err) {
        console.error('ERROR: sanity check failed for modules/trackmeasure:', err.message);
        throw err;
    }

    // Remove the temp directory
    if (fs.existsSync(tempDir)) {
        try {
            fs.rmSync(tempDir, { recursive: true, force: true });
            console.log(`Deleted temp directory at ${tempDir}`);
        } catch (err) {
            console.log('Error deleting temp directory:', err.message);
        }
    }
    console.log("Cleanup completed successfully!\n\n");
    
    console.log("\n\n==========================Downloading Python Packages as Wheels============================\n\n");
    
    const wheelsDir = path.join(rootDir, "wheels");
    const pipsFile = path.join(rootDir, "config", "PIPS.txt");
    
    if (!fs.existsSync(wheelsDir)) {
        fs.mkdirSync(wheelsDir, { recursive: true });
        console.log(`Created wheels directory at ${wheelsDir}`);
    } else {
        fs.rmSync(wheelsDir, { recursive: true, force: true });
        fs.mkdirSync(wheelsDir, { recursive: true });
        console.log(`Cleaned and recreated wheels directory at ${wheelsDir}`);
    }
    
    if (fs.existsSync(pipsFile)) {
        console.log(`Downloading wheels from requirements in ${pipsFile}...`);
        
        const condaInfo = getCondaPath();
        if (condaInfo.conda_path) {
            const platform = process.platform;
            let pipCommand;
            
            if (platform === 'win32') {
                pipCommand = `"${path.join(condaInfo.conda_path, 'Scripts', 'pip.exe')}"`;
            } else {
                pipCommand = `"${path.join(condaInfo.conda_path, 'bin', 'pip')}"`;
            }
            
            const pipPath = platform === 'win32' ? 
                path.join(condaInfo.conda_path, 'Scripts', 'pip.exe') : 
                path.join(condaInfo.conda_path, 'bin', 'pip');
                
            if (!fs.existsSync(pipPath)) {
                console.log("Conda pip not found, using system pip...");
                pipCommand = 'pip';
            }
            
            try {
                //download wheels
                const downloadCommand = `${pipCommand} download -r "${pipsFile}" -d "${wheelsDir}" --no-deps`;
                console.log(`Running: ${downloadCommand}`);
                
                child_process.execSync(downloadCommand, { 
                    stdio: 'inherit',
                    cwd: rootDir 
                });
                
                const wheelFiles = fs.readdirSync(wheelsDir).filter(file => file.endsWith('.whl'));
                console.log(`Successfully downloaded ${wheelFiles.length} wheel files:`);
                wheelFiles.forEach(file => console.log(`  - ${file}`));
                
            } catch (err) {
                console.error("Error downloading wheels:", err.message);
                console.log("Continuing without wheels...");
            }
        } else {
            console.log("No conda installation found, skipping wheel download...");
        }
    } else {
        console.log(`PIPS.txt not found at ${pipsFile}, skipping wheel download...`);
    }
    
    console.log("Wheel download completed!\n\n");
    
    console.log("\n\n==========================Creating and Packing PCA environment============================\n\n");

    //runner is waiting for user input, so it needs a timeout
    const readline = require('readline');

    const userInput = await new Promise(resolve => {
        const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
        let settled = false
        const timer = setTimeout(() => { if (!settled) { settled = true; rl.close(); resolve('default'); } }, 5000)

        rl.question('Do you want to create the PCA environment? (y/n): ', answer => {
            if (!settled) { settled = true; clearTimeout(timer); rl.close(); resolve(answer); } })
    });

    if (userInput === 'default') {
        console.log("No input received. Proceeding with environment creation (CI/non-interactive mode)...");
    } else if (userInput.toLowerCase() === 'y' || userInput.toLowerCase() === 'yes') {
        console.log("Proceeding with environment creation...");
    } else {
        console.log("Environment creation cancelled by user.");
        return;
    }

    const platform = process.platform
    let scriptFile, command, argument
    argument = getCondaPath().conda_path

    if (!argument) {
        console.log("No conda install exists! Skipping environment packing...");
        return;
    }

    if (platform === 'win32') {
        scriptFile = path.join(__dirname, 'create-PCA-env.bat')
        command = `cmd /c "\"${scriptFile}\" \"${argument}\""`
    } else {
        scriptFile = path.join(__dirname , 'create-PCA-env.sh')
        command = `bash "${scriptFile}" "${argument}"`
    }

    console.log(`Child Process Spawned: ${scriptFile}\n`)
    try {
        const output = child_process.execSync(command, { encoding: 'utf-8', stdio: 'inherit' })
        if (output) console.log(`${output}`)
        const pcaTarPath = path.join(rootDir, 'env', 'PCA.tar.gz');
        if (fs.existsSync(pcaTarPath)) {
            console.log(`PCA environment package created successfully at ${pcaTarPath}`);
        } else {
            console.log("PCA.tar.gz was not created, but continuing with build...");
        }
    } catch (err) {
        console.error("Error running environment creation script:", err.message);
    }
};
