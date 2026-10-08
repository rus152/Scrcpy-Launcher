<#
Rebuilds launcher/resources/scrcpy-server-5.0: the official scrcpy 5.0 server with
vdm-display.patch applied, so the new display is owned by a VirtualDeviceManager
virtual device and keeps running while the phone's own screen is off.

Needs git, a JDK in JAVA_HOME and the Android SDK in ANDROID_HOME. Mirrors scrcpy's
own server/build_without_gradle.sh.
#>
param(
    [string]$AndroidHome = $env:ANDROID_HOME,
    [string]$Platform = "android-37.0",
    [string]$BuildTools = "36.0.0"
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$Version = "5.0"
$Commit = "19871982cefb9de4c981c2314dfda2ac81564b48"  # tag v5.0
$Output = Join-Path $PSScriptRoot "..\..\launcher\resources\scrcpy-server-$Version"
$Work = Join-Path ([IO.Path]::GetTempPath()) "scrcpy-server-build"
$AndroidJar = "$AndroidHome\platforms\$Platform\android.jar"
$Tools = "$AndroidHome\build-tools\$BuildTools"
$Gen = "$Work\build\gen"
$Classes = "$Work\build\classes"

Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
git -c core.autocrlf=false clone --quiet --depth 1 --branch "v$Version" https://github.com/Genymobile/scrcpy $Work
if ((git -C $Work rev-parse HEAD) -ne $Commit) { throw "Tag v$Version no longer points to $Commit" }
git -C $Work apply "$PSScriptRoot\vdm-display.patch"

New-Item -ItemType Directory -Force "$Gen\com\genymobile\scrcpy", $Classes | Out-Null
Set-Content "$Gen\com\genymobile\scrcpy\BuildConfig.java" @"
package com.genymobile.scrcpy;

public final class BuildConfig {
  public static final boolean DEBUG = false;
  public static final String VERSION_NAME = "$Version";
}
"@

# Absolute paths: the Windows aidl.exe cannot resolve packages against a relative "-I."
$Aidl = "$Work\server\src\main\aidl"
& "$Tools\aidl.exe" "-o$Gen" "-I$Aidl" "$Aidl\android\content\IOnPrimaryClipChangedListener.aidl"
& "$Tools\aidl.exe" "-o$Gen" "-I$Aidl" -p "$AndroidHome\platforms\$Platform\framework.aidl" "$Aidl\android\view\IDisplayWindowListener.aidl"

Get-ChildItem "$Work\server\src\main\java", $Gen -Recurse -Filter *.java |
    ForEach-Object { $_.FullName.Replace("\", "/") } | Set-Content "$Work\build\sources.txt"
& "$env:JAVA_HOME\bin\javac.exe" -encoding UTF-8 -source 1.8 -target 1.8 -Xlint:-options -nowarn `
    -bootclasspath $AndroidJar -cp "$Tools\core-lambda-stubs.jar" -d $Classes "@$Work\build\sources.txt"
& "$env:JAVA_HOME\bin\jar.exe" cf "$Work\build\classes.jar" -C $Classes .
& "$Tools\d8.bat" --release --classpath $AndroidJar --output "$Work\build\server.zip" "$Work\build\classes.jar"

Move-Item "$Work\build\server.zip" $Output -Force
Remove-Item $Work -Recurse -Force
Write-Host "Server generated in $(Resolve-Path $Output)"
