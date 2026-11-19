# Native startup and Windows portability — 1.10.1

The upgraded analyzer now opens directly as a native Qt application, without the
old Electron module selector or an external Conda environment. The installed
original PCA application remains separate. All upgraded analysis/editor modules
use the same Python source on both platforms.

## Mac diagnosis and repair

The inspected Mac runs macOS 27.2 on Apple silicon. Both the previously installed
upgraded Electron executable and its external PCA Python were Intel x86_64 builds.
Their signatures verified, and Rosetta was available. This is consistent with
the reported update/incompatibility message: [Apple documents an Intel application
warning ahead of reduced Rosetta support in macOS 28](https://support.apple.com/en-us/102527).
The exact original dialog was not captured, so this is a supported diagnosis,
not a claim that a crash log proved the dialog's cause.

The replacement is an arm64 PyInstaller application with Python 3.12 and its
scientific/Qt libraries bundled. A splash appears before scientific imports.
Startup exceptions show a useful message and write a rotating diagnostic log.
Repeated launches forward document requests to the running application; Finder
can send `.pcajob` and `.cine` documents. The local socket is scoped to the user
profile. The Mac bundle is ad-hoc signed for this local installation; it is not
Developer ID signed or notarized for public distribution.

Settings, Matplotlib/Numba caches and logs live outside the signed application:

- macOS: `~/Library/Application Support/PhantomCineAnalyzer`
- Windows: `%LOCALAPPDATA%\PhantomCineAnalyzer`

`app_runtime.settings_path(seed=old_config)` migrates the prior installed settings
once. Existing valid user settings take priority. Malformed JSON is retained in
a timestamped backup before defaults are restored. Calibration and reports read
the same per-user settings as the UI. Original Cine files and jobs are unchanged.
`PCA_USER_DATA_DIR` allows an isolated development/test profile.
The bootstrap explicitly replaces PyInstaller's temporary Matplotlib cache path
before scientific imports. This prevents an expensive font scan on every launch.

## Rebuilding

Use a native Python 3.12 environment on Apple silicon. Install
`packaging/runtime-requirements.txt` and PyInstaller. The private pure-Python
`pyphantom_*` and `cinereader` packages are collected from the authorized existing
PCA environment with `scripts/collect_pca_vendor.py`; they are not public PyPI
dependencies. Its manifest records versions/hashes and retains license metadata.
Set `PYTHONPATH` and `PCA_VENDOR_SITE` to that vendor directory, then run:

```sh
python -m PyInstaller --noconfirm --distpath /path/to/dist --workpath /path/to/work packaging/pca.spec
```

Keep generated bundles, vendor copies and virtual environments outside the Git
repository. PyInstaller must run on the target OS/architecture; the Mac build is
not a Windows executable. Native Qt/scientific libraries are platform-specific.

## Windows package

`scripts/build_windows_portable.py` assembles an offline Windows x64 folder from
official CPython 3.13.17's embedded Windows AMD64 distribution, Windows binary
wheels for the pinned requirements plus `pywin32==311`, and the authorized vendor
directory. Obtain wheels using `pip download --platform win_amd64
--python-version 313 --implementation cp --abi cp313 --only-binary=:all:`.
Use a clean wheel directory; duplicate versions are rejected. The builder checks
PE machine types, archive paths, and records hashes in `BUILD-DETAILS.json`.
It never overwrites an existing output directory.

```sh
python scripts/build_windows_portable.py --output '/path/to/PCA Windows Portable' \
  --python-zip /path/to/python-3.13.17-embed-amd64.zip \
  --wheels /path/to/windows-wheels --vendor /path/to/vendor
```

This follows Python's supported
[embedded distribution/vendoring model](https://docs.python.org/3.13/using/windows.html#the-embeddable-package).
No separate Python/Conda installation or first-run download is required.
Copy the ZIP by flash drive, extract the entire folder on Windows, run **Check
Installation.cmd**, then **Start PCA.cmd**. Preserve the folder layout.
Copy original media alongside `.pcajob` files and relink moved footage as needed.
Employer application policies still apply to custom unsigned programs.

The Windows package is assembled on macOS and has not been executed on Windows.
Binary inspection is not an execution test. Before a customer demo, verify the
installation check, Cine load/playback, all needed trackers, save/reopen of a job,
and short MP4 export on the actual Windows PC. The diagnostic report is saved in
`logs/installation-check.json` under the per-user data directory.

## Verification

- Existing 256 unit/UI tests pass with the native arm64 Python environment.
- Startup tests cover settings migration/recovery, document arguments, queued
  Finder events, and actual cross-process single-instance forwarding.
- Opt-in `PCA_STARTUP_TEST_REPORT` writes a JSON report and a screenshot of the
  application's own window. `PCA_STARTUP_TEST_EXPORT=1` also exports and decodes
  six frames from the supplied Cine; `PCA_STARTUP_TEST_QUIT=1` closes after testing.
  These hooks are inactive during normal launches.
- The installed Mac application must be launch-tested with representative Cine
  footage and its signature checked again after use. Keep the previous bundle
  under the user's application-support Backups directory before replacing it.

Installed verification on 2026-10-27: 256 existing tests and five startup tests
passed. The frozen app loaded a reference Cine at 94,000 capture FPS, exported and
decoded six MP4 frames at 30 playback FPS, and opened a saved job through Finder
with its tracked object restored. All 577 bundled Mach-O files include arm64.
The installed signature remained valid after use, and repeated launch requests
reused the running app. Existing settings matched the prior installation exactly
at migration. The old upgraded bundle is retained under
`Backups/startup-20261027/Previous Upgraded PCA 1.10.0.app` in application support.
The Windows ZIP passed integrity/source checks and contains 402 checked x64
native modules; Windows execution remains unverified.

After correcting the persistent font-cache path, the measured repeat launch
showed the main window in 3.16 seconds and loaded the test Cine in 4.21 seconds.
The initial font-cache creation took about 18 seconds including footage loading;
it is completed for this Mac's installed profile. Times depend on machine/media.
