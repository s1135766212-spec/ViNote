param([string]$BrowserPython = '')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$containerName = 'vinote-mindmap-it-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$imageName = 'vinote-mindmap-integration:local'
$containerStarted = $false

if (-not $BrowserPython) {
    $systemPython = (Get-Command python -ErrorAction Stop).Source
    & $systemPython -c 'import playwright' 2>$null
    if ($LASTEXITCODE -eq 0) {
        $BrowserPython = $systemPython
    } else {
        $browserVenv = Join-Path $env:TEMP 'vinote-mindmap-browser-venv'
        if (-not (Test-Path -LiteralPath (Join-Path $browserVenv 'Scripts\python.exe'))) {
            & $systemPython -m venv $browserVenv
            if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated Playwright venv' }
        }
        $BrowserPython = Join-Path $browserVenv 'Scripts\python.exe'
        & $BrowserPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip in isolated venv' }
        & $BrowserPython -m pip install --only-binary=:all: playwright
        if ($LASTEXITCODE -ne 0) { throw 'Could not install Playwright in isolated venv' }
    }
}

Push-Location $repoRoot
try {
    & docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable' }
    & docker build --tag $imageName .
    if ($LASTEXITCODE -ne 0) { throw 'Isolated test image build failed' }
    $mount = 'type=bind,source=' + $PSScriptRoot + ',target=/app/tests,readonly'
    & docker run --rm --detach --name $containerName --publish '127.0.0.1:18998:8999' --mount $mount $imageName /app/.venv/bin/python -m tests.mindmap_integration_server | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Isolated test container failed to start' }
    $containerStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:18998/health' -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        & docker logs --tail 100 $containerName
        throw 'Isolated test server did not become healthy'
    }

    $env:VINOTE_INTEGRATION_BASE = 'http://127.0.0.1:18998'
    $env:VINOTE_BROWSER_CHANNEL = 'chrome'
    & $BrowserPython (Join-Path $PSScriptRoot 'browser_mindmap_integration.py')
    if ($LASTEXITCODE -ne 0) { throw 'Browser integration scenarios failed' }
} finally {
    if ($containerStarted) { & docker stop $containerName | Out-Null }
    Pop-Location
}
