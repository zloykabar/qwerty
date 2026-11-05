# Установщик Джарвиса. Запускается через INSTALL_JARVIS.bat. Можно запускать повторно: сделанное пропускается.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir = $PSScriptRoot
Set-Location $dir
try { Start-Transcript -Path (Join-Path $dir "install.log") -Force | Out-Null } catch {}

$PY_URL     = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
$PY_PTH     = "python312"
$GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
$VOSK_URL   = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
$OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
$WV2_URL    = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$VCRT_URL   = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

# SHA256 для артефактов с фиксированной версией: если файл подменят по пути, установка остановится.
# Для «плавающих» ссылок (aka.ms, go.microsoft.com, ollama.com/download) хэш меняется вместе с версией,
# поэтому там проверяем цифровую подпись Authenticode (Microsoft/Ollama).
$SHA = @{
    $PY_URL   = "8d3f33be9eb810f23c102f08475af2854e50484b8e4e06275e937be61ce3d2fb"
    $VOSK_URL = "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"
}
# Кому доверяем подпись установщиков (подстрока в имени подписанта)
$TRUSTED_SIGNERS = @("Ollama", "Microsoft Corporation")

$rt  = Join-Path $dir "runtime\python"
$py  = Join-Path $rt "python.exe"
$failed = New-Object System.Collections.ArrayList

function Step($t)  { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Info($t)  { Write-Host "  $t" }
function Good($t)  { Write-Host "  $t" -ForegroundColor Green }
function Bad($t)   { Write-Host "  $t" -ForegroundColor Red }

function Get-File($url, $out) {
    $tmp = "$out.part"
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
    $done = $false
    $curl = Join-Path $env:WINDIR "System32\curl.exe"
    if (Test-Path $curl) {
        & $curl -L --fail --retry 3 --retry-delay 2 -# -o $tmp $url
        if ($LASTEXITCODE -eq 0) { $done = $true }
    }
    if (-not $done) {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -ErrorAction Stop
    }
    if (-not (Test-Integrity $url $tmp $out)) {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        throw "Проверка подлинности файла не прошла: $url"
    }
    Move-Item $tmp $out -Force
}

function Test-Integrity($url, $path, $name) {
    # $path — скачанный временный файл, $name — имя, под которым он будет сохранён.
    # Тип определяем по $name: у временного файла расширение .part, оно бы всё сломало.
    # 1. Известный SHA256 для артефактов с фиксированной версией
    $want = $SHA[$url]
    if ($want) {
        $got = (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $want) {
            Bad "Хэш не совпал: $url"
            Bad "  ожидался $want"
            Bad "  получен  $got"
            return $false
        }
        Info "Хэш файла совпал."
        return $true
    }
    # 2. .exe-установщики по «плавающим» ссылкам проверяем цифровой подписью
    if ($name.ToLower().EndsWith(".exe")) {
        try {
            $sig = Get-AuthenticodeSignature -FilePath $path
            if ($sig.Status -ne "Valid") {
                Bad "Подпись файла недействительна ($($sig.Status)): $url"
                return $false
            }
            $signer = "$($sig.SignerCertificate.Subject)"
            foreach ($t in $TRUSTED_SIGNERS) {
                if ($signer -like "*$t*") { Info "Подпись проверена: $t"; return $true }
            }
            Bad "Файл подписан неизвестным издателем: $signer"
            return $false
        } catch {
            Bad "Не удалось проверить подпись файла: $url ($($_.Exception.Message))"
            return $false
        }
    }
    # 3. Прочие скрипты, например get-pip.py: защищает только HTTPS
    if ($url -notmatch '^https://') { Bad "Скачивание по незащищённому каналу: $url"; return $false }
    return $true
}

function Test-Url($u, $tries = 1, $timeout = 10) {
    for ($i = 0; $i -lt $tries; $i++) {
        try { Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec $timeout -ErrorAction Stop | Out-Null; return $true } catch { Start-Sleep 1 }
    }
    return $false
}

function Find-Ollama {
    $p = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $p) { return $p }
    $c = Get-Command ollama -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

function Make-Link($path) {
    $sh = New-Object -ComObject WScript.Shell
    $l = $sh.CreateShortcut($path)
    $l.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
    $l.Arguments = '"' + (Join-Path $dir "Jarvis.vbs") + '"'
    $l.WorkingDirectory = $dir
    $l.Description = "Джарвис"
    $ico = Join-Path $dir "assets\app.ico"
    if (Test-Path $ico) { $l.IconLocation = $ico }
    $l.Save()
}

Clear-Host
Write-Host "==============================================" -ForegroundColor Yellow
Write-Host "   УСТАНОВКА ДЖАРВИСА" -ForegroundColor Yellow
Write-Host "==============================================" -ForegroundColor Yellow
Write-Host "Нужен интернет и около 5 ГБ места. Ничего ставить заранее не надо:"
Write-Host "Python, библиотеки, нейросеть и голоса скачаются сами."
Write-Host "Джарвис ставится в эту папку: $dir"
Write-Host ""

if ($dir -like "*\Temp\*" -or $dir -like "*.zip*") {
    Bad "Похоже, архив не распакован. Распакуйте ВСЮ папку (правая кнопка -> Извлечь всё) и запустите снова."
    exit 1
}
if (-not [Environment]::Is64BitOperatingSystem) {
    Bad "Нужна 64-разрядная Windows 10 или 11. На 32-разрядной Джарвис не запустится."
    exit 1
}
try {
    $drive = (Get-Item $dir).PSDrive
    $freeGb = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGb -lt 6) {
        Bad "На диске $($drive.Name): свободно всего $freeGb ГБ, нужно минимум 6 ГБ (с Whisper около 10 ГБ). Освободите место и запустите снова."
        exit 1
    }
} catch {}
if ($dir -match '[^\u0000-\u007F]') {
    Write-Host "ВНИМАНИЕ: в пути к папке есть русские буквы или особые символы:" -ForegroundColor Yellow
    Write-Host "  $dir" -ForegroundColor Yellow
    Write-Host "Нейросетевой голос (Piper) на таких путях может не работать. Надёжнее перенести папку Джарвиса в C:\Jarvis." -ForegroundColor Yellow
    $ans = Read-Host "Всё равно продолжить установку? (y/n)"
    if ($ans -notmatch '^[yYдД]') { exit 1 }
}
if (-not (Test-Url "https://www.python.org" 3 10)) {
    Bad "Нет интернета (или его блокирует антивирус/фаервол). Проверьте соединение и запустите установку ещё раз."
    exit 1
}

# вопросы задаём сразу, дальше установка идёт без вмешательства
$wantAuto    = (Read-Host "Запускать Джарвиса вместе с Windows? (y/n)") -match '^[yYдД]'
$wantWhisper = (Read-Host "Поставить точное распознавание речи Whisper (понимает английские названия и шёпот, +4 ГБ, долго)? (Y/n, по умолчанию да)") -notmatch '^[nNнН]'
Write-Host ""
Write-Host "Начинаю. Не закрывайте окно, это займёт 10-30 минут." -ForegroundColor Yellow

# ---------- 1. Системные компоненты ----------
Step "1/8  Системные компоненты Windows"
try {
    $vc = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" -ErrorAction SilentlyContinue
    if ($vc -and $vc.Installed -eq 1) { Info "Visual C++ Runtime уже есть." }
    else {
        Info "Ставлю Visual C++ Runtime (может появиться запрос Windows - нажмите «Да»)..."
        $f = Join-Path $env:TEMP "vc_redist.x64.exe"
        Get-File $VCRT_URL $f
        Start-Process $f -ArgumentList "/install", "/quiet", "/norestart" -Wait
    }
} catch { Bad "Visual C++ не поставился: $($_.Exception.Message)"; [void]$failed.Add("Visual C++") }
try {
    $wv = @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
    $has = $false
    foreach ($k in $wv) { if (Test-Path $k) { $has = $true } }
    if ($has) { Info "WebView2 (движок окна) уже есть." }
    else {
        Info "Ставлю WebView2 (движок окна интерфейса)..."
        $f = Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe"
        Get-File $WV2_URL $f
        Start-Process $f -ArgumentList "/silent", "/install" -Wait
    }
} catch { Bad "WebView2 не поставился: $($_.Exception.Message)"; [void]$failed.Add("WebView2") }

# ---------- 2. Python ----------
Step "2/8  Python (свой, внутри папки Джарвиса)"
try {
    if (-not (Test-Path $py)) {
        New-Item -ItemType Directory -Force $rt | Out-Null
        $zip = Join-Path $env:TEMP "python-embed.zip"
        Info "Скачиваю Python 3.12 (около 10 МБ)..."
        Get-File $PY_URL $zip
        Expand-Archive -Path $zip -DestinationPath $rt -Force -ErrorAction Stop
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    }
    # включаем site-packages и добавляем папку Джарвиса в пути поиска модулей (безопасно повторять)
    $pth = Join-Path $rt "$PY_PTH._pth"
    Set-Content -Path $pth -Value @("$PY_PTH.zip", ".", "..\..", "import site") -Encoding ASCII
    & $py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Info "Ставлю pip..."
        $gp = Join-Path $env:TEMP "get-pip.py"
        Get-File $GETPIP_URL $gp
        & $py $gp --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw "get-pip завершился с ошибкой" }
    }
    Good "Python готов."
} catch {
    Bad "Не получилось поставить Python: $($_.Exception.Message)"
    Bad "Без него дальше нельзя. Проверьте интернет и запустите установку снова."
    exit 1
}

# ---------- 3. Библиотеки ----------
Step "3/8  Библиотеки Джарвиса"
$req = Join-Path $dir "requirements.txt"
$pipBase = @("-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check", "--prefer-binary")
& $py -m pip install --upgrade pip --no-warn-script-location --disable-pip-version-check
& $py @pipBase -r $req
if ($LASTEXITCODE -ne 0) {
    Info "Общая установка не прошла. Ставлю библиотеки по одной, чтобы найти проблемную..."
    foreach ($line in (Get-Content $req -Encoding UTF8)) {
        $pkg = $line.Trim()
        if (-not $pkg -or $pkg.StartsWith("#")) { continue }
        & $py @pipBase $pkg
        if ($LASTEXITCODE -ne 0) {
            Info "Повтор для $pkg ..."
            Start-Sleep 2
            & $py @pipBase $pkg
        }
    }
}
$mods = "vosk","sounddevice","webview","psutil","requests","comtypes","pystray","PIL","docx","pptx","openpyxl","ddgs","pymorphy3","rapidfuzz","websocket","clr"
$missing = @()
foreach ($m in $mods) {
    & $py -c "import $m" *> $null
    if ($LASTEXITCODE -ne 0) { $missing += $m }
}
if ($missing.Count -gt 0) { Bad "Не встали библиотеки (модули): $($missing -join ', '). Смотрите сообщения выше."; [void]$failed.Add("библиотеки Python: $($missing -join ', ')") }
else { Good "Библиотеки готовы." }

# ---------- 4. Ollama ----------
Step "4/8  Ollama (запускает нейросеть)"
try {
    $ollama = Find-Ollama
    if (-not $ollama) {
        Info "Скачиваю и ставлю Ollama (около 1 ГБ)..."
        $f = Join-Path $env:TEMP "OllamaSetup.exe"
        Get-File $OLLAMA_URL $f
        Start-Process $f -ArgumentList "/SILENT", "/NORESTART" -Wait
        Start-Sleep 3
        $ollama = Find-Ollama
    }
    if ($ollama) { Good "Ollama: $ollama" } else { throw "Ollama не нашлась после установки" }
} catch { Bad "Ollama не поставилась: $($_.Exception.Message)"; [void]$failed.Add("Ollama") }

# ---------- 5. Модель ----------
Step "5/8  Модель нейросети"
$ollama = Find-Ollama
if ($ollama) {
    $model = "qwen2.5:3b"
    try { $cfg = Get-Content (Join-Path $dir "config.json") -Raw -Encoding UTF8 | ConvertFrom-Json; if ($cfg.ollama_model) { $model = $cfg.ollama_model } } catch {}
    $up = Test-Url "http://localhost:11434"
    if (-not $up) {
        Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 30 -and -not $up; $i++) { Start-Sleep 1; $up = Test-Url "http://localhost:11434" }
    }
    Info "Скачиваю модель $model (около 2 ГБ)..."
    & $ollama pull $model
    if ($LASTEXITCODE -ne 0) {
        Bad "Модель не скачалась. Джарвис попробует сам при первом запуске."
        [void]$failed.Add("модель $model")
    } else { Good "Модель готова." }
} else {
    Bad "Пропускаю: нет Ollama."
}

# ---------- 6. Голоса ----------
Step "6/8  Голоса Джарвиса (Piper)"
& $py (Join-Path $dir "install_voice.py")
if ($LASTEXITCODE -ne 0) { Bad "Голоса не скачались. Позже можно поставить заново, запустив INSTALL_JARVIS.bat."; [void]$failed.Add("голоса") }

# ---------- 7. Распознавание речи ----------
Step "7/8  Распознавание речи (Vosk)"
$modelDir = Join-Path $dir "model"
if (Test-Path $modelDir) { Info "Модель Vosk уже есть." }
else {
    try {
        $zip = Join-Path $env:TEMP "vosk-ru.zip"
        $tmpDir = Join-Path $dir "_vosk_tmp"
        Info "Скачиваю русскую модель распознавания (около 45 МБ)..."
        Get-File $VOSK_URL $zip
        Expand-Archive -Path $zip -DestinationPath $tmpDir -Force -ErrorAction Stop
        $inner = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
        Move-Item $inner.FullName $modelDir
        Remove-Item $tmpDir -Recurse -Force
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Good "Готово."
    } catch { Bad "Модель Vosk не скачалась: $($_.Exception.Message)"; [void]$failed.Add("модель Vosk") }
}

# ---------- 8. Ярлыки ----------
Step "8/8  Ярлык на рабочем столе"
try {
    & $py (Join-Path $dir "brand.py")
    $desktop = [Environment]::GetFolderPath("Desktop")
    Make-Link (Join-Path $desktop "Джарвис.lnk")
    Good "Ярлык «Джарвис» создан на рабочем столе."
    if ($wantAuto) {
        Make-Link (Join-Path ([Environment]::GetFolderPath("Startup")) "Джарвис.lnk")
        Good "Добавлено в автозагрузку Windows."
    }
} catch { Bad "Ярлык не создался: $($_.Exception.Message). Запускайте START_JARVIS.bat."; [void]$failed.Add("ярлык") }

# ---------- Whisper (по желанию) ----------
if ($wantWhisper) {
    Step "Дополнительно: Whisper"
    & $py (Join-Path $dir "install_whisper.py")
    if ($LASTEXITCODE -ne 0) { Bad "Whisper не поставился. Можно повторить позже: Install_Whisper.bat."; [void]$failed.Add("Whisper") }
}

# ---------- Итог ----------
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "ВСЁ УСТАНОВЛЕНО!" -ForegroundColor Green
} else {
    Write-Host "Установка завершена, но не получилось: $($failed -join ', ')." -ForegroundColor Yellow
    Write-Host "Запустите INSTALL_JARVIS.bat ещё раз: готовое пропустится, недостающее докачается." -ForegroundColor Yellow
    Write-Host "Если опять не выйдет, пришлите файл install.log из этой папки." -ForegroundColor Yellow
}
Write-Host "Запускать: ярлык «Джарвис» на рабочем столе или START_JARVIS.bat" -ForegroundColor Gray
Write-Host ""
$go = Read-Host "Запустить Джарвиса сейчас? (y/n)"
if ($go -match '^[yYдД]') { Start-Process (Join-Path $env:WINDIR "System32\wscript.exe") -ArgumentList ('"' + (Join-Path $dir "Jarvis.vbs") + '"') -WorkingDirectory $dir }
try { Stop-Transcript | Out-Null } catch {}
