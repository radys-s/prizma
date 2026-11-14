<#
.SYNOPSIS
  Turns on Refrakt's /v1 gateway on this PC and gives AI Bridge Studio the same token.

  Adds GATEWAY_TOKEN to backend\.env (only if it has none) and sets the user variable
  REFRAKT_GATEWAY_TOKEN, which Studio reads. The token is never printed. Restart Refrakt
  and Studio afterwards.
#>
$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\..\backend\.env'
$line = Get-Content $envFile -ErrorAction SilentlyContinue | Where-Object { $_ -match '^GATEWAY_TOKEN=.' } | Select-Object -Last 1
if ($line) {
  $token = $line.Substring('GATEWAY_TOKEN='.Length)
} else {
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  Add-Content -Path $envFile -Value "`nGATEWAY_TOKEN=$token"
}
[Environment]::SetEnvironmentVariable('REFRAKT_GATEWAY_TOKEN', $token, 'User')
Write-Output 'Gateway token is set for Refrakt and Studio. Restart both.'
