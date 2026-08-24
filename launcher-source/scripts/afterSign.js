const { notarize } = require('@electron/notarize');

exports.default = async function afterSign(context) {
  const { electronPlatformName, appOutDir } = context;
  
  console.log('🔧 afterSign script is running!');
  console.log('Platform:', electronPlatformName);
  
  // Only notarize on macOS
  if (electronPlatformName !== 'darwin') {
    console.log('Skipping notarization - not macOS platform');
    return;
  }

  // Check if we have the required environment variables
  if (!process.env.APPLE_ID || !process.env.APPLE_APP_SPECIFIC_PASSWORD || !process.env.APPLE_TEAM_ID) {
    console.log('Skipping notarization - missing Apple credentials');
    return;
  }

  const appName = context.packager.appInfo.productFilename;
  console.log('Starting notarization...');

  return await notarize({
    tool: 'notarytool',
    appPath: `${appOutDir}/${appName}.app`,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID,
  });
};