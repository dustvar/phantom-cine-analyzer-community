InstallDir "$LOCALAPPDATA\Programs\PhantomCineAnalyzer"

!macro CustomInstall
  ; Install PCA environment
  SetOutPath "$INSTDIR"
  CreateDirectory "$INSTDIR\logs"
  ExecWait 'cmd /C "$INSTDIR\env\install-PCA-env.bat" > logs\install-out.log'

  ; Set the output path to the modules\trackmeasure directory
  SetOutPath "$INSTDIR\modules\trackmeasure"
  
  ; Check All Users installation
  StrCpy $0 "$INSTDIR"
  StrCpy $1 "$PROGRAMFILES64"
  StrLen $2 $1
  StrCpy $3 $0 $2
  StrCmp $3 $1 is_program_files continue_install
  
  is_program_files:
    ; Apply permissions for regular users for the module
    ExecWait 'icacls "$INSTDIR\modules\trackmeasure" /grant Users:F /T' $7
    ExecWait 'icacls "$INSTDIR\modules\trackmeasure" /grant Everyone:F /T' $8
    Goto continue_install

  continue_install:
  ; Check if the .condarc file exists in the user's profile directory
  IfFileExists "$PROFILE\.condarc" 0 add_line
  
  ; If the file does not exist, create it and add the line
  FileOpen $0 "$PROFILE\.condarc" w
  FileWrite $0 "ssl_verify: false$\n"
  FileClose $0
  
  ; If the file exists, add the line to the end of the file
  Goto done
  
  add_line:
  FileOpen $0 "$PROFILE\.condarc" a
  FileWrite $0 "ssl_verify: false$\n"
  FileClose $0
  

  done:
!macroend


!macro customWelcomePage
  !define MUI_WELCOMEPAGE_TEXT "Welcome to the Phantom Cine Analyzer installer.$\r$\n$\r$\nThis wizard will guide you through the installation process.$\r$\n$\r$\n*** IMPORTANT ***$\r$\nBefore proceeding, you must install an Anaconda environment tool. Vision Research recommends using Miniforge, which you can download here:$\r$\n$\r$\nhttps://conda-forge.org/download/$\r$\n$\r$\nPlease complete this step before continuing with the installation."
  !insertmacro MUI_PAGE_WELCOME
!macroend



