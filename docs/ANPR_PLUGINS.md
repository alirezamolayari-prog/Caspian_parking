# ANPR engine plug-ins

Written for: the installer / integrator who connects a plate-reading engine to Caspian Parking.

Caspian Parking does not ship a plate-reading model. No Iranian-plate model with a licence that allows
closed-source commercial use was available (see `docs/DECISIONS.md`, D‑076). The app works without one:

| Camera type (Settings → Devices → Cameras) | Engine needed? |
|---|---|
| Smart ANPR camera (reads plates itself, pushes JSON over HTTP) | no — leave the engine as `none` |
| Network camera (RTSP / ONVIF) | yes — a plug-in, otherwise only the live preview and manual entry |
| Simulator | no (`simulator`, for training and tests) |

## Smart ANPR camera (HTTP push)

Set the camera type to *smart* and a port (e.g. `8081`), then configure the camera to POST to
`http://<gate PC>:<port>/plate` with a JSON body:

```json
{"plate": "12ب345-22", "confidence": 0.93, "vehicle_type": "sedan", "image": "<base64 JPEG>"}
```

- `plate` may be `null` (vehicle seen, plate unreadable). The result appears under *Unidentified passes*.
- To send several frames of one pass, use `{"reads": [{"plate": …, "confidence": …}, …], "image": …}`.
  The app votes per character over the frames.
- `vehicle_type` is one of `sedan`, `van`, `truck`, `motorcycle`, `other`.
- Allow the port in Windows Firewall for the camera's IP only.

## Engine plug-in for RTSP cameras

1. Put a Python file into `<data root>\anpr\`, for example `vendor_engine.py`.
2. In the camera row set *engine* to `plugin:vendor_engine.py:VendorEngine`.
3. Restart the app. If the plug-in fails to load, the camera still shows its preview and the operator
   types plates; the error is written to the log.

The class needs a `name` attribute and a `read(frame)` method. `frame` is an OpenCV BGR `numpy` array.
Return one `FrameRead` per plate seen in that frame (an empty list when nothing is in view):

```python
from caspian_parking.core.anpr import FrameRead


class VendorEngine:
    name = "vendor"

    def __init__(self) -> None:
        import vendor_sdk  # the vendor's licensed SDK, or onnxruntime with a licensed model

        self.reader = vendor_sdk.Reader(license_file=r"C:\ParkingData\anpr\license.key")

    def read(self, frame) -> list[FrameRead]:
        results = self.reader.detect(frame)
        return [FrameRead(r.text, r.score, r.vehicle_class, r.vehicle_score) for r in results]
```

- `text` must be the plate as the vendor prints it (Persian or Latin digits, with or without separators);
  the app normalises it. Unparseable text counts as unreadable.
- `confidence` is 0–1. The camera's *minimum confidence* setting decides when a read is trusted.
- `read` is called on a worker thread for every frame; keep it under ~50 ms per frame. Exceptions are
  logged and the frame is skipped.
- Consecutive frames with reads form one pass; a pass ends after 4 frames without a plate (or 25 frames).
  The best frame is saved as the photo.

Check the vendor SDK's licence: it must allow use in a commercial, closed-source product, and its
runtime files must be installed on every gate PC that uses the plug-in.
