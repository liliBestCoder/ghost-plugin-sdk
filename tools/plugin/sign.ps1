#Requires -Version 7
<#
.SYNOPSIS
    Signs one file for the plugin centre (ECDSA P-256 + SHA-256, detached .sig),
    or prints a key's public half. PowerShell 7 / .NET only -- no Python.

.DESCRIPTION
    The no-Python alternative to `gpkg.py sign`, for whoever holds a key and
    has to sign a single document: the maintainer signing registry.json with
    the root key, or a developer signing ghost-plugin.json with a dev key.

    Signing:
        pwsh -NoProfile -File tools/plugin/sign.ps1 -Key <private.pem> -File <path> [-Out <path.sig>] [-Force]

    signs the file's RAW bytes -- no BOM stripping, no line-ending conversion,
    no JSON re-serialisation (spec-release.md section 1) -- and writes
    <path>.sig (or -Out): base64 of the 64-byte IEEE P1363 r||s and ONE
    trailing "\n", the exact shape tools/plugin/make_fixtures.ps1 and
    gpkg.py produce. Before writing, the signature is verified with a key
    rebuilt from the exported X||Y alone, so what is written is checked
    against the 64 bytes the client actually compiles in, not against the
    key object. An existing .sig is never overwritten without -Force.

    Public key:
        pwsh -NoProfile -File tools/plugin/sign.ps1 -Key <private.pem> -PublicKey

    prints base64 of the raw X||Y (88 characters) and nothing else on stdout.

    The key file is a PEM that .NET's ImportFromPem reads: PKCS#8
    "PRIVATE KEY" (what gpkg.py keygen writes) or SEC1 "EC PRIVATE KEY",
    unencrypted, on the P-256 curve. The private key is never printed, and
    error messages never echo the file's contents.

    Tested by ghost_plugin_sign_tool_test (src/tests/test_plugin_sign_tool.cpp):
    it signs with a throwaway key and verifies with the production verifier.
#>
[CmdletBinding(DefaultParameterSetName = 'Sign')]
param(
    [Parameter(Mandatory)][string]$Key,
    [Parameter(Mandatory, ParameterSetName = 'Sign')][string]$File,
    [Parameter(ParameterSetName = 'Sign')][string]$Out,
    [Parameter(ParameterSetName = 'Sign')][switch]$Force,
    [Parameter(Mandatory, ParameterSetName = 'PublicKey')][switch]$PublicKey
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sha = [System.Security.Cryptography.HashAlgorithmName]::SHA256
$p1363 = [System.Security.Cryptography.DSASignatureFormat]::IeeeP1363FixedFieldConcatenation
$p256Oid = '1.2.840.10045.3.1.7'

# .NET resolves relative paths against the PROCESS directory, which is not
# PowerShell's $PWD; resolve through the provider so `-File .\x.json` means
# what it says.
function Resolve-Full([string]$p) { $PSCmdlet.GetUnresolvedProviderPathFromPSPath($p) }

function Import-P256Key([string]$path) {
    $full = Resolve-Full $path
    if (-not [System.IO.File]::Exists($full)) { throw "key file not found: $full" }
    $pem = [System.IO.File]::ReadAllText($full)
    $k = [System.Security.Cryptography.ECDsa]::Create()
    try {
        $k.ImportFromPem($pem)
    } catch {
        # Deliberately not $_: keep whatever the parser saw out of the console.
        throw "could not read $full as an unencrypted EC private key PEM (PKCS#8 'PRIVATE KEY' or SEC1 'EC PRIVATE KEY')"
    } finally {
        $pem = $null
    }
    $params = $k.ExportParameters($false)
    if ($params.Curve.Oid.Value -ne $p256Oid) { throw "the key in $full is not on the P-256 curve" }
    $k
}

# Raw X||Y, 64 bytes (spec-release section 1: the uncompressed point without 0x04).
function Get-PubXY($k) {
    $q = $k.ExportParameters($false).Q
    if ($q.X.Length -ne 32 -or $q.Y.Length -ne 32) { throw "unexpected coordinate length" }
    [byte[]]$xy = [byte[]]$q.X + [byte[]]$q.Y
    , $xy
}

function New-PublicOnlyKey([byte[]]$xy) {
    $params = [System.Security.Cryptography.ECParameters]::new()
    $params.Curve = [System.Security.Cryptography.ECCurve+NamedCurves]::nistP256
    $pt = [System.Security.Cryptography.ECPoint]::new()
    $pt.X = [byte[]]$xy[0..31]
    $pt.Y = [byte[]]$xy[32..63]
    $params.Q = $pt
    $pub = [System.Security.Cryptography.ECDsa]::Create()
    $pub.ImportParameters($params)
    $pub
}

$k = Import-P256Key $Key
[byte[]]$xy = Get-PubXY $k
$pubB64 = [Convert]::ToBase64String($xy)
if ($pubB64.Length -ne 88) { throw "public key base64 is $($pubB64.Length) chars, not 88" }

if ($PSCmdlet.ParameterSetName -eq 'PublicKey') {
    Write-Output $pubB64
    return
}

$fileFull = Resolve-Full $File
if (-not [System.IO.File]::Exists($fileFull)) { throw "file to sign not found: $fileFull" }
$outFull = if ($Out) { Resolve-Full $Out } else { $fileFull + '.sig' }
if ([System.IO.Directory]::Exists($outFull)) { throw "output path is a directory: $outFull" }
if (-not $Force -and [System.IO.File]::Exists($outFull)) {
    throw "$outFull already exists; pass -Force to replace it"
}

[byte[]]$bytes = [System.IO.File]::ReadAllBytes($fileFull)
[byte[]]$sig = $null
try {
    $sig = $k.SignData($bytes, $sha, $p1363)
} catch {
    throw "signing failed -- does $Key hold a PRIVATE key?"
}
if ($sig.Length -ne 64) { throw "SignData returned $($sig.Length) bytes, not a 64-byte P1363 r||s" }

$pub = New-PublicOnlyKey $xy
if (-not $pub.VerifyData($bytes, $sig, $sha, $p1363)) {
    throw "self-check: the signature does not verify with the exported X||Y -- nothing written"
}

$text = [Convert]::ToBase64String($sig) + "`n"
[byte[]]$textBytes = [System.Text.Encoding]::ASCII.GetBytes($text)
# CreateNew without -Force: the existence check above is a friendly message,
# this is the guarantee (no window between checking and writing).
$mode = if ($Force) { [System.IO.FileMode]::Create } else { [System.IO.FileMode]::CreateNew }
$fs = [System.IO.FileStream]::new($outFull, $mode, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try { $fs.Write($textBytes, 0, $textBytes.Length) } finally { $fs.Dispose() }

$digest = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
$keyFp = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($xy)).ToLowerInvariant()
Write-Host "wrote $outFull"
Write-Host "  signed   $fileFull ($($bytes.Length) bytes, sha256 $digest)"
Write-Host "  key      $pubB64"
Write-Host "  key sha256 (of the 64 X||Y bytes) $keyFp"
