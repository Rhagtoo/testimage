$root = "C:\Users\USERNAME"
$py = (Get-Command python).Source

function Stop-ByPidFile($name) {
    $p = Join-Path $root $name
    if (-not (Test-Path $p)) { return }
    try {
        $pid = [int](Get-Content $p -Raw).Trim()
        if ($pid -gt 0) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue }
    } catch {}
    Remove-Item $p -Force -ErrorAction SilentlyContinue
}

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*auto_cycle.py*' -or $_.CommandLine -like '*pentest_site_gallery_scanner.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Stop-ByPidFile "auto_cycle.pid"
Stop-ByPidFile "scanner.pid"
Stop-ByPidFile "burst.pid"
Start-Sleep -Seconds 3

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Add-Content -Path (Join-Path $root "auto_cycle.log") -Value "`n--- clean restart $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ---"
Start-Process -FilePath $py -ArgumentList "-u", "$root\auto_cycle.py", "--restart-burst", "-v" -WorkingDirectory $root -WindowStyle Hidden
Start-Sleep -Seconds 12
foreach ($name in @("auto_cycle.pid", "scanner.pid", "burst.pid")) {
    $p = Join-Path $root $name
    if (Test-Path $p) { Write-Output "$name=$((Get-Content $p -Raw).Trim())" }
}