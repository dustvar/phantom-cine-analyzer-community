// Launch job files with argument arrays so spaces and shell metacharacters are safe.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

function jobLaunchSpec(jobPath, condaPath, moduleDirectory, environment = 'PCA', platform = process.platform) {
    if (typeof jobPath !== 'string' || path.extname(jobPath).toLowerCase() !== '.pcajob') {
        throw new Error('Choose a Phantom job (.pcajob).');
    }
    const job = path.resolve(jobPath);
    if (!fs.statSync(job).isFile()) throw new Error('The job file is unavailable.');
    const prefix = path.isAbsolute(environment) ? environment : path.join(condaPath, 'envs', environment);
    const python = path.join(prefix, ...(platform === 'win32' ? ['python.exe'] : ['bin', 'python']));
    const script = path.join(moduleDirectory, 'trackmeasure', 'launch_app.py');
    if (!fs.existsSync(python)) throw new Error('The PCA Python environment is unavailable. Open the launcher to configure it first.');
    if (!fs.existsSync(script)) throw new Error('The Track & Measure module is unavailable.');
    return { python, args: [script, job], cwd: path.dirname(script) };
}

function openJob(spec, onError, spawnProcess = spawn) {
    const child = spawnProcess(spec.python, spec.args, { cwd: spec.cwd, detached: true, stdio: 'ignore', shell: false });
    child.on('error', onError);
    child.unref();
    return child;
}
module.exports = { jobLaunchSpec, openJob };
