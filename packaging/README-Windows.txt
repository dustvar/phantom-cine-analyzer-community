PHANTOM CINE ANALYZER — ADDITIONAL FEATURES 1.14.3
Windows 10/11, 64-bit Intel or AMD

1. Press Windows+R, enter %USERPROFILE%, and make a folder named PCA there.
   Copy this ZIP into that local folder, outside a OneDrive-backed Desktop.
2. Right-click the ZIP and choose Extract All. Keep the whole extracted folder.
3. Double-click Check Installation.cmd. It checks the libraries, GUI and video
   encoder and tells you where it saved its report.
4. Double-click Start PCA.cmd. Choose Cines, or open an existing .pcajob job.

For a PCA icon directly on your Desktop, run Create PCA Desktop Icon.cmd once
after placing the extracted folder where you want it. You can then open that
icon instead of Start PCA.cmd. It uses the updated icon with the letters PCA.
Keep the complete extracted folder in place; the icon points to that folder.

Python and the required libraries are included. There is no separate Conda/Python
installation, administrator setup or first-run download. The folder can be moved;
do not separate Start PCA.cmd from its app and runtime folders.

This Windows package was assembled and checked for Windows x64 binaries on a Mac.
It has NOT yet been run on a Windows PC. Before using it with a customer, run the
installation check, load a representative Cine, play/scrub it, track an object,
save and reopen a job, and export a short MP4. Share any failure report for repair.

This package targets Intel/AMD Windows PCs. ARM Windows is not validated.
Your employer's policy may require IT approval for a custom/unsigned application.
Use the normal IT approval process if it is blocked; do not disable protections.

Settings/logs are stored in:
  %LOCALAPPDATA%\PhantomCineAnalyzer
Logs:
  logs\startup.log
  logs\installation-check.json

JOBS AND FOOTAGE
Copy .pcajob job files AND their original Cine/video files to the work PC. Jobs
reference media rather than embedding it; use Relink if the media location changed.
The Mac .app cannot run on Windows. Use this Windows folder on the work PC.
This transfer contains the application, not your recordings or personal jobs.
It has the same 1.14.3 features as the current Mac copy; see What is included.txt.

LICENSES AND BUILD DETAILS
Original PCA terms are in LICENSE.txt. Dependency license files accompany their
packages under runtime\Lib\site-packages. BUILD-DETAILS.json records the package
versions, archive hashes and architecture checks. This is a community-modified
PCA application, not an official new Vision Research/AMETEK software release.

UPDATING A WORKING 1.14.1 OR 1.14.2 INSTALLATION
The small PCA Windows Update 1.14.3 ZIP reuses its runtime. Keep the existing
runtime folder and follow UPDATE - START HERE.txt. For new tracking controls,
read Tracking tools guide.txt. The full portable ZIP is for a fresh installation.
