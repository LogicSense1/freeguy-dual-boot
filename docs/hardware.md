# HDMI, CEC and remote voice

[简体中文](hardware.zh-CN.md)

These components adapt the tested freeguy firmware to the local Google TV
environment. They are not portable replacement drivers. The original kernel,
panel initialization, TV middleware, audio HAL and Bluetooth decoder remain
dependencies supplied by the owner.

## HDMI and CEC

`hdmi/` registers the three physical inputs through Android's TV Input Framework
and the original TV HAL. It acquires the video surface, enters the original
input mode, and restores application mode when the last session closes.

The high-refresh-rate fix waits for stable input timing, then reapplies the
current backlight value through the original `UPDATE_ONLY` operation. It does
not replace panel tables, choose new electrical parameters or change the saved
brightness. This corrected the observed 144 Hz black screen.

Only the active, visible CEC session forwards navigation and media keys.
Back goes to the connected device; Home, power, volume and voice stay with the
TV. Device names come from CEC. Without a name, the label falls back to
`HDMI 1`, `HDMI 2` or `HDMI 3`. There is no fourth physical input or new source
selection page. `hdmi-label/` adjusts the existing menu's label presentation.

Build both APKs with your local Android tools and signing key:

```sh
python3 tools/build-hdmi-apps.py \
  --android-jar /path/to/android.jar \
  --aapt2 /path/to/aapt2 \
  --d8-jar /path/to/r8.jar \
  --apksigner-jar /path/to/apksigner.jar \
  --java-home /path/to/jdk-17 \
  --keystore /private/signing.p12 \
  --keystore-password-file /private/signing-password.txt \
  --output /private/new-hdmi-build
```

The builder runs CEC ownership/key-release and timing-transition tests, verifies
APK signatures, and checks that compile-only framework stubs are absent from
the DEX. Installing the APKs alone does not supply the required privileged
permissions, original services or runtime component configuration.

## Remote and voice

`voice/` reconnects an already bonded Xiaomi remote. It does not create or remove
bonds. Both systems need a separately prepared, consistent copy of the owner's
pairing information; this source bundle does not yet include that provisioning
procedure. Never import another TV's pairing data.

The bridge receives the physical voice-key reports, uses the locally supplied
OEM decoder, and supplies PCM through the original audio HAL socket. A small
memory buffer retains the start of speech while the official assistant opens.
The assistant process stays running between requests to avoid repeated
“Gemini is starting” delays.

Hold the voice key while speaking. The tested remote stops sending audio when
the key is released. This is not hands-free listening. The bridge writes timing
and signal statistics, not audio recordings; in Google mode the official
assistant processes the speech under the user's account and service settings.
Wake registration is volatile and uses the existing bond's address.

```sh
python3 tools/build-voice.py \
  --oem-apk /private/inputs/decoder.apk \
  --oem-sha256 YOUR_VERIFIED_APK_SHA256 \
  --oem-jni /private/inputs/libmibluetooth_jni.so \
  --jni-sha256 YOUR_VERIFIED_JNI_SHA256 \
  --android-jar /path/to/android.jar \
  --d8-jar /path/to/r8.jar \
  --java-home /path/to/jdk-17 \
  --output /private/new-voice-build
```

The compiler reads twelve required declarations from the supplied APK and
checks the expected ABI. Generated declarations are excluded from the DEX.
The output payload also contains the owner's APK/JNI, so keep the whole build
directory private. Build success does not prove microphone transport, recognition
or reconnection on another remote.

## Built-in microphone and component setup

`runtime/voc-primary-compat.c` adapts Android's built-in microphone device and
channel masks to the original four-channel VOC input. It delegates output and
unrelated inputs to the original audio HAL. Opening or enumerating an input
does not start capture; reading does. Mute, ownership and cleanup checks prevent
the adapter from taking over an unrelated VOC handle.

```sh
python3 tools/build-audio-adapter.py \
  --clang /path/to/clang \
  --ld-lld /path/to/ld.lld \
  --output /private/new-microphone-build
```

Clang must support the ARM Android target and host AddressSanitizer/UBSan.
The builder runs sixteen synthetic contract scenarios and routing-string tests.
The adapter depends on the exact original HAL layout and original VAD firmware
startup. Do not load it against a different firmware version based only on a
matching CPU or exported symbol name.

`tools/render-runtime-policy.py` pins the locally built HDMI APKs and audio
adapter, then emits the component setup script. In Google it disables the
incompatible donor sound/picture providers, duplicate HDMI service and related
services, enables the native-label overlay, and checks the resulting state.
The startup wrapper reports ready only after this setup succeeds.

```sh
python3 tools/render-runtime-policy.py \
  --hdmi-apk /private/new-hdmi-build/OriginalHdmi.apk \
  --label-apk /private/new-hdmi-build/NativeHdmiLabel.apk \
  --audio-adapter /private/new-microphone-build/builtin-mic-primary.so \
  --output /private/runtime-policy.sh
```

Rendering does not install or execute the script. Integration into the complete
runtime is required; copying these files into a running TV is not an installation
procedure.

## Evidence and limits

On the tested installation, the owner confirmed:

- HDMI 1 and 2 picture and audio; HDMI 3 at 3840×2160, 60 Hz and 144 Hz.
- Returning to the desktop and re-entering HDMI 3; changing 60↔144 Hz.
- PS5 picture, audio, CEC navigation, Back and the `PlayStation 5` label after
  unplugging and reconnecting HDMI.
- Remote navigation in both systems using the same pairing, and held-key
  Google speech recognition after startup and wake.

Finite built-in microphone checks returned audio samples, including ordinary
mono and stereo application requests. Those checks measured signal levels
without saving recordings. Remote speech and playback still worked afterward.

Some wake attempts needed a second power-key press. The owner accepted that
behavior; its cause has not been established. Releasing the voice key before
speaking does not work. Most picture-setting controls remain incomplete.
USB storage, Bluetooth audio and ARC/eARC were not functionally verified.
These results do not establish Netflix certification or protected 4K playback.

The independently rebuilt runtime has started with these components. Public
builders are checked separately from physical acceptance; future changes need
new tests for the paths they affect.
