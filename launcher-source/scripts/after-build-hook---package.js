const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

exports.default = async function(buildResult) {
    // afterAllArtifactBuild receives buildResult with artifactPaths array
    console.log('\n\n==========================Creating Distribution Package============================\n');

    const distDir = path.join(__dirname, '..', 'dist');
    const envDir = path.join(__dirname, '..', 'env');
    
    // Find the DMG file
    const dmgFiles = fs.readdirSync(distDir).filter(f => f.endsWith('.dmg'));
    
    if (dmgFiles.length === 0) {
        console.log('No DMG file found, skipping package creation');
        return;
    }

    const dmgFile = dmgFiles[0];
    const dmgBaseName = path.basename(dmgFile, '.dmg');
    const packageName = `${dmgBaseName}.zip`;
    
    console.log(`Creating package: ${packageName}`);
    console.log(`DMG: ${dmgFile}`);
    
    // Create package structure
    const packageDir = path.join(distDir, 'package-temp');
    const supportDir = path.join(packageDir, 'support');
    
    // Clean up any existing package directory
    if (fs.existsSync(packageDir)) {
        fs.rmSync(packageDir, { recursive: true, force: true });
    }
    
    // Create directories
    fs.mkdirSync(packageDir, { recursive: true });
    fs.mkdirSync(supportDir, { recursive: true });
    
    // Copy DMG
    console.log('Copying DMG...');
    fs.copyFileSync(
        path.join(distDir, dmgFile),
        path.join(packageDir, dmgFile)
    );
    
    // Copy PCA.tar.gz
    const pcaTarGz = path.join(envDir, 'PCA.tar.gz');
    if (fs.existsSync(pcaTarGz)) {
        console.log('Copying PCA.tar.gz to support directory...');
        fs.copyFileSync(
            pcaTarGz,
            path.join(supportDir, 'PCA.tar.gz')
        );
        
        // Clean up the source file after copying
        console.log('Cleaning up source PCA.tar.gz...');
        fs.unlinkSync(pcaTarGz);
    } else {
        console.log('Warning: PCA.tar.gz not found, package will not include Python environment');
    }
    
    // Create zip file
    console.log('Creating zip archive...');
    const zipPath = path.join(distDir, packageName);
    
    // Remove existing zip if present
    if (fs.existsSync(zipPath)) {
        fs.unlinkSync(zipPath);
    }
    
    // Use macOS's built-in zip command (works on mac, creates compatible zips)
    try {
        execSync(`cd "${packageDir}" && zip -r "../${packageName}" .`, { stdio: 'inherit' });
        console.log(`✅ Package created successfully: ${packageName}`);
    } catch (error) {
        console.error('Error creating zip:', error);
    }
    
    // Clean up temp directory
    console.log('Cleaning up temporary files...');
    fs.rmSync(packageDir, { recursive: true, force: true });
    
    console.log('\n==========================Package Creation Complete============================\n');
};
