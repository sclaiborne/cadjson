"""A self-contained HTML viewer: the built part or assembly as meshes, orbit and zoom, one colour
per placed part with a list to hide and show them, section planes along X/Y/Z, a few numbers.

The page embeds the meshes as base64 typed arrays and loads three.js from a CDN, so it is one
file that opens in any browser. No server, no plugin."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

from build123d import Shape

from cadjson import __version__


def _mesh(shape: Shape, tolerance: float, angular: float) -> dict:
    verts, tris = shape.tessellate(tolerance, angular)
    pos = struct.pack(f"<{3 * len(verts)}f", *(c for v in verts for c in (v.X, v.Y, v.Z)))
    idx = struct.pack(f"<{3 * len(tris)}I", *(i for t in tris for i in t))
    bb = shape.bounding_box()
    return {
        "positions": base64.b64encode(pos).decode(),
        "indices": base64.b64encode(idx).decode(),
        "triangles": len(tris),
        "volume": round(shape.volume, 3),
        "bbox": [[round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3)],
                 [round(bb.max.X, 3), round(bb.max.Y, 3), round(bb.max.Z, 3)]],
    }


def write_viewer(part: Shape, path: Path, tolerance: float, angular: float, *, name: str,
                 pieces: list[tuple[str, Shape]] | None = None) -> Path:
    """pieces: (label, solid) for each placed part of an assembly; None for a single part."""
    items = pieces or [(name, part)]
    data = {
        "name": name,
        "version": __version__,
        "parts": [{"name": label, **_mesh(shape, tolerance, angular)} for label, shape in items],
    }
    html = TEMPLATE.replace("__TITLE__", name).replace("__DATA__", json.dumps(data))
    path.write_text(html, encoding="utf-8")
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  html, body { margin: 0; height: 100%; background: #f4f4f2; font: 13px/1.4 system-ui, sans-serif; color: #222; }
  #view { position: absolute; inset: 0; }
  #panel { position: absolute; top: 12px; left: 12px; width: 270px; background: rgba(255,255,255,.94);
           border: 1px solid #d8d8d4; border-radius: 6px; padding: 10px 12px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
  #panel h1 { font-size: 14px; margin: 0 0 6px; }
  #panel .dim { color: #666; font-size: 12px; }
  #parts { list-style: none; margin: 8px 0; padding: 0; }
  #parts li { display: flex; align-items: center; gap: 6px; padding: 2px 0; cursor: pointer; }
  #parts li input { margin: 0; }
  #parts .all { font-size: 11px; color: #666; padding: 2px 0 0 18px; }
  #parts .all a { color: #4a7ebb; cursor: pointer; margin-right: 8px; }
  .row.buttons { flex-wrap: wrap; gap: 4px; }
  button.on { background: #e4ecf7; border-color: #4a7ebb; }
  #parts li .sw { width: 12px; height: 12px; border-radius: 2px; flex: none; }
  #parts li.off { opacity: .4; }
  #parts li .v { margin-left: auto; color: #888; font-size: 11px; }
  .row { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12px; }
  .row input[type=range] { flex: 1; }
  .row label { width: 52px; }
  button { font: inherit; font-size: 12px; padding: 2px 8px; border: 1px solid #bbb; background: #fff; border-radius: 4px; cursor: pointer; }
  #hint { position: absolute; bottom: 10px; left: 12px; color: #777; font-size: 11px; }
</style>
</head>
<body>
<div id="view"></div>
<div id="panel">
  <h1 id="title"></h1>
  <div class="dim" id="dims"></div>
  <ul id="parts"></ul>
  <div class="row"><label>section X</label><input type="range" id="cx" min="0" max="1000" value="1000"></div>
  <div class="row"><label>section Y</label><input type="range" id="cy" min="0" max="1000" value="1000"></div>
  <div class="row"><label>section Z</label><input type="range" id="cz" min="0" max="1000" value="1000"></div>
  <div class="row buttons"><button data-view="iso">iso</button><button data-view="top">top</button><button data-view="front">front</button><button data-view="right">right</button><button id="fit">fit</button></div>
  <div class="row buttons"><button id="edges" class="on">edges</button><button id="hidden">hidden lines</button><button id="xray">x-ray</button></div>
</div>
<div id="hint">drag: orbit &middot; right-drag: pan &middot; wheel: zoom &middot; untick a part to hide it</div>
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
               "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/" } }
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA = __DATA__;
const COLORS = [0x4a7ebb, 0xd9822b, 0x5aa469, 0xc44e52, 0x8172b2, 0x937860, 0xda8bc3, 0x8c8c8c, 0xccb974, 0x64b5cd];

function decode(b64, Type) {
  const bin = atob(b64), buf = new ArrayBuffer(bin.length), u8 = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Type(buf);
}

const el = document.getElementById('view');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(el.clientWidth, el.clientHeight);
renderer.localClippingEnabled = true;
el.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf4f4f2);
const camera = new THREE.PerspectiveCamera(35, el.clientWidth / el.clientHeight, 0.1, 100000);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xffffff, 0x777777, 1.1));
const key = new THREE.DirectionalLight(0xffffff, 1.2); key.position.set(1, -1.5, 2); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.4); fill.position.set(-1.5, 1, -0.5); scene.add(fill);

// Each section keeps the half the default views look into: X keeps x <= c (seen from the right),
// Y keeps y >= c (seen from the front), Z keeps z <= c (seen from the top).
const clip = [new THREE.Plane(new THREE.Vector3(-1, 0, 0), 1e9), new THREE.Plane(new THREE.Vector3(0, 1, 0), 1e9), new THREE.Plane(new THREE.Vector3(0, 0, -1), 1e9)];
const bbox = new THREE.Box3();
const group = new THREE.Group();
scene.add(group);
const meshes = [];
let showEdges = true;

DATA.parts.forEach((p, i) => {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(decode(p.positions, Float32Array), 3));
  geo.setIndex(new THREE.BufferAttribute(decode(p.indices, Uint32Array), 1));
  geo.computeVertexNormals();
  const color = COLORS[i % COLORS.length];
  const mat = new THREE.MeshStandardMaterial({ color, metalness: 0.05, roughness: 0.65, side: THREE.DoubleSide, clippingPlanes: clip, clipShadows: true });
  const mesh = new THREE.Mesh(geo, mat);
  const edgeGeo = new THREE.EdgesGeometry(geo, 25);
  const edges = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial({ color: 0x1a1a1a, clippingPlanes: clip }));
  // the same edges drawn without a depth test, dashed and light: where the solid covers them they
  // show through as hidden lines; where they are visible the dark pass above paints over them
  const hiddenGeo = edgeGeo.clone();
  const hidden = new THREE.LineSegments(hiddenGeo, new THREE.LineDashedMaterial({ color: 0x7a7a7a, dashSize: 1.2, gapSize: 0.8, depthTest: false, depthWrite: false, transparent: true, opacity: 0.9, clippingPlanes: clip }));
  hidden.computeLineDistances(); hidden.visible = false; hidden.renderOrder = -1;
  const g = new THREE.Group(); g.add(mesh); g.add(hidden); g.add(edges); group.add(g);
  geo.computeBoundingBox(); bbox.union(geo.boundingBox);
  meshes.push({ g, mesh, edges, hidden, p, color });
  const li = document.createElement('li');
  li.innerHTML = `<input type="checkbox" checked><span class="sw" style="background:#${color.toString(16).padStart(6, '0')}"></span><span>${p.name}</span><span class="v">${p.volume.toLocaleString()} mm³</span>`;
  const box = li.querySelector('input');
  const setVisible = on => { g.visible = on; box.checked = on; li.classList.toggle('off', !on); };
  box.onchange = () => setVisible(box.checked);
  li.onclick = e => { if (e.target !== box) setVisible(!g.visible); };
  meshes[meshes.length - 1].setVisible = setVisible;
  document.getElementById('parts').appendChild(li);
});
if (meshes.length > 1) {
  const all = document.createElement('li'); all.className = 'all';
  all.innerHTML = '<a data-all="1">show all</a><a data-all="0">hide all</a><a data-all="solo">only selected</a>';
  all.querySelectorAll('a').forEach(a => a.onclick = e => {
    e.stopPropagation();
    if (a.dataset.all === 'solo') { const on = meshes.filter(m => m.g.visible); meshes.forEach(m => m.setVisible(on.length === 0 || on.includes(m))); }
    else meshes.forEach(m => m.setVisible(a.dataset.all === '1'));
  });
  document.getElementById('parts').appendChild(all);
}

const size = new THREE.Vector3(); bbox.getSize(size);
const center = new THREE.Vector3(); bbox.getCenter(center);
document.getElementById('title').textContent = DATA.name;
document.getElementById('dims').textContent = `${size.x.toFixed(2)} × ${size.y.toFixed(2)} × ${size.z.toFixed(2)} mm · cadjson ${DATA.version}`;

const grid = new THREE.GridHelper(Math.ceil(Math.max(size.x, size.y) * 2 / 10) * 10, Math.ceil(Math.max(size.x, size.y) * 2 / 10), 0xbbbbbb, 0xe2e2e0);
grid.rotation.x = Math.PI / 2; grid.position.z = bbox.min.z - 0.01; scene.add(grid);
scene.add(new THREE.AxesHelper(Math.max(size.x, size.y, size.z) * 0.25));

function view(dir) {
  const d = Math.max(size.x, size.y, size.z) * 2.2;
  const dirs = { iso: [1, -1, 0.8], top: [0, 0, 1], front: [0, -1, 0], right: [1, 0, 0] }[dir];
  const v = new THREE.Vector3(...dirs).normalize().multiplyScalar(d);
  camera.position.copy(center).add(v);
  controls.target.copy(center);
  controls.update();
}
['cx', 'cy', 'cz'].forEach((id, k) => {
  const axis = ['x', 'y', 'z'][k];
  document.getElementById(id).oninput = e => {
    const f = e.target.value / 1000, lo = bbox.min[axis], hi = bbox.max[axis];
    clip[k].constant = k === 1 ? -(lo + (hi - lo) * (1 - f)) + 0.01 : lo + (hi - lo) * f + 0.01;
  };
});
let showHidden = false, xray = false;
const toggle = (id, on) => document.getElementById(id).classList.toggle('on', on);
document.getElementById('edges').onclick = () => { showEdges = !showEdges; meshes.forEach(m => m.edges.visible = showEdges); toggle('edges', showEdges); };
document.getElementById('hidden').onclick = () => { showHidden = !showHidden; meshes.forEach(m => m.hidden.visible = showHidden); toggle('hidden', showHidden); };
document.getElementById('xray').onclick = () => {
  xray = !xray;
  meshes.forEach(m => { m.mesh.material.transparent = xray; m.mesh.material.opacity = xray ? 0.3 : 1; m.mesh.material.depthWrite = !xray; m.mesh.material.needsUpdate = true; });
  toggle('xray', xray);
};
document.getElementById('fit').onclick = () => view('iso');
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => view(b.dataset.view));
view('iso');

window.addEventListener('resize', () => {
  camera.aspect = el.clientWidth / el.clientHeight; camera.updateProjectionMatrix();
  renderer.setSize(el.clientWidth, el.clientHeight);
});
(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script>
</body>
</html>
"""
