# U2-Net small ONNX model

`u2netp.onnx` is the 4,574,861-byte U2-Net small model distributed by the
rembg project, downloaded without modification from:
https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx

SHA-256: `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`
MD5 (also pinned by rembg): `8e83ca70e441ab06c318d82300c84806`

U2-Net authors: Xuebin Qin, Zichen Zhang, Chenyang Huang, Masood Dehghan,
Osmar R. Zaiane, and Martin Jagersand (2020).
Upstream: https://github.com/xuebinqin/U-2-Net
Upstream license: Apache License 2.0; the unmodified license is included in
`u2netp.LICENSE.txt`. No upstream NOTICE file was present in the repository.

The rembg maintainers identify the U2-Net model's license as upstream Apache
2.0; rembg's MIT license covers rembg source code rather than all models:
https://github.com/danielgatis/rembg/issues/837
The historical ONNX export's exact conversion command/checkpoint is not
recorded by the rembg release; this file records that provenance limitation.
No rembg source code is bundled or required by this application.

Paper: Qin et al., "U2-Net: Going Deeper with Nested U-Structure for Salient
Object Detection," Pattern Recognition 106 (2020), 107404.
https://arxiv.org/abs/2005.09007

This model predicts a candidate alpha mask locally. Original photo RGB is
never regenerated. A separate all-person coverage audit is required before
accepting a predicted cutout; unavailable or rejected masks retain the full
original photo. The application never downloads models at runtime.
