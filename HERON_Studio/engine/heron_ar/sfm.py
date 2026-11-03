"""Structure-from-Motion on the GoPro clip with COLMAP (pycolmap).

  1. extract()  - pull frames (every `frame_step`-th) from the clip, downscaled to `work_width`,
                  plus a mask that hides the black EIS border strips and the top edge.
  2. solve()    - SIFT features -> sequential matching (video order, `sequential_overlap`
                  neighbours) -> incremental mapping with bundle adjustment. The fisheye
                  camera starts from the Gyroflow lens profile and its focal length and
                  distortion are refined by BA.

Image names are f_<clip frame index>.jpg, which georef.py uses to recover each image's
video time. Result: <work>/sparse/<model id>/ (cameras.bin, images.bin, points3D.bin).
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np, cv2

FPS = 24000 / 1001

def extract(video, work, t0, t1, step=12, width=960, log=print, fps=FPS):
    work = Path(work); d = work / "images"; m = work / "masks"
    d.mkdir(parents=True, exist_ok=True); m.mkdir(exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened(): raise FileNotFoundError(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); h = int(round(width * H / W))
    mk = np.full((h, width), 255, np.uint8); e = int(width * 0.05); mk[:, :e] = 0; mk[:, width - e:] = 0; mk[: int(h * 0.075), :] = 0
    f0, f1 = int(t0 * fps), int(t1 * fps); cap.set(cv2.CAP_PROP_POS_FRAMES, f0); n = 0
    for f in range(f0, f1):
        ok, fr = cap.read()
        if not ok: break
        if (f - f0) % step: continue
        name = f"f_{f:06d}.jpg"
        cv2.imwrite(str(d / name), cv2.resize(fr, (width, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 93])
        cv2.imwrite(str(m / (name + ".png")), mk); n += 1
    log(f"extracted {n} frames -> {d}")
    return n

def initial_camera(lens_json, width, focal_scale=1.0):
    d = json.loads(Path(lens_json).read_text())
    K = np.array(d["fisheye_params"]["camera_matrix"], float); D = d["fisheye_params"]["distortion_coeffs"]
    s = width / d["calib_dimension"]["w"]
    return [K[0, 0] * s * focal_scale, K[1, 1] * s * focal_scale, K[0, 2] * s, K[1, 2] * s, *D]

def solve(work, lens_json, width=960, focal_scale=1.0, max_features=2500, overlap=12, log=print):
    import pycolmap
    work = Path(work); db = work / "db.db"; db.unlink(missing_ok=True)
    ext = pycolmap.FeatureExtractionOptions(); ext.sift.max_num_features = max_features
    rd = pycolmap.ImageReaderOptions(); rd.camera_model = "OPENCV_FISHEYE"
    rd.camera_params = ",".join(map(str, initial_camera(lens_json, width, focal_scale))); rd.mask_path = str(work / "masks")
    pycolmap.extract_features(str(db), str(work / "images"), camera_mode=pycolmap.CameraMode.SINGLE, reader_options=rd, extraction_options=ext)
    mo = pycolmap.SequentialPairingOptions(); mo.overlap = overlap; mo.loop_detection = False
    pycolmap.match_sequential(str(db), pairing_options=mo)
    op = pycolmap.IncrementalPipelineOptions()
    op.ba_refine_principal_point = False; op.ba_refine_extra_params = True; op.ba_refine_focal_length = True
    (work / "sparse").mkdir(exist_ok=True)
    recs = pycolmap.incremental_mapping(str(db), str(work / "images"), str(work / "sparse"), options=op)
    log("models (id: registered images): " + str({k: r.num_reg_images() for k, r in recs.items()}))
    return work / "sparse"
