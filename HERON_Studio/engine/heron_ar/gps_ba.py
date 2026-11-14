"""Bundle adjustment with the drone's GPS track as soft position priors.

Used by the full merge of a chunked solve. Chunks joined by model_merger only through their shared
frames can bend at the ends of the flight (take-off and landing), and an ordinary bundle adjustment
has nothing to pull them straight. Here every camera with a GPS position gets a prior on its centre:

  1. similarity from the merged model to the GPS track (latency searched, robust; the clock offset is
     re-found from the camera path if the fit says the sync is wrong, as in georef.py)
  2. the model is moved into local ENU metres with that similarity
  3. pose-prior bundle adjustment: reprojection error + GPS position error (horizontal sigma
     `sigma_h`, vertical `sigma_v`, Cauchy loss so GPS glitches don't drag the solve)

The result is a normal COLMAP model, already metric and in the ENU frame; georef.py fits it again
(scale ~1) and carries on as usual.
"""
import time
import numpy as np, pycolmap
from pathlib import Path


def gps_bundle_adjust(model_dir, out_dir, cfgp, sigma_h=1.5, sigma_v=3.0, iters=150, log=print):
    from .scene import Scene
    from .geometry import load_config, clip_fps
    from .georef import _poses, _fit, _bad_fit, _resync, _gps
    cfg = load_config(cfgp); cfg.pop("sfm_poses", None); scene = Scene(cfg); fps = clip_fps(cfg)
    rec = pycolmap.Reconstruction(str(model_dir))
    fr, Rcw, C = _poses(rec)
    s, Rs, ts, res, keep, lat = _fit(scene, cfg, fps, fr, C, label="merge: ")
    if _bad_fit(s, res, keep):
        scene = _resync(scene, cfg, cfgp, fps, fr, C) or scene
        s, Rs, ts, res, keep, lat = _fit(scene, cfg, fps, fr, C, label="merge: ")
    if s < 1e-3 or keep.mean() < 0.4:
        raise RuntimeError(f"merged model doesn't fit the GPS track (scale {s:.4g}); check the clock sync")
    rec.transform(pycolmap.Sim3d(float(s), pycolmap.Rotation3d(Rs), np.asarray(ts, float)))

    # GPS positions for every posed image inside the telemetry's time span
    t = scene.tel.pt; vt = cfg["clip_video_start_s"] + fr / fps
    mt = np.array([scene.mission_t(v) + lat for v in vt]); inside = (mt > t[0]) & (mt < t[-1])
    G = _gps(scene, vt, lat)
    by_frame = {f: g for f, g, ok in zip(fr, G, inside) if ok}
    cov = np.diag([sigma_h ** 2, sigma_h ** 2, sigma_v ** 2])
    priors = []; config = pycolmap.BundleAdjustmentConfig()
    import re
    for iid, im in rec.images.items():
        if not im.has_pose: continue
        config.add_image(iid)
        f = int(re.findall(r"\d+", im.name)[0])
        if f in by_frame:
            priors.append(pycolmap.PosePrior(position=np.asarray(by_frame[f], float), position_covariance=cov,
                                             coordinate_system=pycolmap.PosePriorCoordinateSystem.CARTESIAN,
                                             corr_data_id=im.data_id))
    log(f"GPS-constrained bundle adjustment: {len(config.images):,} frames, {len(priors):,} with GPS priors "
        f"(sigma {sigma_h:g} m horizontal, {sigma_v:g} m vertical), up to {iters} iterations")
    opts = pycolmap.BundleAdjustmentOptions()
    opts.refine_principal_point = False; opts.print_summary = False
    opts.ceres.loss_function_type = pycolmap.LossFunctionType.CAUCHY      # outlier observations, as in COLMAP's mapper
    opts.ceres.solver_options.max_num_iterations = int(iters)
    opts.ceres.solver_options.minimizer_progress_to_stdout = False
    popts = pycolmap.PosePriorBundleAdjustmentOptions()
    # Huber, not Cauchy: a bent flight end starts 10-20 m off its GPS track, and a Cauchy prior would treat
    # all of it as outliers and leave the bend in (tested on the 10 Nov flight). Huber keeps a constant pull
    # beyond 2 sigma, so a lone GPS glitch can't drag hard but a whole bent section still gets straightened.
    popts.ceres.prior_position_loss_function_type = pycolmap.LossFunctionType.HUBER
    popts.ceres.prior_position_loss_scale = 2.0
    t0 = time.time()
    ba = pycolmap.create_pose_prior_bundle_adjuster(opts, popts, config, priors, rec)
    summ = ba.solve()
    cs = getattr(summ, "ceres_summary", summ)
    its = getattr(cs, "num_successful_steps", None)
    msg = (f"{its} + {getattr(cs, 'num_unsuccessful_steps', 0)} iterations, " if its is not None else "") + str(getattr(cs, "termination_type", "")) + " " + str(getattr(cs, "message", ""))
    log(f"GPS-constrained bundle adjustment done in {time.time() - t0:.0f} s: {msg.strip()[:200]}")
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True); rec.write(str(out_dir))
    return out_dir
