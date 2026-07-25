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
