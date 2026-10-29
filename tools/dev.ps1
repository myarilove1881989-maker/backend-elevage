param(
  [ValidateSet('setup','start','test','build-web','save','push')][string]$Action = 'setup',
  [string]$Name,
  [string]$Message,
  [string[]]$Tests = @(),
  [switch]$Postgres
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$root = Split-Path $repo -Parent
$env:TEMP = Join-Path $root 'tmp'; $env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $root '.cache/pip'
$env:GRADLE_USER_HOME = Join-Path $root '.cache/gradle'
$env:ANDROID_USER_HOME = Join-Path $root '.cache/android-user'
$env:PUB_CACHE = if($env:ELEVAGE_PUB_CACHE){$env:ELEVAGE_PUB_CACHE}else{'D:/ElevAge-Android-Validation-20261023-01/operation-20261025/pub-cache'}
$sdk = if($env:ELEVAGE_FLUTTER_SDK){$env:ELEVAGE_FLUTTER_SDK}else{'D:/ElevAge-Android-Validation-20261023-01/operation-20261025/flutter-sdk'}
$flutter = Join-Path $sdk 'bin/flutter.bat'
$python = Join-Path $root '.venv/Scripts/python.exe'
$mobile = Test-Path (Join-Path $repo 'pubspec.yaml')
New-Item -ItemType Directory -Force -Path $env:TEMP,$env:PIP_CACHE_DIR,$env:GRADLE_USER_HOME,$env:ANDROID_USER_HOME | Out-Null
Set-Location $repo
function Git { & git.exe -c "safe.directory=$repo" @args; if($LASTEXITCODE){throw 'Git failed'} }
function RequireClean { if((& git.exe -c "safe.directory=$repo" status --porcelain)){throw 'Commit or preserve local changes first'} }
function RequireDevelopment { $branch = & git.exe -c "safe.directory=$repo" branch --show-current; if($branch -ne 'develop' -and $branch -notlike 'feature/*'){throw 'Only develop or feature/* allowed'}; return $branch }
switch($Action){
  'setup' {
    Git config core.autocrlf false
    Git config core.hooksPath .githooks
    Git config push.default simple
    Git config pull.ff only
    if(!$mobile){
      if(!(Test-Path $python)){ & python -m venv (Join-Path $root '.venv'); if($LASTEXITCODE){throw 'venv failed'} }
      & $python -m pip install -r requirements.txt; if($LASTEXITCODE){throw 'Python dependencies failed'}
    } else {
      $expected = (Get-Content tools/flutter-revision.txt -Raw).Trim()
      $actual = & git.exe -c "safe.directory=$sdk" -C $sdk rev-parse HEAD
      if($LASTEXITCODE -or $actual -ne $expected){throw 'Flutter SDK revision mismatch'}
      & $flutter pub get --offline --enforce-lockfile; if($LASTEXITCODE){throw 'Flutter dependencies failed'}
    }
  }
  'start' {
    if($Name -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$'){throw 'Supply -Name with lowercase letters, digits and hyphens'}
    RequireClean
    Git fetch origin
    Git switch develop
    Git merge --ff-only origin/develop
    Git switch -c "feature/$Name"
  }
  'test' {
    if($mobile){
      if(!$Tests.Count){$Tests=@('test/password_reset_test.dart','test/permission_contract_test.dart','test/financial_indicators_test.dart','test/production_network_safety_test.dart')}
      & $flutter test --no-pub --concurrency=1 @Tests
    } else {
      $opts=@(); if($Postgres){$opts+='--postgres'}
      & $python tools/test_local.py @opts @Tests
    }
    if($LASTEXITCODE){throw 'Targeted tests failed'}
  }
  'build-web' {
    if(!$mobile){throw 'Use this action in mobile'}
    & $flutter build web --release --no-pub --no-wasm-dry-run --dart-define=APP_ENV=production --dart-define=API_URL=https://backend-elevage.onrender.com/api
    if($LASTEXITCODE){throw 'Web build failed'}
  }
  'save' {
    $null=RequireDevelopment
    if(!$Message){throw 'Supply -Message'}
    Git add --all
    & $python tools/check_index.py
    if($LASTEXITCODE){throw 'Staged secret scan failed'}
    Git commit -m $Message
  }
  'push' {
    RequireClean
    $branch=RequireDevelopment
    Git push --set-upstream origin "HEAD:refs/heads/$branch"
  }
}
