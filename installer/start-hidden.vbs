' Clayrune hidden launcher (Windows).
'
' Runs installer\start.bat with NO visible console window, so end users never
' see the Flask server's log console. This is the default end-user entry point
' (the Desktop / Start Menu shortcut targets this script via wscript.exe).
'
' Developers who want to watch logs live can run start.bat (or
' `python server.py`) directly for a normal visible console. In both modes the
' server's output is also persisted to data\logs\clayrune.log.
Option Explicit
Dim sh, fso, scriptDir, batPath, env
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath   = fso.BuildPath(scriptDir, "start.bat")
' Tell start.bat it was launched windowless so it redirects logs to a file
' instead of an (invisible) console.
Set env = sh.Environment("PROCESS")
env("CLAYRUNE_HIDDEN") = "1"
' 0 = hidden window, False = return immediately (don't wait for the server).
sh.Run """" & batPath & """", 0, False
