# Copies the email and AI settings from your local backend\.env to the server's
# deploy/.env, restarts the app, and makes you the admin. Nothing is printed.
#
#   powershell -ExecutionPolicy Bypass -File deploy\set-secrets.ps1 -Server 16.176.186.149 -Key C:\path\to\lostfound-key.pem
#
# Copied: SMTP_USER, SMTP_PASSWORD (spaces removed), EMAIL_FROM, OPENAI_API_KEY,
# OPENAI_MODEL, GEMINI_API_KEY, LLM_API_KEY (Groq), and EMAIL_BACKEND=smtp.
# A setting missing from backend\.env is left as it is on the server.
# Everything else on the server stays.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$AdminEmail = "soumyaranjansahu17430@gmail.com",
    [string]$User = "ubuntu"
)
$ErrorActionPreference = "Stop"

$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) "backend\.env"
if (-not (Test-Path $envFile)) { throw "Can't find $envFile" }

# The last line for a setting wins (like the app itself); placeholders shorter
# than $min characters are skipped.
function Get-Setting([string]$name, [int]$min) {
    $found = Select-String -Path $envFile -Pattern "^$name=(.*)$" |
        ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() } |
        Where-Object { $_.Length -ge $min } |
        Select-Object -Last 1
    if ($found) { return [string]$found } else { return "" }
}

$settings = [ordered]@{
    "SMTP_USER"      = Get-Setting "SMTP_USER" 5
    "SMTP_PASSWORD"  = (Get-Setting "SMTP_PASSWORD" 8) -replace '\s', ''
    "EMAIL_FROM"     = Get-Setting "EMAIL_FROM" 3
    "OPENAI_API_KEY" = Get-Setting "OPENAI_API_KEY" 10
    "OPENAI_MODEL"   = Get-Setting "OPENAI_MODEL" 3
    "LLM_API_KEY"    = Get-Setting "LLM_API_KEY" 10
    "GEMINI_API_KEY" = Get-Setting "GEMINI_API_KEY" 10
}

$lines = @()
foreach ($name in $settings.Keys) {
    $value = $settings[$name]
    if ($value) {
        $lines += "$name=$value"
        Write-Host "  found $name"
    } elseif ($name -notin @("EMAIL_FROM", "OPENAI_MODEL")) {
        Write-Host "  MISSING $name in backend\.env (left as it is on the server)"
    }
}
if ($settings["SMTP_USER"] -and $settings["SMTP_PASSWORD"]) {
    $lines += "EMAIL_BACKEND=smtp"
} else {
    $lines += "EMAIL_BACKEND=console"
    Write-Host "  No Gmail login found, so emails stay in 'console' mode (codes in the server logs)."
}

$kv = ($lines -join "`n")
$remote = @"
set -e
cd ~/lost-found/deploy
rm -f .env.save
cat > /tmp/lf-kv <<'LF_KV_END'
$kv
LF_KV_END
awk 'NR==FNR { i = index(`$0, "="); v[substr(`$0, 1, i - 1)] = substr(`$0, i + 1); next }
     { i = index(`$0, "="); k = substr(`$0, 1, i - 1)
       if (i > 1 && (k in v)) { print k "=" v[k]; delete v[k] } else print }
     END { for (k in v) print k "=" v[k] }' /tmp/lf-kv .env > .env.new
mv .env.new .env
chmod 600 .env
rm -f /tmp/lf-kv
echo "Settings saved. Restarting the app..."
sudo docker compose up -d
sleep 15
sudo docker compose exec -T backend python -m app.cli make-admin $AdminEmail || echo "make-admin failed (see the message above)"
echo "ALL DONE"
"@
$remote = $remote -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))

Write-Host "Sending the settings to $Server..."
ssh -i $Key -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=20 "${User}@${Server}" "echo $b64 | base64 -d | bash"
if ($LASTEXITCODE -ne 0) { throw "Something went wrong on the server (see above)." }
