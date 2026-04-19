' YT Grab -- silent launcher (primary).
'
' Double-click this to start the app with NO visible console window.
' The browser opens automatically once the server is listening.
'
' On first run (when the Python venv hasn't been set up yet), this
' delegates to launch.bat so the user can see pip install progress.
' Once setup is done, every subsequent launch is fully silent.
'
' If the user wants to see server logs / Python output for debugging,
' they can run launch.bat directly -- both launchers coexist.

Option Explicit

Dim objShell, fso, cwd, pyw, server

Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Work relative to this script's own folder, regardless of where the
' user launches it from.
cwd = fso.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = cwd

pyw = cwd & "\venv\Scripts\pythonw.exe"
server = cwd & "\server.py"

' First-time setup: if the venv doesn't exist yet, run launch.bat in a
' visible window so the user sees "installing dependencies..." progress.
' The "1, True" arguments mean "show window, wait for exit."
If Not fso.FileExists(pyw) Then
    objShell.Run """" & cwd & "\launch.bat""", 1, True
End If

' Guard: if setup somehow didn't produce pythonw.exe, fall back to
' launch.bat (which will show the error). Don't silently fail.
If Not fso.FileExists(pyw) Then
    objShell.Run """" & cwd & "\launch.bat""", 1, False
    WScript.Quit
End If

' Start the server silently, detached. pythonw.exe has no console window
' by design. "0, False" means "hidden window, don't wait" -- we fire and
' forget; the server stays alive in the background, this VBS exits.
objShell.Run """" & pyw & """ """ & server & """", 0, False
