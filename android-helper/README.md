# Android icon helper

[Русская версия](README.ru.md)

`IconDumper` is started by ADB through `app_process`, with no APK install. It
asks the system `PackageManager` for app metadata, renders each Android
`Drawable` into a transparent PNG and returns the records over stdout.

The prebuilt `launcher/resources/icon-dumper.dex` is a reproducible build of
this source. Rebuilding it needs a Java compiler and D8/R8:

```powershell
java -jar ecj.jar -8 -d classes android-helper/src/io/scrcpy/launcher/IconDumper.java
java -cp r8.jar com.android.tools.r8.D8 --min-api 23 --output out classes/io/scrcpy/launcher/IconDumper.class
```

The code was written independently; no AYA sources are copied into the project.

# Patched scrcpy server

`launcher/resources/scrcpy-server-5.0` is the official scrcpy 5.0 server with
`scrcpy-server/vdm-display.patch` applied. The launcher points
`SCRCPY_SERVER_PATH` at it; the `scrcpy.exe` client stays official.

Why: on Android 17 (the `separate_timeouts` aconfig flag), every regular virtual
display joins the main screen's power group, so the app window goes dark
together with the phone's screen. The patch creates the display through
`VirtualDeviceManager`: a virtual device's display lives in its own group and
keeps running while the screen is off and after the power button. This needs a
`COMPANION_DEVICE_APP_STREAMING` CDM association for `com.android.shell`, which
the launcher creates itself (`ENSURE_VIRTUAL_DEVICE_ASSOCIATION` in
`launcher/adb.py`). Without the association the server behaves like stock.

Rebuilding (needs git, a JDK in `JAVA_HOME`, and the Android SDK in
`ANDROID_HOME` with platform 37 and build-tools 36.0.0):

```powershell
.\android-helper\scrcpy-server\build.ps1
```

Removing the association from the phone:

```powershell
adb shell cmd companiondevice disassociate 0 com.android.shell 02:00:00:00:5c:01
```
