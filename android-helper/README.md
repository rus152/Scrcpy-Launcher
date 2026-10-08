# Android icon helper

`IconDumper` запускается ADB-командой `app_process` без установки APK. Он
запрашивает метаданные у системного `PackageManager`, отрисовывает Android
`Drawable` в прозрачный PNG и возвращает записи через stdout.

Готовый `launcher/resources/icon-dumper.dex` является воспроизводимым
артефактом этого исходника. Для пересборки нужны Java-компилятор и D8/R8:

```powershell
java -jar ecj.jar -8 -d classes android-helper/src/io/scrcpy/launcher/IconDumper.java
java -cp r8.jar com.android.tools.r8.D8 --min-api 23 --output out classes/io/scrcpy/launcher/IconDumper.class
```

Код написан независимо; исходники AYA в проект не копируются.

# Патченный scrcpy-server

`launcher/resources/scrcpy-server-5.0` — официальный сервер scrcpy 5.0 с
`scrcpy-server/vdm-display.patch`. Лаунчер подставляет его через
`SCRCPY_SERVER_PATH`, клиент `scrcpy.exe` остаётся официальным.

Зачем: на Android 17 (aconfig-флаг `separate_timeouts`) любой обычный
виртуальный дисплей попадает в группу питания основного экрана, и окно
приложения гаснет вместе с экраном телефона. Патч создаёт дисплей через
`VirtualDeviceManager`: дисплей виртуального устройства живёт в своей группе
и продолжает работать при выключенном экране и после кнопки питания. Для этого
нужна CDM-ассоциация `COMPANION_DEVICE_APP_STREAMING` для `com.android.shell`;
лаунчер создаёт её сам (`ENSURE_VIRTUAL_DEVICE_ASSOCIATION` в `launcher/adb.py`).
Без ассоциации сервер работает как стоковый.

Пересборка (нужны git, JDK в `JAVA_HOME`, Android SDK в `ANDROID_HOME` с
platform 37 и build-tools 36.0.0):

```powershell
.\android-helper\scrcpy-server\build.ps1
```

Удалить ассоциацию с телефона:

```powershell
adb shell cmd companiondevice disassociate 0 com.android.shell 02:00:00:00:5c:01
```
