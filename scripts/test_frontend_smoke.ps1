param(
    [int]$Port = 8766,
    [switch]$NoRestart,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Artifacts = Join-Path $Root ".artifacts\frontend-smoke"
New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

Set-Location $Root

Write-Step "Compile key Python files"
Invoke-Checked $Python @(
    "-m", "py_compile",
    "src\resume_bot\local_web.py",
    "src\resume_bot\local_web_assets.py",
    "src\resume_bot\llm.py"
)

Write-Step "Run focused regression tests"
Invoke-Checked $Python @(
    "-m", "unittest",
    "tests.test_llm",
    "tests.test_local_web",
    "tests.test_matching",
    "tests.test_pipeline_source_selection",
    "tests.test_pipeline_queue_import"
)

$BaseUrl = "http://127.0.0.1:$Port"

if (-not $NoRestart) {
    Write-Step "Restart local web server on $BaseUrl"
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        if ($listener.OwningProcess) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Process -FilePath $Python `
        -ArgumentList "scripts\run_local_web.py --host 127.0.0.1 --port $Port" `
        -WorkingDirectory $Root `
        -WindowStyle Hidden | Out-Null
}

Write-Step "Wait for local web server"
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing "$BaseUrl/api/ai-settings" -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $ready) {
    throw "Local web server did not become ready at $BaseUrl"
}

Write-Step "Check served page and inline script"
$Html = (Invoke-WebRequest -UseBasicParsing "$BaseUrl/" -TimeoutSec 10).Content
if (-not $Html -or $Html.Length -lt 1000) {
    throw "Served HTML is unexpectedly small."
}

$ScriptMatch = [regex]::Match($Html, "<script>([\s\S]*)</script>")
if (-not $ScriptMatch.Success) {
    throw "Could not find inline script in served HTML."
}
$ServedScriptPath = Join-Path $Artifacts "served-inline-script.js"
Set-Content -LiteralPath $ServedScriptPath -Value $ScriptMatch.Groups[1].Value -Encoding UTF8

$Node = Get-Command node -ErrorAction SilentlyContinue
if ($Node) {
    Invoke-Checked $Node.Source @("--check", $ServedScriptPath)
} else {
    Write-Host "node not found; skipped JS syntax check." -ForegroundColor Yellow
}

if (-not $SkipBrowser) {
    Write-Step "Run browser smoke test"
    $SmokePy = Join-Path $Artifacts "frontend_smoke.py"
    @'
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--screenshot", required=True)
    args = parser.parse_args()

    console_errors: list[str] = []
    model_payload = {
        "ok": True,
        "target": "text",
        "provider": "openai-compatible",
        "models_url": "https://frontend-smoke.invalid/v1/models",
        "model_count": 2,
        "models": [
            {"id": "mimo-v2.5", "input": "text,image"},
            {"id": "mimo-v2.5-pro", "input": "text"},
        ],
        "duration_ms": 1,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error"
            else None,
        )
        page.route(
            "**/api/ai-settings/models",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(model_payload),
            ),
        )
        page.goto(args.url, wait_until="networkidle")
        if page.locator("body").inner_text().strip() == "":
            raise AssertionError("Page body is empty.")

        nav = page.locator('[data-nav-page="ai-settings"]')
        if nav.count() == 0:
            raise AssertionError("AI settings navigation entry is missing.")
        nav.first.click()

        api_key = page.locator("#ai-text-api-key")
        base_url = page.locator("#ai-text-base-url")
        list_button = page.locator('[data-ai-list-models="text"]')
        if api_key.count() == 0 or base_url.count() == 0 or list_button.count() == 0:
            raise AssertionError("AI settings controls are missing.")

        api_key.fill("frontend-smoke-secret-9999")
        base_url.fill("https://token-plan-cn.xiaomimimo.com/v1")
        list_button.first.click()
        page.wait_for_function(
            "document.querySelector('#ai-text-model') && document.querySelector('#ai-text-model').options.length >= 2"
        )
        preserved = page.locator("#ai-text-api-key").input_value()
        if preserved != "frontend-smoke-secret-9999":
            raise AssertionError("API key draft was cleared after listing models.")

        for page_id in ["workbench", "review", "settings", "ai-settings", "system"]:
            button = page.locator(f'[data-nav-page="{page_id}"]')
            if button.count():
                button.first.click()

        page.screenshot(path=args.screenshot, full_page=True)
        browser.close()

    if console_errors:
        raise AssertionError("Browser console errors:\n" + "\n".join(console_errors[:20]))


if __name__ == "__main__":
    main()
'@ | Set-Content -LiteralPath $SmokePy -Encoding UTF8

    $ScreenshotPath = Join-Path $Artifacts "frontend-smoke.png"
    Invoke-Checked $Python @($SmokePy, "--url", $BaseUrl, "--screenshot", $ScreenshotPath)
    Write-Host "Browser screenshot: $ScreenshotPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "Frontend smoke test passed for $BaseUrl" -ForegroundColor Green
