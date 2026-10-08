# Scrcpy Launcher

[Русская версия](README.ru.md)

A Windows desktop launcher built with Flet (Material Design 3) around bundled
`scrcpy 5.0` and `adb 37`. It lists ADB devices, connects them over USB or
Wi‑Fi (including Android 11+ Wireless Debugging pairing), and opens the Android
apps you pick, each in its own scrcpy virtual display window.

- English and Russian interface, switchable at any time.
- Per-app launch profiles and favorites, stored locally per device.
- App windows keep running while the phone's screen is off, including on
  Android 17.
- Device battery level in the app bar.

## Download

Grab `ScrcpyLauncher-<version>-win64.exe` from
[Releases](https://github.com/rus152/Scrcpy-Launcher/releases) and run it: it's
a single file with scrcpy and ADB inside, no Python needed. On first start it
copies scrcpy and ADB to `%LOCALAPPDATA%\ScrcpyLauncher`, where they run from
afterwards.

## Running from source

Requires Windows, Python 3.12+ and a phone with ADB debugging enabled.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

On first launch the app asks for the interface language and checks for
`scrcpy/scrcpy.exe` and `scrcpy/adb.exe`. If they are missing, it offers to
download the
[official scrcpy 5.0 Windows archive](https://github.com/Genymobile/scrcpy/releases/tag/v5.0)
and unpacks it into `scrcpy/` next to `main.py`. The download can be cancelled.
A system-wide ADB install is never used.

## Connecting a phone

The app opens on a device list. Click a phone to connect and load its apps.

### USB

1. On the phone, enable Developer options and USB debugging.
2. Plug in the cable and accept the RSA key prompt on the phone.
3. Press **Refresh** and click the device with the **Online** status.

**Unauthorized** means the phone has to be unlocked and the key accepted.
**Offline** means ADB isn't ready yet: reconnect the device or refresh the list.

### Wi‑Fi pairing (Android 11+)

1. Put the PC and the phone on the same Wi‑Fi network.
2. On the phone, open Developer options → Wireless debugging → "Pair device
   with pairing code".
3. In the app, press **Connect via IP / Pair**, then **Pair**, and enter the IP,
   the **pair port** and the code from the phone.
4. After pairing, the app looks up the separate connection port over mDNS. If
   the network blocks mDNS, enter the IP and the **connection port** from the
   Wireless debugging screen in the **Device manager**.

The pair port and the connection port are different; the pair port won't work
for connecting.

### Unsupported devices

Watches and Android TV boxes are detected and marked **Unsupported**: a watch
can't run an app on a display of its own, and a TV box opens the app on the
television's screen while the scrcpy window stays blank.

## Apps and profiles

The catalogue is built from the full output of
`pm list packages --user <current user>`, so it includes apps without a
regular launcher activity. Turn on **System packages** to see preinstalled ones.

Names and icons come from `launcher/resources/icon-dumper.dex`. The app
temporarily pushes it over ADB to `/data/local/tmp/scrcpy-launcher` and runs it
through Android's `app_process`: it isn't installed as an APK, doesn't appear
in the app list and needs no root. Android itself renders regular, vector and
adaptive icons into uniform 144×144 PNGs with the same safe zone. Three ADB
processes handle separate batches of apps in parallel. Results are kept in a
persistent local cache keyed by device, package and `versionCode`, together
with the label and system flag, so the next start only processes new or
updated apps. If the firmware blocks `app_process`, icons are extracted from
the APK over ADB instead. Scrcpy isn't involved in building the interface; it's
only used to launch the app you pick.

**Launch profile** creates a profile for one device and package. Every option
in a profile has a **Global** switch that takes the value from the global
scrcpy settings; turning it off sets an individual value, including explicitly
turning a boolean option off. Expert arguments are passed without a shell;
`--serial`, `--new-display` and `--start-app` are reserved for the launcher.

The star button adds an app to favorites. In card view, favorites appear in a
separate **★ Favorites** group above **All**; search and the system-package
filter apply to both. In table view, favorites are marked with `★` and sorted
to the top. Profiles and favorites are kept separately for each physical
device; they and the settings live in a local SQLite database in
`%LOCALAPPDATA%`, not in the project or on the phone.

Each launch runs the equivalent of:

```text
scrcpy --serial <device> --new-display[=<size>/<dpi>] --start-app=[+]<package>
```

so every Android app opens in its own virtual display window, and several can
run at once. Clicking a running app switches to its window; **Active sessions**
lists the open windows with their logs.

## Screen off and Android 17

On Android 17 (the `separate_timeouts` flag), every regular virtual display
belongs to the phone's main screen power group, so the app window goes dark
together with the phone's screen. The launcher therefore ships a patched
scrcpy server (`launcher/resources/scrcpy-server-5.0`) that creates the display
through `VirtualDeviceManager`, so it keeps running while the screen is off and
after the power button. This needs a companion-device association for
`com.android.shell`, which the launcher creates automatically on devices where
it's needed. Details, rebuild steps and how to remove the association are in
[android-helper/README.md](android-helper/README.md).

## Launcher settings

The settings button next to the title opens the interface language, the view to
start in (cards or table), the base M3 theme colour, the appearance
(light, dark or system) and whether to pick up the connected phone's
Material You accent colour.
