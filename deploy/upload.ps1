# Run on your Windows computer to send the code to the AWS server and start it.
#
#   powershell -ExecutionPolicy Bypass -File deploy\upload.ps1 -Server 16.176.186.149 -Key C:\path\to\lostfound-key.pem
#
# Only backend/, frontend/ and deploy/ are sent. Your local .env files,
# uploads, node_modules, .venv and caches stay on your computer.
# The build runs in the background on the server: if the connection drops,
# it keeps going. Reconnect and watch it with:  tail -f ~/deploy.log
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$User = "ubuntu"
)
$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$archive = Join-Path $env:TEMP "lostfound-deploy.tgz"
if (Test-Path $archive) { Remove-Item $archive }
$sshOpts = @("-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6")

Write-Host "Packing the project..."
tar -czf $archive -C $root `
    --exclude=node_modules --exclude=.next --exclude=out `
    --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache `
    --exclude=.env --exclude=.env.local `
    --exclude=uploads --exclude=.cache `
    backend frontend deploy
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

Write-Host "Uploading to $Server..."
scp @sshOpts $archive "${User}@${Server}:~/lostfound-deploy.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

Write-Host "Building and starting on the server (the first time takes 5-10 minutes)..."
ssh @sshOpts "${User}@${Server}" "mkdir -p ~/lost-found && tar -xzf ~/lostfound-deploy.tgz -C ~/lost-found && sed -i 's/\r$//' ~/lost-found/deploy/*.sh && bash ~/lost-found/deploy/remote-run.sh"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "The connection dropped or the deploy failed. The build may still be running on the server."
    Write-Host "Check it with:  ssh -i `"$Key`" ${User}@${Server} `"tail -n 40 -f ~/deploy.log`""
    throw "deploy did not finish while connected"
}
