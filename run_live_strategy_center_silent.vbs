Option Explicit

Dim fso, shell, scriptDir, pythonExe, pythonwExe, targetScript, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = "C:\ProgramData\miniforge3\envs\stock\python.exe"
pythonwExe = "C:\ProgramData\miniforge3\envs\stock\pythonw.exe"
targetScript = fso.BuildPath(scriptDir, "run_live_strategy_center.py")

If Not fso.FileExists(targetScript) Then
    MsgBox "Startup script not found: " & targetScript, vbCritical, "Live Strategy Center"
    WScript.Quit 1
End If

If fso.FileExists(pythonwExe) Then
    cmd = """" & pythonwExe & """ """ & targetScript & """"
ElseIf fso.FileExists(pythonExe) Then
    cmd = """" & pythonExe & """ """ & targetScript & """"
Else
    MsgBox "Python launcher not found. Check your Miniforge/Conda path.", vbCritical, "Live Strategy Center"
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDir
shell.Run cmd, 0, False
