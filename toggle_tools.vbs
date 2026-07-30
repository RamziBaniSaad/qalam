' ============================================================
'  Qalam - Toggle (An/Aus) fuer den Arbeitsmodus.
'  Laeuft es -> beenden (VRAM frei). Laeuft es nicht -> starten.
'  Komplett ohne Terminal. Gedacht fuer einen globalen Hotkey.
'
'  Erweiterbar: weitere Tools unten in der Start-/Stop-Logik
'  ergaenzen (gleiches Muster), dann schaltet EIN Hotkey alles.
' ============================================================
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
proj = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = proj & "\venv\Scripts\pythonw.exe"

' --- Pruefen, ob Qalam laeuft (pythonw aus DIESEM Projekt) ---
Set svc = GetObject("winmgmts:\\.\root\cimv2")
Set procs = svc.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'pythonw.exe'")

running = False
Dim pids()
n = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(LCase(p.CommandLine), "qalam") > 0 Then
            ReDim Preserve pids(n)
            pids(n) = p.ProcessId
            n = n + 1
            running = True
        End If
    End If
Next

' --- Rueckmeldung: Ton + Sprechblase, damit man WEISS, was der Hotkey getan hat.
'     Ohne das drueckt man im Zweifel ein zweites Mal und schaltet zurueck.
Sub Melde(zustand)
    On Error Resume Next
    sh.Run "powershell -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ _
           & proj & "\signal.ps1"" -Zustand " & zustand, 0, False
    On Error Goto 0
End Sub

If running Then
    ' --- STOP: alle zugehoerigen Prozesse beenden ---
    For i = 0 To UBound(pids)
        On Error Resume Next
        sh.Run "taskkill /PID " & pids(i) & " /F", 0, True
        On Error Goto 0
    Next
    ' --- zusaetzlich: LLM-Modell aus dem VRAM entladen, damit beim Zocken alles frei ist ---
    On Error Resume Next
    sh.Run "cmd /c ollama stop qwen2.5:3b", 0, True
    sh.Run "cmd /c ollama stop llama3.2:3b", 0, True
    On Error Goto 0
    Melde "aus"
Else
    ' --- START: App stumm im Hintergrund ---
    sh.CurrentDirectory = proj
    sh.Run """" & pyw & """ run.py", 0, False
    Melde "an"
End If
