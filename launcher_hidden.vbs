' Qalam - Start komplett ohne Terminal (Hintergrund).
' Doppelklick genuegt. Beenden: Tray-Icon unten rechts -> Rechtsklick -> Exit.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
proj = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = proj & "\venv\Scripts\pythonw.exe"
sh.CurrentDirectory = proj
' 0 = Fenster versteckt, False = nicht auf Ende warten
sh.Run """" & pyw & """ run.py", 0, False
