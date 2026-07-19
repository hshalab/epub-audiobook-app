param([int]$TaskNum)
$plan = Get-Content "D:\Projects\epub-audiobook-app\docs\superpowers\plans\2026-07-19-multi-oauth-client.md" -Raw
$inTask = $false
$inFence = $false
$result = @()
$nextTaskPattern = "^#+[ \t]+Task[ \t]+($($TaskNum+1))([^0-9]|$)"
foreach ($line in $plan -split "`n") {
    if ($line -match '^```') { $inFence = -not $inFence }
    if ($inTask -and -not $inFence -and $line -match $nextTaskPattern) { break }
    if (-not $inFence -and $line -match "^#+[ \t]+Task[ \t]+$TaskNum([^0-9]|$)") { $inTask = $true }
    if ($inTask) { $result += $line }
}
$out = "D:\Projects\epub-audiobook-app\.superpowers\sdd\task-$TaskNum-brief.md"
$result -join "`n" | Set-Content $out -Encoding utf8
Write-Output "wrote $out ($($result.Count) lines)"
