$root = "C:\Users\USERNAME"
$py = (Get-Command python).Source
foreach ($name in @("auto_cycle.pid", "scanner.pid", "burst.pid")) {
    $p = Join-Path $root $name
    if (-not (Test-Path $p)) { continue }
    try {
        $pid = [int](Get-Content $p -Raw).Trim()
        if ($pid -gt 0) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue }
    } catch {}
    Remove-Item $p -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
$log = Join-Path $root "auto_cycle.log"
Add-Content -Path $log -Value "`n--- detached start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ---"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Start-Process -FilePath $py -ArgumentList "-u", "$root\auto_cycle.py", "--restart-burst", "-v" -WorkingDirectory $root -WindowStyle Hidden
Start-Sleep -Seconds 10
foreach ($name in @("auto_cycle.pid", "scanner.pid", "burst.pid")) {
    $p = Join-Path $root $name
    if (Test-Path $p) { Write-Output "$name=$(Get-Content $p -Raw).Trim()" } else { Write-Output "$name=none" }
}