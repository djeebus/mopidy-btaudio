**************
Mopidy-BtAudio
**************

Mopidy extension that plays audio from a2dp sources

- Auto connects to known bluetooth devices on startup
- Enables discoverable mode when no devices are connected
- Auto accepts pairing requests
- Pauses music when bluetooth device starts playing
- Resumes music when the bluetooth device stops playing


*****************
* Bluetooth RPC *
*****************

May require adding `--compat` to the bluetoothd process, and
running `sudo sdptool add SP`.
