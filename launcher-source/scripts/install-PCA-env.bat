@echo off
setlocal enabledelayedexpansion


REM Check if running as administrator
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Running with administrator privileges.
    set "is_admin=true"
) else (
    echo Running without administrator privileges.
    set "is_admin=false"
)


REM Get the directory where this script is located
set "script_dir=%~dp0"
echo Script directory: !script_dir!

REM Look for PCA.tar.gz in multiple possible locations
set "tar_file="
if exist "!script_dir!PCA.tar.gz" (
    set "tar_file=!script_dir!PCA.tar.gz"
    echo Found PCA.tar.gz at: !tar_file!
) else (
    exit /b 1
)

set "conda_directory="
for %%d in (
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\AppData\Local\miniforge3"
    "C:\ProgramData\miniforge3"
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\AppData\Local\miniconda3"
    "C:\ProgramData\miniconda3"
    "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\AppData\Local\Anaconda3"
    "C:\ProgramData\Anaconda3"
) do (
    if exist "%%d" (
        set "conda_directory=%%d"
        echo Found conda at: !conda_directory!
        goto :conda_found
    )
)
:conda_found

echo using directory: !conda_directory!

if not defined conda_directory (
    echo Error: Could not find a valid Anaconda/Miniconda installation.
    ::prompt for custom conda path
    set /p "conda_directory=Please enter the path to your root Conda installation(ex: C:\Users\YourUsername\miniforge3): "
)

REM Ensure envs directory exists
if not exist "!conda_directory!\envs" (
    echo Creating envs directory at: !conda_directory!\envs
    mkdir "!conda_directory!\envs"
    if !ERRORLEVEL! neq 0 (
        echo Error creating envs directory - attempting with admin privileges...
        if "!is_admin!" == "false" (
            echo Requesting administrator privileges to continue...
            powershell -Command "Start-Process '%~f0' -ArgumentList 'elevated' -Verb RunAs"
            exit /b 0
        ) else (
            echo Failed to create envs directory even with admin privileges!
            echo Directory: !conda_directory!\envs
            exit /b 1
        )
    )
) else (
    echo Envs directory already exists at: !conda_directory!\envs
)

REM Remove PCA env if it exists
if exist "!conda_directory!\envs\PCA" (
    echo Removing existing PCA environment...
    rmdir /s /q "!conda_directory!\envs\PCA" 2>nul
)

mkdir "!conda_directory!\envs\PCA"
if !ERRORLEVEL! neq 0 (
    echo Error: Could not create PCA environment directory
    exit /b 1
)

REM Extract environment directly to envs directory
echo Extracting PCA environment...
tar -xzf "!tar_file!" -C "!conda_directory!\envs\PCA" -v
if !ERRORLEVEL! neq 0 (
    echo Error: Failed to extract PCA environment
    exit /b 1
)

REM Apply permissions if conda is installed system-wide (ProgramData)
echo !conda_directory! | findstr /i "ProgramData" >nul
if !ERRORLEVEL! == 0 (
    echo Conda is system-wide, applying permissions for all users...
    takeown /f "!conda_directory!\envs\PCA" /r >nul 2>&1
    icacls "!conda_directory!\envs\PCA" /grant Users:F /T >nul 2>&1
)

echo PCA environment installed successfully.


endlocal