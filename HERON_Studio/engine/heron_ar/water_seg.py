"""Water segmentation v2: GrabCut seeded by confident colour cues and the mapped full-pool
outline, refined at full resolution with a guided filter so the edge follows the real shoreline."""
import numpy as np, cv2

def segment_water_v2(bgr, prior, horizon_mask, scale=0.35):
    h, w = bgr.shape[:2]; sw, sh = int(w * scale), int(h * scale)
    small = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA)
    pr = cv2.resize(prior, (sw, sh), interpolation=cv2.INTER_NEAREST).astype(bool)
    hz = cv2.resize(horizon_mask, (sw, sh), interpolation=cv2.INTER_NEAREST).astype(bool)
    b, g, r = [small[:, :, i].astype(np.int16) for i in range(3)]
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV); hue, sat, val = [hsv[:, :, i].astype(np.int16) for i in range(3)]
    blue = (b - r > 30) & (b > 110) & (hue > 88) & (hue < 125) & (sat > 50)
    neutral = (b - r > -4) & (sat < 45) & (val > 70) & (val < 230)
    tan = (r - b > 22)                                   # dry lakebed / vegetation
    veg = (g > b + 10) & (g >= r - 5)
    mask = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    mask[pr & hz & neutral] = cv2.GC_PR_FGD
    mask[pr & hz & cv2.erode(blue.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)] = cv2.GC_FGD
    mask[(~pr) | (~hz)] = cv2.GC_BGD
    mask[pr & hz & (tan | veg) & ~blue] = cv2.GC_BGD
    if (mask == cv2.GC_FGD).sum() < 50:
        return np.zeros((h, w), np.uint8)
    bg = np.zeros((1, 65), np.float64); fg = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, None, bg, fg, 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return np.zeros((h, w), np.uint8)
    m = np.isin(mask, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8)
    # keep regions anchored by confident blue water
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    bc = np.bincount(lab.ravel(), weights=blue.ravel().astype(np.float32), minlength=n)
    keep = (bc > np.maximum(0.05 * st[:, 4], 60)) & (st[:, 4] > sw * sh * 3e-4); keep[0] = False
    m = keep[lab].astype(np.float32)
    up = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
    try:
        guide = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        up = cv2.ximgproc.guidedFilter(guide, (up * 255).astype(np.uint8), 8, 200).astype(np.float32) / 255
    except Exception:
        pass
    return ((up > 0.5) & (cv2.resize(horizon_mask, (w, h), interpolation=cv2.INTER_NEAREST) > 0)).astype(np.uint8)
