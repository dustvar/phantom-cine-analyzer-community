PCA 1.14.3 - SMALL WINDOWS UPDATE
For an existing, working PCA Windows Portable 1.14.1 or 1.14.2 installation.

This small download reuses the Python and libraries you already extracted.
Keep your existing PCA folder and its runtime folder. This ZIP cannot run alone.
If you no longer have that installation, use the full 1.14.3 portable ZIP instead.

INSTALL
1. Close PCA. Extract this small update ZIP into a separate local folder.
2. Open your EXISTING PCA Windows Portable 1.14.1 or 1.14.2 folder. You should see:
      app     runtime     Start PCA.cmd     Check Installation.cmd
3. In that existing folder, rename app to app-backup-before-1.14.3.
   Rename BUILD-DETAILS.json to BUILD-DETAILS-backup-before-1.14.3.json.
   If those backup names already exist, keep them and choose a new backup name.
4. Open the extracted UPDATE folder. Copy ALL its contents into your existing
   PCA folder. Allow Windows to replace the supporting text and command files.
   Copy the app folder itself, not just its contents. The result should have
   app and runtime side by side again, plus your app-backup-before-1.14.3 folder.
5. Run Check Installation.cmd from that existing folder. When it reports PASS,
   run Start PCA.cmd. The application should report version 1.14.3.
6. Run Create PCA Desktop Icon.cmd there to create the new PCA Desktop icon.
   The helper is app\create_desktop_shortcut.py; the new icon is
   app\images\icon-1.14.3.ico. Keep the entire existing PCA folder in place afterward.

Your folder may still have 1.14.1 or 1.14.2 in its name; its application is
now 1.14.3. You do not need to rename or move the folder. Your jobs, footage,
and personal settings are retained. Test playback and tracking before your
next customer demonstration, then delete the old app backup when satisfied.

ROLL BACK IF NEEDED
Close PCA. Rename the new app folder to app-1.14.3-unused, then rename your
app-backup-before-1.14.3 folder back to app. Rename the new BUILD-DETAILS.json to
BUILD-DETAILS-1.14.3-unused.json and restore your backed-up manifest to
BUILD-DETAILS.json. Run Check Installation.cmd and Start PCA.cmd again.
Keep runtime unchanged throughout. Choose unused backup names if necessary.

WHAT IS CHECKED
The update builder checks that the embedded Python archive and every dependency
wheel have the same names and hashes as each supported prior package
(1.14.1 and 1.14.2). It checks the new
application against its source manifest. See UPDATE-DETAILS.json for details.
This package was assembled on a Mac; native Windows operation still needs the
installation check and a short playback/tracking test on your work computer.
