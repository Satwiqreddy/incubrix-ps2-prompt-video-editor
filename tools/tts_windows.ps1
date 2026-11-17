# Windows-only: speak one line with a Windows system voice into a WAV and write word start times (from the synthesiser's word events).
# powershell -File tools/tts_windows.ps1 -Wav out.wav -Json out.json -Voice David -Text "hello world"
param([string]$Wav, [string]$Json, [string]$Voice, [string]$Text)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$name = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name } | Where-Object { $_ -like "*$Voice*" } | Select-Object -First 1
$s.SelectVoice($name)
$s.SetOutputToWaveFile($Wav)
$global:w = New-Object System.Collections.ArrayList
$s.add_SpeakProgress({ param($sender, $e) [void]$global:w.Add(@{ text = $e.Text; start = $e.AudioPosition.TotalSeconds }) })
$s.Speak($Text)
$s.SetOutputToNull()
Start-Sleep -Milliseconds 300
ConvertTo-Json -InputObject @($global:w) -Compress | Out-File -Encoding utf8 $Json
