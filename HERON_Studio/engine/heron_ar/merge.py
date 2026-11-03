"""Merge several georeferenced SfM chunks (overlapping time windows) into one pose file."""
import numpy as np

def merge(out, parts, log=print):
    Z = [np.load(p) for p in parts]
    vt = np.concatenate([z["vt"] for z in Z]); o = np.argsort(vt)
    keep = np.r_[True, np.diff(vt[o]) > 0.1]; o = o[keep]          # overlap: keep one keyframe per instant
    cat = lambda k: np.concatenate([z[k] for z in Z])[o]
    np.savez(out, frames=cat("frames"), vt=vt[o], Rwc=cat("Rwc"), Cw=cat("Cw"), Rimu=cat("Rimu"),
             cam_params=np.median(np.stack([z["cam_params"] for z in Z]), 0), points=np.concatenate([z["points"] for z in Z]))
    log(f"{out}: {len(o)} keyframes from {len(parts)} chunks")
