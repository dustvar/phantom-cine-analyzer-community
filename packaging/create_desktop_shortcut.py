"""Create a per-user Windows shortcut after the portable folder is in place."""
from pathlib import Path
import subprocess
import sys


def main():
    if sys.platform != 'win32':
        print('Run this command on your Windows PC after extracting the full folder.')
        return 1
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    root = Path(__file__).resolve().parent.parent
    executable = root/'runtime/pythonw.exe'
    entry = root/'app/desktop_bootstrap.py'
    # A versioned icon path prevents Explorer reusing the old shortcut artwork.
    icon = root/'app/images/icon-1.14.3.ico'
    if not icon.is_file():
        icon = root/'app/images/icon.ico'
    if not all(path.is_file() for path in (executable, entry, icon)):
        print('Extract the entire PCA ZIP first, keeping app and runtime together.')
        return 1
    try:
        from win32com.client import Dispatch
        shell = Dispatch('WScript.Shell')
        desktop = Path(shell.SpecialFolders('Desktop'))
        link = desktop/'PCA 1.14.3.lnk'
        # Preserve an existing shortcut, even if it belongs to another copy.
        index = 2
        while link.exists():
            link = desktop/f'PCA 1.14.3 ({index}).lnk'
            index += 1
        shortcut = shell.CreateShortcut(str(link))
        shortcut.TargetPath = str(executable)
        shortcut.Arguments = subprocess.list2cmdline(['-B', str(entry)])
        shortcut.WorkingDirectory = str(root/'app')
        shortcut.IconLocation = str(icon)+',0'
        shortcut.Description = 'Phantom Cine Analyzer 1.14.3'
        shortcut.Save()
        if not link.is_file():
            raise RuntimeError('Windows did not save the shortcut.')
        print('Created: '+str(link))
        print('Open this PCA icon to start. Keep the full PCA folder in its current location.')
        return 0
    except Exception as error:
        print('Could not create the icon: '+str(error))
        print('You can still open PCA using Start PCA.cmd inside the extracted folder.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
