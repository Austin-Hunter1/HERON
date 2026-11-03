"""Per-frame shoreline refinement.

The world-anchored water map gives a stable prior. In a narrow band around the projected
shoreline, the decision is re-made from THIS frame's pixels: local colour models for water
and land are fitted from confident pixels on each side (Lab, full covariance), the
likelihood ratio is combined with the prior, and the result is edge-aware smoothed with a
colour-guided filter. The prior keeps it stable; the band lets the edge sit exactly on the
real waterline even if the pose is off by a few pixels.
"""
import numpy as np, cv2

def _gauss_ll(X, mu, C):
    C = C + np.eye(3) * 4.0; Ci = np.linalg.inv(C); d = X - mu
    return -0.5 * np.einsum("ij,jk,ik->i", d, Ci, d) - 0.5 * np.log(np.linalg.det(C))

def refine(frame_bgr, prior, valid, scale=0.5, band_px=18, prior_w=1.6):
    h, w = prior.shape
    sw, sh = int(w * scale), int(h * scale)
    fr = cv2.resize(frame_bgr, (sw, sh), interpolation=cv2.INTER_AREA)
    p = cv2.resize(prior, (sw, sh), interpolation=cv2.INTER_LINEAR)
    v = cv2.resize(valid.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0
    lab = cv2.cvtColor(fr, cv2.COLOR_BGR2LAB).astype(np.float32)
    m0 = (p > 0.5).astype(np.uint8)
    if m0.sum() < 200 or (1 - m0)[v].sum() < 200: return prior
    b = max(3, int(band_px * scale))
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    band = (cv2.dilate(m0, k(b)) - cv2.erode(m0, k(b))).astype(bool) & v
    wat = cv2.erode(m0, k(b + 2)).astype(bool) & cv2.dilate(m0 ^ 1, k(6 * b)).astype(bool) & v      # water near the shore
    lnd = cv2.erode(1 - m0, k(b + 2)).astype(bool) & cv2.dilate(m0, k(6 * b)).astype(bool) & v       # land near the shore
    glint = lab[..., 0] > 245
    wat &= ~glint; lnd &= ~glint
    if wat.sum() < 100 or lnd.sum() < 100: return prior
    Xw, Xl = lab[wat], lab[lnd]
    rng = np.random.default_rng(0)
    if len(Xw) > 20000: Xw = Xw[rng.choice(len(Xw), 20000, replace=False)]
    if len(Xl) > 20000: Xl = Xl[rng.choice(len(Xl), 20000, replace=False)]
    muw, Cw = Xw.mean(0), np.cov(Xw.T); mul, Cl = Xl.mean(0), np.cov(Xl.T)
    X = lab[band]
    llr = np.clip(_gauss_ll(X, muw, Cw) - _gauss_ll(X, mul, Cl), -12, 12)
    pp = np.clip(p[band], 1e-3, 1 - 1e-3); logit = np.log(pp / (1 - pp))
    post = p.copy(); post[band] = 1 / (1 + np.exp(-(0.5 * llr + prior_w * logit)))
    post = cv2.ximgproc.guidedFilter(fr, (post * 255).astype(np.uint8), max(2, int(4 * scale * 2)), 400).astype(np.float32) / 255
    # keep only components connected to the prior water (no stray land blobs)
    mb = (post > 0.5).astype(np.uint8); n, lab_, st, _ = cv2.connectedComponentsWithStats(mb, 8)
    if n > 1:
        ov = np.bincount(lab_.ravel(), weights=m0.ravel().astype(np.float32), minlength=n)
        bad = ov < 0.3 * st[:, 4]; bad[0] = False
        post[bad[lab_]] = np.minimum(post[bad[lab_]], 0.3)
    return cv2.resize(post, (w, h), interpolation=cv2.INTER_LINEAR)
