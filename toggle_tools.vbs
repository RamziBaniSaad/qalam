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

' --- Noor-Werkzeuge, falls dieser Rechner sie hat -------------------------
'  Die Tafel und das Aufraeumen liegen im privaten noor-Repo, nicht hier.
'  Qalam soll auch ohne sie ganz normal funktionieren -- deshalb wird nur
'  aufgerufen, was tatsaechlich da ist.
noorWerkzeuge = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\noor\werkzeuge"

Sub NoorSkript(datei, argumente, warten)
    On Error Resume Next
    pfad = noorWerkzeuge & "\" & datei
    If fso.FileExists(pfad) Then
        sh.Run "powershell -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ _
               & pfad & """ " & argumente, 0, warten
    End If
    On Error Goto 0
End Sub

If running Then
    ' --- STOP: Arbeitsmodus komplett herunterfahren ---
    '  Reihenfolge mit Absicht: erst Noors Bildschirm leerraeumen und die Tafel
    '  sauber beenden (samt Sammler im Hintergrund), dann Qalam, dann das VRAM.
    NoorSkript "noor-links-zu.ps1", "", True
    NoorSkript "noor-tafel.ps1", "-Stopp", True

    ' /T beendet den ganzen Baum, nicht nur den einen Prozess. Eine venv startet
    ' den echten Interpreter als Kindprozess; ohne /T bleibt der stehen, und beim
    ' naechsten Einschalten laufen zwei davon. Zwei Ohren streiten sich dann um
    ' dasselbe Mikrofon und keines hoert mehr etwas -- am 31.07.2026 genau so
    ' passiert, Ramzi hat dreissigmal vergeblich gerufen.
    For i = 0 To UBound(pids)
        On Error Resume Next
        sh.Run "taskkill /PID " & pids(i) & " /T /F", 0, True
        On Error Goto 0
    Next
    ' --- zusaetzlich: LLM-Modell aus dem VRAM entladen, damit beim Zocken alles frei ist ---
    On Error Resume Next
    sh.Run "cmd /c ollama stop qwen2.5:3b", 0, True
    sh.Run "cmd /c ollama stop llama3.2:3b", 0, True
    On Error Goto 0
    Melde "aus"
    ' Claude ganz zum Schluss: er belegt Arbeitsspeicher und laeuft sonst still
    ' weiter, obwohl der Arbeitsmodus aus ist. Bewusst NACH der Rueckmeldung --
    ' mit Claude endet auch eine laufende Sitzung, und der Ton soll vorher da
    ' sein.
    '
    ' NICHT per "taskkill /IM claude.exe": "claude.exe" ist unter Windows
    ' KEIN eindeutiger Name. Die Claude-Code-Sitzung (dieser CLI-Hintergrund-
    ' prozess, unter AppData\Roaming\Claude\claude-code\<version>\claude.exe)
    ' heisst GENAUSO wie die Desktop-App unter WindowsApps\Claude_*\Claude.exe.
    ' Ein blinder Kill nach Namen haette beim naechsten Ausschalten die
    ' laufende Noor-Sitzung mit sich gerissen -- am 31.07.2026 vor dem ersten
    ' echten Einsatz bemerkt, nie ausgeloest. Deshalb wird ueber WMI nach dem
    ' tatsaechlichen Installationspfad gefiltert: nur was unter "WindowsApps"
    ' liegt, ist die Desktop-App.
    Set claudeProcs = svc.ExecQuery( _
        "SELECT ProcessId, ExecutablePath FROM Win32_Process WHERE Name = 'Claude.exe'")
    For Each cp In claudeProcs
        If Not IsNull(cp.ExecutablePath) Then
            If InStr(LCase(cp.ExecutablePath), "\windowsapps\") > 0 Then
                On Error Resume Next
                sh.Run "taskkill /PID " & cp.ProcessId & " /T /F", 0, True
                On Error Goto 0
            End If
        End If
    Next
Else
    ' --- START: Arbeitsmodus hochfahren ---
    sh.CurrentDirectory = proj
    sh.Run """" & pyw & """ run.py", 0, False
    ' Die Tafel gehoert dazu: sie ging beim Ausschalten mit, also kommt sie
    ' beim Einschalten auch wieder. Alles andere waere eine Einbahnstrasse.
    NoorSkript "noor-tafel.ps1", "", False
    ' Claude wieder mit hoch. Der Startbefehl steht im Autostart-Eintrag der
    ' App selbst -- so bleibt er richtig, auch wenn die App sich aktualisiert
    ' und ihre Versionsnummer im Pfad wechselt.
    On Error Resume Next
    claudeBefehl = sh.RegRead("HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Claude")
    If Err.Number = 0 And Len(claudeBefehl) > 0 Then
        sh.Run claudeBefehl, 0, False
    End If
    Err.Clear
    On Error Goto 0
    Melde "an"
End If
