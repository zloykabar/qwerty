Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
pyw = here & "\runtime\python\pythonw.exe"
If fso.FileExists(pyw) Then
  sh.Run """" & pyw & """ """ & here & "\jarvis.py""", 0, False
Else
  sh.Run "pythonw jarvis.py", 0, False
End If
