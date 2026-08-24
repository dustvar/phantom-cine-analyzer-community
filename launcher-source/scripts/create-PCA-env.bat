set conda-path=%1

echo Using Conda Path: "%conda-path%"

:: run in the conda environment provided
call "%conda-path%\Scripts\activate.bat" "%conda-path%"

call conda env remove -n PCA -y
echo Removing PCA Environment if it exists...
echo Creating PCA Environment...

:: new conda environment based on the requirements_miniforge-MIN.yml
call conda activate base
call python -m pip cache purge
call conda env create -v -f "%cd%\config\requirements.yml"
call conda activate PCA
call python -m pip install pywin32
call conda activate base
echo PCA Environment Created!


echo Packing Environment...

:: delete env first if it exists
IF EXIST env ( rmdir /S /Q env )
mkdir env

:: Pack the environment with conda-pack
set conda-user-path="%USERPROFILE%\.conda"
if exist "%conda-path%\envs\PCA" (
    call conda pack -p "%conda-path%\envs\PCA" -o env\PCA.tar.gz
) else if exist "%conda-user-path%\envs\PCA" (
    call conda pack -p "%conda-user-path%\envs\PCA" -o env\PCA.tar.gz
) else (
    echo ERROR: Could not find PCA environment to pack.
    exit /b 1
)
        
echo Environment Packed!