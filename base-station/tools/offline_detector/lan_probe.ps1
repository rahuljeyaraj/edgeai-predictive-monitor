# Runs on the Windows host -- deliberately not in WSL2, which does not share
# the browser's route to the board. Issues the same GET /nodes the dashboard
# polls, once a second, and reports how long each one took.
param(
  [int]$Dur = 120,
  [string]$BoardIp = "192.168.1.10",
  [int]$Port = 8080,
  [int]$TimeoutSec = 4
)
Write-Output "epoch,result,secs"
$uri = "http://{0}:{1}/nodes" -f $BoardIp, $Port
$end = (Get-Date).AddSeconds($Dur)
while ((Get-Date) -lt $end) {
  $tick = Get-Date
  $ep = [int][double]::Parse((Get-Date -UFormat %s))
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    Invoke-WebRequest -Uri $uri -TimeoutSec $TimeoutSec -UseBasicParsing | Out-Null
    $res = "ok"
  } catch { $res = "FAIL" }
  $sw.Stop()
  Write-Output ("{0},{1},{2:N2}" -f $ep, $res, $sw.Elapsed.TotalSeconds)
  $sleep = 1 - (New-TimeSpan -Start $tick -End (Get-Date)).TotalSeconds
  if ($sleep -gt 0) { Start-Sleep -Milliseconds ([int]($sleep * 1000)) }
}
