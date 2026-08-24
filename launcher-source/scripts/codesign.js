// https://github.com/electron-userland/electron-builder/issues/4785
// signtool sign /v /debug /a /fd sha256 /tr http://sha256timestamp.ws.symantec.com/sha256/timestamp "myexe.exe"

'use strict'

const util = require('util')
const exec = util.promisify(require('child_process').exec)
const chalk = require('chalk')

const TimeStampServer = 'http://timestamp.digicert.com'

async function doSign (file) {

  let args = [
    'signtool',
    'sign',
    '/v',
    '/debug',
    '/a',
    '/fd',
    'sha256',
    '/td sha256',
    '/tr',
    TimeStampServer,
    `"${file}"`
  ]

  try {
    const { stdout } = await exec(args.join(' '))
    console.log(stdout)
  } catch (error) {
    console.info(`${chalk.red.bold('WARNING: ')} No key found, skipping code signing...`)
    // throw error
  }
}

exports.default = async function (config) {
  const filePath = config.path;
  // Only sign main app exe, installer, and custom helpers
  // Skip node_modules and known third-party binaries
  const lower = filePath.toLowerCase();
  const isExe = lower.endsWith('.exe');
  const isDll = lower.endsWith('.dll');
  const isMainApp = lower.includes('phantomcineanalyzer') && lower.endsWith('.exe');
  const isInstaller = lower.includes('setup') && isExe;
  const isUninstaller = lower.includes('uninstaller') && isExe;
  const isElevate = lower.endsWith('elevate.exe');
  const isInNodeModules = lower.includes('node_modules');
  const isThirdParty =
    lower.includes('7zip-bin') ||
    lower.includes('app-builder') ||
    lower.includes('electron.exe') ||
    lower.includes('dmg-builder');

  // Only sign if it's the main app, installer, uninstaller, or elevate helper
  if (
    (isMainApp || isInstaller || isUninstaller || isElevate) &&
    !isInNodeModules && !isThirdParty
  ) {
    console.info(`Signing ${chalk.green.bold(filePath)}`);
    await doSign(filePath);
  } else if (isMainApp || isInstaller || isUninstaller || isElevate) {
    // Allow signing these even if not filtered above
    console.info(`Signing ${chalk.green.bold(filePath)}`);
    await doSign(filePath);
  } else {
    console.info(`${chalk.yellow('Skipping signing')} ${filePath}`);
  }
}
