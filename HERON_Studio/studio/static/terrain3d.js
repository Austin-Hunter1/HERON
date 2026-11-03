// 3D shore model: height grid from /model3d, optional drone photo texture, water plane, flown track.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";

export function mountTerrain(el, m, fid) {
  const W = m.w, H = m.h, c = m.cell;
  const raw = Uint8Array.from(atob(m.heights_cm_b64), (ch) => ch.charCodeAt(0));
  const hc = new Int16Array(raw.buffer);
  const hgt = new Float32Array(W * H); let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < W * H; i++) {
    const v = hc[i] === -32768 ? NaN : hc[i] / 100; hgt[i] = v;
    if (v === v) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  const floor = Math.max(lo, -4) - 0.6;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })   // lets "Save view" grab the canvas;
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));              // HiDPI without melting a laptop GPU
  el.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(45, 1, 1, 20000); cam.up.set(0, 0, 1);
  const ctl = new OrbitControls(cam, renderer.domElement); ctl.enableDamping = true; ctl.maxPolarAngle = Math.PI * 0.49;
  scene.add(new THREE.HemisphereLight(0xdfefff, 0x1a2630, 1.15));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(-600, -400, 900); scene.add(sun);

  // terrain mesh, centred on the grid
  const cx = W * c / 2, cy = H * c / 2;
  const geo = new THREE.PlaneGeometry(W * c, H * c, W - 1, H - 1);
  const pos = geo.attributes.position, col = new Float32Array(pos.count * 3), uv = geo.attributes.uv;
  const ramp = (h) => {                                                   // lakebed → shore → grass
    const t = Math.max(0, Math.min(1, (h + 0.5) / 4));
    const a = [0.55, 0.50, 0.40], b = [0.42, 0.47, 0.30], d = [0.30, 0.38, 0.24];
    const k = t < 0.5 ? t * 2 : (t - 0.5) * 2, p = t < 0.5 ? a : b, q = t < 0.5 ? b : d;
    return [p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k, p[2] + (q[2] - p[2]) * k];
  };
  const ob = m.ortho_box;
  for (let r = 0; r < H; r++) for (let x = 0; x < W; x++) {
    const i = r * W + x, vi = (H - 1 - r) * W + x;                     // PlaneGeometry rows run north→south
    const h = hgt[i];
    const rgb = h === h ? ramp(h) : [0.07, 0.1, 0.13];
    col.set(rgb, vi * 3);
    if (ob) {
      const e = m.origin_en[0] + x * c, n = m.origin_en[1] + r * c;
      uv.setXY(vi, (e - ob.en[0]) / (ob.en[2] - ob.en[0]), (n - ob.en[1]) / (ob.en[3] - ob.en[1]));
    }
  }
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  // frame the part that has heights, not the whole (mostly empty) grid
  let x0 = W, x1 = 0, r0 = H, r1 = 0;
  for (let r = 0; r < H; r++) for (let x = 0; x < W; x++) if (hgt[r * W + x] === hgt[r * W + x]) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); r0 = Math.min(r0, r); r1 = Math.max(r1, r); }
  const focus = new THREE.Vector3(((x0 + x1) / 2) * c - cx, ((r0 + r1) / 2) * c - cy, 0);
  const span = Math.max(1, Math.max(x1 - x0, r1 - r0) * c);
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, metalness: 0, side: THREE.DoubleSide });
  const terrain = new THREE.Mesh(geo, mat); scene.add(terrain);
  let photo = null;
  if (m.ortho && ob) {
    // photo map PNG has transparency where there's no photo; the height-coloured mesh underneath shows there
    new THREE.TextureLoader().load(`/api/flights/${fid}/media/ortho.png`, (t) => {
      t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
      photo = new THREE.MeshStandardMaterial({ map: t, roughness: 0.95, side: THREE.DoubleSide, alphaTest: 0.5 });
      terrain.material = photo; under.visible = true;
    });
  }
  const under = new THREE.Mesh(geo, mat); under.visible = false; under.position.z = -0.05; scene.add(under);

  const water = new THREE.Mesh(new THREE.PlaneGeometry(W * c * 1.02, H * c * 1.02),
    new THREE.MeshStandardMaterial({ color: 0x2aa7cf, transparent: true, opacity: 0.55, roughness: 0.15, metalness: 0.1 }));
  scene.add(water);

  let track = null;
  if (m.track_en?.length) {
    const pts = m.track_en.map(([e, n, z]) => new THREE.Vector3(e - cx, n - cy, z));
    track = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x3ddc97 }));
    scene.add(track);
  }

  let ex = 2, wl = 0;
  function applyZ() {
    for (let r = 0; r < H; r++) for (let x = 0; x < W; x++) {
      const h = hgt[r * W + x]; pos.setZ((H - 1 - r) * W + x, ((h === h ? h : floor)) * ex);
    }
    pos.needsUpdate = true; geo.computeVertexNormals();
    water.position.z = wl * ex + 0.02;
    if (track) { track.scale.z = 1; track.position.z = 0; }     // track keeps true height above water (drawn unscaled)
  }
  applyZ();

  const at = (dx, dy, dz) => { cam.position.set(focus.x + dx, focus.y + dy, dz); ctl.target.copy(focus); };
  const views = {
    orbit: () => at(-span * 0.45, -span * 0.6, span * 0.42),
    top: () => at(0, -1, span * 1.1),
    low: () => at(-span * 0.12, -span * 0.55, span * 0.07),
  };
  views.orbit();

  const ro = new ResizeObserver(() => { const r = el.getBoundingClientRect(); renderer.setSize(r.width, r.height, false); cam.aspect = r.width / Math.max(1, r.height); cam.updateProjectionMatrix(); });
  ro.observe(el);
  let alive = true;
  (function loop() { if (!alive) return; ctl.update(); renderer.render(scene, cam); requestAnimationFrame(loop); })();

  const ray = new THREE.Raycaster(); const api = { onProbe: null };
  window.__h3 = { renderer, scene, cam, terrain, water };
  renderer.domElement.addEventListener("click", (e) => {
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), cam);
    const hit = ray.intersectObject(terrain)[0];
    if (!hit) return api.onProbe?.(null);
    const x = Math.round((hit.point.x + cx) / c), rr = Math.round((hit.point.y + cy) / c);
    const h = hgt[Math.min(H - 1, Math.max(0, rr)) * W + Math.min(W - 1, Math.max(0, x))];
    api.onProbe?.(h === h ? { e: m.origin_en[0] + x * c, n: m.origin_en[1] + rr * c, h } : null);
  });

  return Object.assign(api, {
    setWater(v) { wl = v; water.position.z = wl * ex + 0.02; },
    setExaggeration(v) { ex = v; applyZ(); },
    setTexture(on) { if (photo) { terrain.material = on ? photo : mat; under.visible = on; } },
    show(what, on) { ({ track, water }[what] || {}).visible = on; },
    view(v) { views[v]?.(); },
    dispose() { alive = false; ro.disconnect(); renderer.dispose(); geo.dispose(); renderer.domElement.remove(); },
  });
}
