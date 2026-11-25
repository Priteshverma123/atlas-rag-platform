// Atlas architecture as a bench-top card-sorting line.
// node build.mjs            -> atlas-architecture.svg (dark, standalone, GitHub-safe)
// node build.mjs --light    -> atlas-architecture-light.svg
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "./iso-kit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const theme = process.argv.includes("--light") ? "light" : "dark";

// ---- card layout (screen px) -------------------------------------------
const W = 1000;
const CARD = { x: 14, y: 14, w: W - 28, h: 476 };
const LEGEND_Y = CARD.y + CARD.h + 30;
const H = LEGEND_Y + 5 * 21 + 14;

// ---- world -------------------------------------------------------------
const PLATE = { x: -244, y: -90, w: 498, d: 140, r: 10 };
const PLATE_H = 10;
const FOOT_H = 5;
const BELT_H = 12; // belt top
const RAIL_TOP = 13.5;

const fit = k.fitProjection(
  [...k.boxCorners(PLATE, -PLATE_H - FOOT_H, 0), [152, -2, 72], [-82, -70, 60], [-226, -60, 36]],
  CARD.w,
  CARD.h - 60,
  { pad: 22, azimuth: 62 },
);
const P = { ...fit, origin: [fit.origin[0] + CARD.x, fit.origin[1] + CARD.y + 30] };

const at = (point) => k.iso(point, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (points, style) => k.dotsSvg(points, style);
const box = (plan, z, h, style = { tone: "mid" }, steps = 3, bevel = 0.4) => S(k.slabOf({ r: 0.6, ...plan }, z, h, P, steps, bevel), style);
const cyl = (x, y, r, z, h, style = { tone: "mid" }, steps = 28, bevel = 0) => S(k.cylinder(x, y, r, z, h, P, steps, bevel), style);
const seg = (a, b, style) => L(k.segment(a, b, P), style);
const byDepth = (points) => [...points].sort((a, b) => k.depthOf([a[0], a[1], 0], P) - k.depthOf([b[0], b[1], 0], P));
const quad = (points, cls = "iso-top", tone = "mid") =>
  `<g class="iso-solid"><path class="iso-fill" d="${k.pathOf(points.map(at), true)}"/><path class="${cls}" d="${k.pathOf(points.map(at), true)}"/><path class="iso-line iso-edge" data-tone="${tone}" d="${k.pathOf(points.map(at), true)}"/></g>`;

// ---- parts --------------------------------------------------------------
function card(x, y, z, { w = 11, d = 8, lit = false, tone = "hi", rules = 2, dots = false } = {}) {
  const plan = { x: x - w / 2, y: y - d / 2, w, d, r: 0.8 };
  const out = [S(k.slabOf(plan, z, 1, P, 3), { tone, lit, crease: "lo" })];
  const top = z + 1;
  for (let i = 0; i < rules; i++) {
    const yy = plan.y + 2 + i * 2;
    out.push(L(k.lineOnTop([plan.x + 1.8, yy], [plan.x + w - 1.8 - i * 2.4, yy], top, P), { tone: lit ? "mid" : "lo", free: true }));
  }
  if (dots) out.push(D(k.dotGrid(plan.x + 2.2, plan.y + 2, 4, 3, 2.1, top, P), { size: 0.38, tone: "mid" }));
  return out.join("");
}

function belt(x0, x1, yc, w) {
  const bed = { x: x0, y: yc - w / 2, w: x1 - x0, d: w, r: w / 2 };
  const inner = k.insetPlan(bed, 1.6);
  const back = box({ x: x0 + w / 2, y: yc - w / 2 - 2.2, w: x1 - x0 - w, d: 2.2, r: 0.5 }, 0, RAIL_TOP);
  const bolts = (y) => {
    const pts = [];
    for (let x = x0 + w / 2 + 5; x < x1 - w / 2 - 2; x += 18) pts.push(at([x, y, RAIL_TOP]));
    return D(pts, { size: 0.42 });
  };
  const ribs = [];
  for (let x = x0 + w / 2 + 3; x < x1 - w / 2 - 1; x += 4) ribs.push(k.lineOnTop([x, inner.y], [x, inner.y + inner.d], BELT_H, P));
  const bedSvg = [
    S(k.slabOf(bed, 0, BELT_H, P, 10, 0.8), { tone: "mid" }),
    L(k.planOutline(inner, BELT_H, P, 10), { tone: "lo" }),
    L(ribs.join(""), { tone: "faint" }),
    L(k.ring(x0 + w / 2, yc, 2, BELT_H, P, 16) + k.ring(x1 - w / 2, yc, 2, BELT_H, P, 16), { tone: "lo" }),
    D([at([x0 + w / 2, yc, BELT_H]), at([x1 - w / 2, yc, BELT_H])], { size: 0.5 }),
  ].join("");
  const front = box({ x: x0 + w / 2, y: yc + w / 2, w: x1 - x0 - w, d: 2.2, r: 0.5 }, 0, RAIL_TOP);
  return {
    back: back + bolts(yc - w / 2 - 1.1),
    bed: bedSvg,
    front: front + bolts(yc + w / 2 + 1.1) + L(k.sideSeam({ x: x0 + w / 2, y: yc + w / 2, w: x1 - x0 - w, d: 2.2, r: 0.5 }, 4, P, 3), { tone: "faint" }),
  };
}

// square post standing on a rail, with a foot block and a screw
function post(x, y, z0, z1, s = 3.2) {
  return [
    box({ x: x - s / 2 - 0.8, y: y - s / 2 - 0.8, w: s + 1.6, d: s + 1.6, r: 0.6 }, z0, 1.6, { tone: "mid" }),
    box({ x: x - s / 2, y: y - s / 2, w: s, d: s, r: 0.5 }, z0 + 1.6, z1 - z0 - 1.6, { tone: "mid" }),
    D([at([x + s / 2 + 0.4, y + s / 2 + 0.4, z0 + 1.6])], { size: 0.4 }),
  ].join("");
}

function plate() {
  const out = [`<path class="iso-halo" d="${k.haloOf(PLATE, -PLATE_H - FOOT_H, PLATE_H, P)}"/>`];
  const feet = [...k.corners(PLATE, 24), [6, PLATE.y + 24], [6, PLATE.y + PLATE.d - 24]];
  for (const [x, y] of byDepth(feet)) {
    out.push(cyl(x, y, 7, -PLATE_H - FOOT_H, 1.6, { tone: "lo" }));
    out.push(cyl(x, y, 5.4, -PLATE_H - FOOT_H + 1.6, FOOT_H - 1.6, { tone: "mid" }));
  }
  out.push(S(k.slabOf(PLATE, -PLATE_H, PLATE_H, P, 8, 1.6), { tone: "mid", inner: L(k.sideSeam(PLATE, -4, P, 8), { tone: "faint" }) }));
  out.push(L(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "lo" }));
  const screws = k.corners(PLATE, 10);
  out.push(L(screws.map(([x, y]) => k.ring(x, y, 2.2, 0, P, 16)).join(""), { tone: "lo" }));
  out.push(D(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 }));
  // ruler strip along the front
  const strip = { x: -132, y: PLATE.y + PLATE.d - 13, w: 236, d: 7, r: 1 };
  out.push(S(k.slabOf(strip, 0, 0.6, P, 3), { tone: "lo", crease: "none" }));
  const t = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 4, 5, strip.y + 0.8, 0.6, [2, 3.6], P);
  out.push(L(t.minor, { tone: "lo" }), L(t.major, { tone: "mid" }));
  out.push(D([at([strip.x + 2, strip.y + 3.5, 0.6]), at([strip.x + strip.w - 2, strip.y + 3.5, 0.6])], { size: 0.45 }));
  // label plate with the one engraved word
  const label = { x: 150, y: PLATE.y + PLATE.d - 18, w: 60, d: 12, r: 1.2 };
  out.push(S(k.slabOf(label, 0, 0.8, P, 3), { tone: "lo", crease: "none" }));
  out.push(L(k.planOutline(k.insetPlan(label, 1.4), 0.8, P, 3), { tone: "faint" }));
  out.push(k.faceTextSvg(k.topMatrix([label.x + 4, label.y + 6.6], 0.8, P, "x"), "ATLAS", { size: 4.6, tone: "mid" }));
  out.push(L([26, 18].map((w, i) => k.lineOnTop([label.x + 30, label.y + 4 + i * 3.4], [label.x + 30 + w, label.y + 4 + i * 3.4], 0.8, P)).join(""), { tone: "lo", free: true }));
  out.push(D([at([label.x + 2.4, label.y + 2.4, 0.8]), at([label.x + label.w - 2.4, label.y + label.d - 2.4, 0.8])], { size: 0.42 }));
  return out.join("");
}

// ---- ingestion line (back) ---------------------------------------------
const C_Y = -62;
function ingestion() {
  const b = belt(-214, -108, C_Y, 14);
  const out = [];
  // embosser column stands behind the belt
  out.push(cyl(-134, C_Y - 15, 5, 0, 3, { tone: "lo" }));
  out.push(cyl(-134, C_Y - 15, 3.4, 3, 37, { tone: "mid" }, 28));
  out.push(L(k.sideArc(-134, C_Y - 15, 3.4, 14, P, 16) + k.sideArc(-134, C_Y - 15, 3.4, 28, P, 16), { tone: "faint" }));
  out.push(b.back, b.bed);
  // upload hopper at the start
  const hop = { x: -234, y: C_Y - 13, w: 22, d: 26, r: 2 };
  out.push(box(hop, 0, 24, { tone: "mid" }, 4, 0.8));
  out.push(box(k.insetPlan(hop, -1.2), 24, 3, { tone: "mid" }, 4, 0.6));
  out.push(L(k.planOutline(k.insetPlan(hop, 2.2), 27, P, 4), { tone: "lo" }));
  for (const [i, x] of [-226, -222, -218].entries()) out.push(box({ x, y: C_Y - 8 + i, w: 1, d: 15, r: 0.2 }, 27 - 6, 13 - i * 2, { tone: "hi", crease: "lo" }, 2, 0));
  out.push(L(k.sideSeam(hop, 8, P, 4), { tone: "faint" }));
  out.push(D([at([hop.x + hop.w, hop.y + 4, 20]), at([hop.x + hop.w, hop.y + hop.d - 4, 20]), at([hop.x + hop.w, hop.y + 4, 4]), at([hop.x + hop.w, hop.y + hop.d - 4, 4])], { size: 0.42 }));
  // parse: scanner bridge with a lamp bar
  out.push(post(-188, C_Y - 8.1, RAIL_TOP, 26));
  out.push(card(-188, C_Y, BELT_H, { w: 9, d: 10, rules: 3 }));
  out.push(box({ x: -191, y: C_Y - 6, w: 6, d: 12 }, 21, 3, { tone: "lo" }));
  out.push(box({ x: -192.5, y: C_Y - 10.5, w: 9, d: 21, r: 1 }, 24, 4, { tone: "mid" }, 3, 0.5));
  out.push(D([at([-188, C_Y - 8, 28]), at([-188, C_Y + 8, 28])], { size: 0.42 }));
  out.push(post(-188, C_Y + 8.1, RAIL_TOP, 24));
  // chunk: guillotine
  out.push(post(-164, C_Y - 8.1, RAIL_TOP, 36));
  out.push(card(-156, C_Y - 2, BELT_H, { w: 5, d: 6, rules: 1 }), card(-150, C_Y + 2, BELT_H, { w: 5, d: 6, rules: 1 }));
  out.push(box({ x: -164.7, y: C_Y - 6.4, w: 1.4, d: 12.8, r: 0.2 }, 18, 18, { tone: "hi", crease: "lo" }, 2, 0));
  out.push(box({ x: -167, y: C_Y - 10.5, w: 6, d: 21, r: 1 }, 36, 4, { tone: "mid" }, 3, 0.5));
  out.push(cyl(-164, C_Y, 2.2, 40, 4, { tone: "mid" }, 20));
  out.push(box({ x: -164.8, y: C_Y - 0.8, w: 1.6, d: 1.6, r: 0.4 }, 44, 7, { tone: "mid" }, 2, 0));
  out.push(cyl(-164, C_Y, 2.6, 51, 3.4, { tone: "mid" }, 20));
  out.push(L(k.knurl(-164, C_Y, 2.6, 51, 54.4, 18, P), { tone: "lo" }));
  out.push(post(-164, C_Y + 8.1, RAIL_TOP, 36));
  // embed: embosser on its column
  out.push(card(-134, C_Y, BELT_H, { w: 7, d: 7, rules: 0, dots: true }));
  out.push(card(-118, C_Y + 1, BELT_H, { w: 7, d: 7, rules: 0, dots: true }));
  out.push(cyl(-134, C_Y, 4.6, 18, 3, { tone: "hi", crease: "lo" }, 28, 0.4));
  out.push(cyl(-134, C_Y, 1.6, 21, 15, { tone: "mid" }, 16));
  out.push(box({ x: -138, y: C_Y - 19, w: 8, d: 23, r: 1.5 }, 36, 5, { tone: "mid" }, 3, 0.6));
  out.push(cyl(-134, C_Y, 7, 41, 1.8, { tone: "mid" }, 36));
  out.push(L(k.ring(-134, C_Y, 5, 42.8, P, 32), { tone: "lo" }));
  out.push(L(k.radialTicks(-134, C_Y, 6.6, 12, 12, 42.8, [1.4, 1.4], P).minor, { tone: "lo" }));
  out.push(D([at([-134, C_Y, 42.8])], { size: 0.5, tone: "hi" }));
  out.push(b.front);
  // feed throat into the cabinet
  out.push(box({ x: -110, y: C_Y - 7, w: 10, d: 14, r: 1 }, 0, 18, { tone: "mid" }, 3, 0.5));
  out.push(L(k.planOutline({ x: -108, y: C_Y - 5, w: 6, d: 10, r: 0.8 }, 18, P, 3), { tone: "lo" }));
  return out.join("");
}

// ---- drawer cabinet: the index ------------------------------------------
const CAB = { x: -100, y: -82, w: 70, d: 40, r: 2 };
const CAB_TOP = 50;
const FRONT = CAB.y + CAB.d;
function cabinet() {
  const out = [];
  out.push(box(k.insetPlan(CAB, 1.5), 0, 4, { tone: "lo" }, 4, 0.4));
  const side = CAB.x + CAB.w;
  const vents = [];
  for (let y = CAB.y + 8; y < FRONT - 6; y += 4) vents.push(k.pathOf(k.roundedPlan({ x: y - 0.7, y: 34, w: 1.4, d: 10, r: 0.7 }, 3).map(([u, z]) => at([side, u, z])), true));
  out.push(S(k.slabOf(CAB, 4, CAB_TOP - 4, P, 4, 0.8), { tone: "mid", inner: L(vents.join(""), { tone: "lo" }) + L(k.sideSeam(CAB, 8, P, 4), { tone: "faint" }) }));
  out.push(box(k.insetPlan(CAB, -1.5), CAB_TOP, 3, { tone: "mid" }, 4, 0.6));
  out.push(D(k.corners(k.insetPlan(CAB, -1.5), 3.5).map(([x, y]) => at([x, y, CAB_TOP + 3])), { size: 0.45 }));
  // drawers 3 x 3 on the front face
  const cols = [-97.5, -75, -52.5];
  const rows = [[7, 17], [19.5, 29.5], [32, 42]];
  for (const [r, [z0, z1]] of rows.entries()) {
    for (const [c, x] of cols.entries()) {
      const open = r === 0 && c === 0 ? 6 : 0;
      const f = { x, y: FRONT - 0.2 + open, w: 20, d: 1.4, r: 0.4 };
      if (open) {
        out.push(box({ x: x + 1, y: FRONT, w: 18, d: open, r: 0.2 }, z0 + 1, z1 - z0 - 3, { tone: "mid" }, 2, 0));
        for (let i = 0; i < 6; i++) out.push(box({ x: x + 2.5 + i * 2.6, y: FRONT + 0.5, w: 0.8, d: open - 1, r: 0.2 }, z1 - 2, 1.5, { tone: "hi", crease: "lo" }, 2, 0));
      }
      out.push(box(f, z0, z1 - z0, { tone: "mid" }, 2, 0.4));
      const fy = f.y + f.d;
      const holder = [[x + 6, z1 - 3], [x + 14, z1 - 3], [x + 14, z1 - 6], [x + 6, z1 - 6]].map(([u, z]) => at([u, fy, z]));
      out.push(L(k.pathOf(holder, true), { tone: "lo" }));
      out.push(box({ x: x + 7.5, y: fy, w: 5, d: 1.6, r: 0.5 }, z0 + 1.8, 1.4, { tone: "mid" }, 2, 0));
      out.push(D([at([x + 1.6, fy, z1 - 1.6]), at([x + 18.4, fy, z1 - 1.6])], { size: 0.36, tone: "lo" }));
    }
  }
  out.push(box({ x: -97.5, y: FRONT - 0.2, w: 65, d: 1.4, r: 0.4 }, 44, 4, { tone: "lo" }, 2, 0));
  return out.join("");
}

// ---- pickers over the two lanes ---------------------------------------
const DENSE_Y = -16;
const SPARSE_Y = 20;
function densePicker() {
  const x = -84;
  const out = [];
  out.push(box({ x: x - 3, y: -72, w: 6, d: 60, r: 1 }, CAB_TOP + 3, 5, { tone: "mid" }, 3, 0.5));
  out.push(D([at([x, -66, 58]), at([x, -48, 58])], { size: 0.42 }));
  out.push(box({ x: x - 3, y: -12, w: 6, d: 2, r: 0.4 }, CAB_TOP + 3, 6, { tone: "mid" }, 2, 0));
  out.push(cyl(x, DENSE_Y, 1.3, 26, 26, { tone: "mid", crease: "none" }, 16));
  out.push(box({ x: x - 5.5, y: DENSE_Y - 5.5, w: 11, d: 11, r: 1.2 }, 51, 9, { tone: "mid" }, 3, 0.6));
  out.push(D(k.corners({ x: x - 5.5, y: DENSE_Y - 5.5, w: 11, d: 11 }, 2).map(([a, b]) => at([a, b, 60])), { size: 0.4 }));
  out.push(cyl(x + 5.5 + 2, DENSE_Y + 2, 2.4, 53, 4, { tone: "mid" }, 20));
  out.push(L(k.knurl(x + 7.5, DENSE_Y + 2, 2.4, 53, 57, 16, P), { tone: "lo" }));
  // magnet puck
  out.push(cyl(x, DENSE_Y, 2.6, 23.5, 2.5, { tone: "mid" }, 20));
  out.push(cyl(x, DENSE_Y, 6.4, 18, 5.5, { tone: "hi", crease: "lo" }, 40, 0.5));
  out.push(L(k.sideArc(x, DENSE_Y, 6.4, 20, P, 24), { tone: "lo" }));
  out.push(L(k.ring(x, DENSE_Y, 4.2, 23.5, P, 32), { tone: "faint" }));
  return out.join("");
}

function sparsePicker() {
  const x = -50;
  const out = [];
  out.push(box({ x: x - 3, y: -72, w: 6, d: 108, r: 1 }, CAB_TOP + 3, 5, { tone: "mid" }, 3, 0.5));
  out.push(D([at([x, -66, 58]), at([x, -48, 58]), at([x, 30, 58])], { size: 0.42 }));
  out.push(cyl(x, SPARSE_Y, 1.3, 28, 24, { tone: "mid", crease: "none" }, 16));
  out.push(box({ x: x - 5.5, y: SPARSE_Y - 5.5, w: 11, d: 11, r: 1.2 }, 51, 9, { tone: "mid" }, 3, 0.6));
  out.push(D(k.corners({ x: x - 5.5, y: SPARSE_Y - 5.5, w: 11, d: 11 }, 2).map(([a, b]) => at([a, b, 60])), { size: 0.4 }));
  out.push(cyl(x + 7.5, SPARSE_Y + 2, 2.4, 53, 4, { tone: "mid" }, 20));
  out.push(L(k.knurl(x + 7.5, SPARSE_Y + 2, 2.4, 53, 57, 16, P), { tone: "lo" }));
  // needle comb: needles first, bar over them
  for (let i = 0; i < 7; i++) {
    const nx = x - 7.2 + i * 2.4;
    const low = i % 3 === 1 ? 14.2 : 17;
    out.push(cyl(nx, SPARSE_Y, 0.45, low, 25 - low, { tone: i % 3 === 1 ? "hi" : "mid", crease: "none" }, 8));
  }
  out.push(box({ x: x - 9, y: SPARSE_Y - 2.5, w: 18, d: 5, r: 0.8 }, 25, 3, { tone: "hi", crease: "lo" }, 3, 0.4));
  out.push(D([at([x - 7.5, SPARSE_Y + 2.5, 26.5]), at([x + 7.5, SPARSE_Y + 2.5, 26.5])], { size: 0.38 }));
  return out.join("");
}

function sparsePost() {
  const x = -50, y = 33.5;
  return [
    cyl(x, y, 4, 0, 2.4, { tone: "lo" }),
    cyl(x, y, 2.4, 2.4, CAB_TOP + 0.6, { tone: "mid", crease: "none" }, 20),
    L(k.sideArc(x, y, 2.4, 20, P, 12) + k.sideArc(x, y, 2.4, 40, P, 12), { tone: "faint" }),
    D([at([x + 3, y, 2.4])], { size: 0.4 }),
  ].join("");
}

// ---- intake + guardrail gate ------------------------------------------
const A_Y = 4;
function intake() {
  const b = belt(-216, -124, A_Y, 16);
  const out = [b.back, post(-178, A_Y - 9.1, RAIL_TOP, 44), b.bed];
  // card magazine
  const mag = { x: -238, y: A_Y - 12, w: 22, d: 24, r: 2 };
  out.push(box(mag, 0, 20, { tone: "mid" }, 4, 0.8));
  out.push(L(k.sideSeam(mag, 6, P, 4), { tone: "faint" }));
  out.push(L(k.planOutline({ x: -230, y: A_Y - 7, w: 6, d: 14, r: 0.8 }, 20, P, 3), { tone: "lo" }));
  out.push(box({ x: -228, y: A_Y - 6, w: 1, d: 12, r: 0.2 }, 14, 13, { tone: "hi", crease: "lo" }, 2, 0));
  out.push(D(k.corners(mag, 3).map(([x, y]) => at([x, y, 20])), { size: 0.42 }));
  // question cards on the belt
  out.push(card(-202, A_Y, BELT_H), card(-190, A_Y + 1, BELT_H));
  // gate: flap hanging from the crossbar, sensor on top
  out.push(box({ x: -178.6, y: A_Y - 6, w: 1.2, d: 12, r: 0.2 }, 30, 13.6, { tone: "hi", crease: "lo" }, 2, 0));
  out.push(box({ x: -181, y: A_Y - 11.5, w: 6, d: 23, r: 1 }, 44, 4, { tone: "mid" }, 3, 0.5));
  out.push(D([at([-178, A_Y - 9, 48]), at([-178, A_Y + 9, 48])], { size: 0.42 }));
  out.push(box({ x: -182, y: A_Y - 4, w: 8, d: 8, r: 1.2 }, 48, 6, { tone: "mid" }, 3, 0.5));
  out.push(cyl(-174, A_Y, 1.6, 50, 2, { tone: "hi" }, 16));
  out.push(card(-160, A_Y, BELT_H), card(-140, A_Y - 1, BELT_H));
  out.push(b.front);
  out.push(post(-178, A_Y + 9.1, RAIL_TOP, 44));
  // reject chute and tray in front of the gate
  out.push(quad([[-188, A_Y + 10.4, 12.6], [-170, A_Y + 10.4, 12.6], [-170, 28, 8.6], [-188, 28, 8.6]]));
  out.push(reject());
  return out.join("");
}

function reject() {
  const T = { x: -194, y: 27, w: 30, d: 20, r: 1.5 };
  const z0 = 0, floor = 3, wall = 9;
  const out = [];
  out.push(box(T, z0, floor, { tone: "lo" }, 3, 0.4));
  out.push(box({ x: T.x, y: T.y, w: T.w, d: 2, r: 0.6 }, floor, wall, { tone: "mid" }, 2, 0.3));
  out.push(box({ x: T.x, y: T.y, w: 2, d: T.d, r: 0.6 }, floor, wall, { tone: "mid" }, 2, 0.3));
  out.push(card(-180, 36, floor, { tone: "mid" }));
  out.push(card(-174, 39, floor + 1, { tone: "mid", rules: 1 }));
  out.push(box({ x: T.x + T.w - 2, y: T.y, w: 2, d: T.d, r: 0.6 }, floor, wall, { tone: "mid" }, 2, 0.3));
  out.push(box({ x: T.x, y: T.y + T.d - 2, w: T.w, d: 2, r: 0.6 }, floor, wall, { tone: "mid" }, 2, 0.3));
  out.push(D([at([T.x + 4, T.y + T.d, floor + wall - 2]), at([T.x + T.w - 4, T.y + T.d, floor + wall - 2])], { size: 0.4 }));
  return out.join("");
}

// ---- splitter and the two lanes ---------------------------------------
function splitter() {
  const ring = [[-130, A_Y - 9], [-112, DENSE_Y - 6], [-104, DENSE_Y - 6], [-104, SPARSE_Y + 6], [-112, SPARSE_Y + 6], [-130, A_Y + 9]];
  const out = [S(k.extrude(ring, 0, BELT_H, P, { bevel: 0.6 }), { tone: "mid" })];
  const fin = [[-121, A_Y], [-112, A_Y - 1], [-106, A_Y - 0.6], [-106, A_Y + 0.6], [-112, A_Y + 1]];
  out.push(S(k.extrude(fin, BELT_H, 5, P), { tone: "mid" }));
  out.push(cyl(-121, A_Y, 1.5, BELT_H, 6.6, { tone: "mid" }, 16));
  out.push(D([at([-121, A_Y, BELT_H + 6.6])], { size: 0.4, tone: "hi" }));
  out.push(D([at([-108, DENSE_Y - 3, BELT_H]), at([-108, SPARSE_Y + 3, BELT_H]), at([-126, A_Y - 5, BELT_H]), at([-126, A_Y + 5, BELT_H])], { size: 0.42 }));
  return out.join("");
}

function lanes() {
  const dense = belt(-108, -4, DENSE_Y, 12);
  const sparse = belt(-108, -4, SPARSE_Y, 12);
  return {
    dense: [dense.back, dense.bed, card(-98, DENSE_Y, BELT_H, { w: 8, d: 7, rules: 0, dots: true }), card(-84, DENSE_Y, BELT_H, { w: 8, d: 7, rules: 0, dots: true }), card(-62, DENSE_Y, BELT_H, { w: 8, d: 7, rules: 0, dots: true }), card(-24, DENSE_Y, BELT_H, { w: 8, d: 7, rules: 0, dots: true }), dense.front].join(""),
    sparse: [sparse.back, sparse.bed, card(-78, SPARSE_Y, BELT_H, { w: 8, d: 7, rules: 2 }), card(-50, SPARSE_Y, BELT_H, { w: 8, d: 7, rules: 2 }), card(-30, SPARSE_Y, BELT_H, { w: 8, d: 7, rules: 2 }), card(-14, SPARSE_Y, BELT_H, { w: 8, d: 7, rules: 2 }), sparse.front].join(""),
  };
}

// ---- RRF collator turntable --------------------------------------------
const COL = [18, 2];
function collator() {
  const [x, y] = COL;
  const out = [`<path class="iso-halo" d="${k.haloOf({ x: x - 22, y: y - 22, w: 44, d: 44, r: 22 }, 0, 1, P)}" style="opacity:.5"/>`];
  out.push(cyl(x, y, 24, 0, 9, { tone: "mid" }, 64, 0.6));
  out.push(D(Array.from({ length: 16 }, (_, i) => i * (Math.PI / 8)).map((a) => at([x + 23 * Math.cos(a), y + 23 * Math.sin(a), 9])), { size: 0.4 }));
  out.push(cyl(x, y, 21.5, 9, 4, { tone: "hi", crease: "lo" }, 64, 0.6));
  const t = k.radialTicks(x, y, 20, 24, 3, 13, [5, 12], P);
  out.push(L(t.minor, { tone: "faint" }), L(t.major, { tone: "lo" }));
  out.push(L(k.ring(x, y, 20, 13, P, 64) + k.ring(x, y, 15, 13, P, 56) + k.ring(x, y, 8, 13, P, 40), { tone: "lo" }));
  const outer = k.radialTicks(x, y, 21.3, 48, 4, 13, [0.8, 1.3], P);
  out.push(L(outer.minor + outer.major, { tone: "lo" }));
  // ranked cards interleaving on the table
  out.push(card(x - 8, y - 9, 13, { w: 7, d: 6, rules: 0, dots: true }));
  out.push(card(x + 9, y - 8, 13, { w: 7, d: 6, rules: 1 }));
  out.push(card(x - 10, y + 9, 13, { w: 7, d: 6, rules: 1 }));
  out.push(card(x + 9, y + 9, 13, { w: 7, d: 6, rules: 0, dots: true }));
  out.push(cyl(x, y, 4.6, 13, 3, { tone: "mid" }, 28, 0.4));
  out.push(cyl(x, y, 2, 16, 2.4, { tone: "mid" }, 20));
  out.push(D([at([x, y, 18.4])], { size: 0.5, tone: "hi" }));
  return out.join("");
}

// ---- output line -------------------------------------------------------
const B_Y = 2;
function outputLine() {
  const b = belt(40, 214, B_Y, 16);
  const BK = B_Y - 9.1, FR = B_Y + 9.1;
  const out = [b.back, b.bed];
  // cards travelling along
  out.push(card(50, B_Y, BELT_H), card(86, B_Y, BELT_H));
  // rerank: roller head lifted on its lead screw (skipped)
  out.push(post(68, BK, RAIL_TOP, 50));
  out.push(card(68, B_Y, BELT_H));
  for (const dx of [-3, 3]) out.push(cyl(68 + dx, B_Y, 2, 27, 4, { tone: "mid" }, 20));
  out.push(box({ x: 61, y: B_Y - 7, w: 14, d: 14, r: 1.4 }, 31, 6, { tone: "hi", crease: "lo" }, 3, 0.6));
  out.push(D(k.corners({ x: 61, y: B_Y - 7, w: 14, d: 14 }, 2).map(([a, b2]) => at([a, b2, 37])), { size: 0.4 }));
  out.push(cyl(68, B_Y, 1.1, 37, 13, { tone: "mid", crease: "none" }, 14));
  out.push(L([39, 40.5, 42, 43.5, 45, 46.5, 48].map((z) => k.sideArc(68, B_Y, 1.1, z, P, 8)).join(""), { tone: "lo" }));
  out.push(box({ x: 65, y: B_Y - 11.5, w: 6, d: 23, r: 1 }, 50, 4, { tone: "mid" }, 3, 0.5));
  out.push(cyl(68, B_Y, 3, 54, 2.6, { tone: "mid" }, 24));
  out.push(L(k.knurl(68, B_Y, 3, 54, 56.6, 18, P), { tone: "lo" }));
  out.push(D([at([68, B_Y - 9, 54]), at([68, B_Y + 9, 54])], { size: 0.42 }));
  // context stacker: four posts, a stack, a stop collar on a graduated post
  out.push(post(100, BK, RAIL_TOP, 40, 2.4), post(118, BK, RAIL_TOP, 40, 2.4));
  const scale = [];
  const major = [];
  for (let z = 16; z <= 38; z += 2) ((z - 16) % 10 === 0 ? major : scale).push(k.segment([119.2, BK + 1.2, z], [119.2, BK + 1.2 - ((z - 16) % 10 === 0 ? 1.6 : 0.9), z], P));
  out.push(L(scale.join(""), { tone: "lo" }), L(major.join(""), { tone: "mid" }));
  for (let i = 0; i < 6; i++) out.push(card(109, B_Y + (i % 2 ? 0.3 : -0.3), BELT_H + i * 1.7, { w: 12, d: 10, rules: i === 5 ? 2 : 0 }));
  out.push(box({ x: 103, y: B_Y - 5.5, w: 12, d: 11, r: 0.8 }, 24.4, 1.2, { tone: "mid" }, 2, 0.2));
  out.push(box({ x: 115, y: BK - 0.6, w: 1.8, d: 1.2, r: 0.3 }, 24.6, 0.8, { tone: "mid" }, 2, 0));
  out.push(box({ x: 116.6, y: BK - 1.8, w: 2.8, d: 3.6, r: 0.5 }, 23.8, 2.4, { tone: "hi", crease: "lo" }, 2, 0.3));
  out.push(cyl(120.4, BK, 1.3, 23.9, 2.2, { tone: "mid" }, 14));
  for (const x of [100, 118]) out.push(box({ x: x - 1.6, y: BK - 1.6, w: 3.2, d: FR - BK + 3.2, r: 0.6 }, 40, 2.4, { tone: "mid" }, 2, 0.3));
  out.push(D([at([100, BK, 42.4]), at([100, FR, 42.4]), at([118, BK, 42.4]), at([118, FR, 42.4])], { size: 0.38 }));
  // bundled context travelling on
  out.push(card(132, B_Y, BELT_H, { w: 12, d: 10, rules: 0 }), card(132, B_Y, BELT_H + 1.4, { w: 12, d: 10, rules: 0 }), card(132, B_Y, BELT_H + 2.8, { w: 12, d: 10, rules: 3 }));
  // gateway turret
  const TX = 158;
  out.push(post(TX, BK, RAIL_TOP, 58));
  out.push(card(TX + 2, B_Y, BELT_H, { w: 12, d: 10, rules: 3 }));
  const heads = [45, 135, 225, 315].map((a) => {
    const r = (a * Math.PI) / 180;
    return { x: TX + 4.6 * Math.cos(r), y: B_Y + 4.6 * Math.sin(r), active: a === 45 };
  });
  for (const h of byDepth(heads.map((h) => [h.x, h.y, h])).map((p) => p[2])) {
    const bottom = h.active ? 22 : 31;
    out.push(cyl(h.x, h.y, 1.1, bottom - 2, 2, { tone: h.active ? "hi" : "mid" }, 12));
    out.push(cyl(h.x, h.y, 1.7, bottom, 40 - bottom, { tone: h.active ? "hi" : "mid", crease: "lo" }, 20, 0.3));
    out.push(L(k.sideArc(h.x, h.y, 1.7, bottom + 3, P, 12), { tone: "lo" }));
  }
  out.push(cyl(TX, B_Y, 7.2, 40, 4, { tone: "mid" }, 48, 0.6));
  out.push(D(heads.map((h) => at([h.x, h.y, 44])), { size: 0.45 }));
  out.push(L(k.ring(TX, B_Y, 6, 44, P, 40), { tone: "faint" }));
  out.push(cyl(TX, B_Y, 2.8, 44, 14, { tone: "mid", crease: "none" }, 24));
  out.push(L(k.sideArc(TX, B_Y, 2.8, 50, P, 12), { tone: "lo" }));
  out.push(box({ x: TX - 3, y: B_Y - 11.5, w: 6, d: 23, r: 1 }, 58, 4, { tone: "mid" }, 3, 0.5));
  // cache drum and the rate-limit spring on the crossbar
  out.push(cyl(TX, B_Y - 5, 4.8, 62, 9, { tone: "mid" }, 36, 0.4));
  out.push(L(k.knurl(TX, B_Y - 5, 4.8, 63, 70, 28, P), { tone: "lo" }));
  out.push(L(k.sideArc(TX, B_Y - 5, 4.8, 62.6, P, 24) + k.sideArc(TX, B_Y - 5, 4.8, 69.6, P, 24), { tone: "faint" }));
  out.push(L(k.ring(TX, B_Y - 5, 3, 71, P, 24), { tone: "lo" }));
  const spring = k.coil(B_Y + 2, B_Y + 8.5, [TX, 64.2], 1.8, 6, "y", P);
  out.push(L(spring.back, { tone: "lo" }));
  out.push(box({ x: TX - 0.7, y: B_Y + 0.5, w: 1.4, d: 9.5, r: 0.5 }, 63.5, 1.4, { tone: "mid" }, 2, 0));
  out.push(L(spring.front, { tone: "mid" }));
  out.push(box({ x: TX - 2.5, y: B_Y + 8.5, w: 5, d: 2.4, r: 0.5 }, 62, 4.6, { tone: "mid" }, 2, 0.3));
  out.push(D([at([TX - 9, B_Y - 9, 62]), at([TX + 2.5, B_Y + 9, 62])], { size: 0.42 }));
  out.push(card(178, B_Y, BELT_H, { w: 12, d: 9, rules: 3 }));
  // citation check: dial gauge with the plunger on the answer
  const GX = 198;
  out.push(post(GX, BK, RAIL_TOP, 44));
  out.push(card(GX, B_Y + 1, BELT_H, { w: 12, d: 9, rules: 3 }));
  out.push(cyl(GX, B_Y + 3, 1.6, BELT_H + 1, 1.4, { tone: "hi" }, 14));
  out.push(cyl(GX, B_Y + 3, 0.8, BELT_H + 2.4, 18, { tone: "hi", crease: "none" }, 10));
  out.push(box({ x: GX - 3, y: BK - 2, w: 6, d: 9, r: 1 }, 40, 4, { tone: "mid" }, 3, 0.4));
  const DR = 9, DZ = 32, DT = DZ + 6;
  out.push(cyl(GX, B_Y + 3, DR, DZ, 6, { tone: "hi", crease: "lo" }, 56, 0.6));
  out.push(L(k.knurl(GX, B_Y + 3, DR, DT - 2.4, DT, 60, P, 0.3), { tone: "lo" }));
  out.push(L(k.ring(GX, B_Y + 3, DR - 1.4, DT, P, 48), { tone: "lo" }));
  const dt = k.radialTicks(GX, B_Y + 3, DR - 1.8, 40, 5, DT, [0.9, 1.8], P);
  out.push(L(dt.minor, { tone: "lo" }), L(dt.major, { tone: "mid" }));
  const ang = 0.3 * Math.PI * 2;
  out.push(L(k.lineOnTop([GX - Math.sin(ang) * 1.8, B_Y + 3 + Math.cos(ang) * 1.8], [GX + Math.sin(ang) * (DR - 2.6), B_Y + 3 - Math.cos(ang) * (DR - 2.6)], DT + 0.2, P), { tone: "hi", free: "start" }));
  out.push(L(k.ring(GX, B_Y + 3, 1.1, DT, P, 14), { tone: "mid" }));
  out.push(D([at([GX, B_Y + 3, DT])], { size: 0.45, tone: "hi" }));
  out.push(b.front);
  out.push(post(68, FR, RAIL_TOP, 50));
  out.push(post(100, FR, RAIL_TOP, 40, 2.4), post(118, FR, RAIL_TOP, 40, 2.4));
  out.push(post(TX, FR, RAIL_TOP, 58));
  return out.join("");
}

function outTray() {
  const T = { x: 216, y: -12, w: 30, d: 28, r: 1.6 };
  const out = [];
  out.push(box({ x: 221, y: -7, w: 20, d: 18, r: 1 }, 0, 6, { tone: "lo" }, 3, 0.4));
  out.push(box(T, 6, 3, { tone: "mid" }, 3, 0.5));
  out.push(box({ x: T.x, y: T.y, w: T.w, d: 2, r: 0.6 }, 9, 5, { tone: "mid" }, 2, 0.3));
  out.push(card(231, 2, 9, { w: 16, d: 13, lit: true, rules: 4 }));
  out.push(box({ x: T.x + T.w - 2, y: T.y, w: 2, d: T.d, r: 0.6 }, 9, 5, { tone: "mid" }, 2, 0.3));
  out.push(box({ x: T.x, y: T.y + T.d - 2, w: T.w, d: 2, r: 0.6 }, 9, 5, { tone: "mid" }, 2, 0.3));
  out.push(D([at([T.x + 4, T.y + T.d, 12]), at([T.x + T.w - 4, T.y + T.d, 12])], { size: 0.4 }));
  return out.join("");
}

// ---- card frame and legend, drawn inside the SVG ------------------------
const mono = `font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace`;
const sans = `font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif`;
const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const corner = (x, y, text, anchor, strong = false, upper = false) =>
  `<text x="${x}" y="${y}" text-anchor="${anchor}" style="${mono};font-size:11px;letter-spacing:${upper ? ".1em" : ".04em"};fill:var(--anatomy-${strong ? "text" : "muted"})">${esc(text)}</text>`;

const LEGEND = [
  ["Card magazine", "question enters (console / REST / MCP)"],
  ["Gate, flap, reject tray", "guardrails: injection, PII, secrets"],
  ["Splitter + two lanes", "hybrid retrieval, run in parallel"],
  ["Magnet picker | needle comb", "dense vectors | BM25 sparse"],
  ["Drawer cabinet", "Qdrant index (dense + sparse)"],
  ["Turntable collator", "RRF fusion of both rankings"],
  ["Roller head, lifted", "adaptive rerank, skipped when top-1 agrees"],
  ["Stacker under a stop collar", "context assembly under a token budget"],
  ["Turret, drum, spring", "LLM gateway: fallback, cache, limits"],
  ["Dial gauge, lit sheet", "citation check, then the cited answer"],
];

function frame() {
  const out = [];
  out.push(`<rect x="0" y="0" width="${W}" height="${H}" style="fill:var(--anatomy-page)"/>`);
  out.push(`<rect x="${CARD.x}" y="${CARD.y}" width="${CARD.w}" height="${CARD.h}" style="fill:var(--anatomy-card);stroke:var(--anatomy-mid);stroke-width:1"/>`);
  out.push(corner(CARD.x + 20, CARD.y + 28, "Fig 1", "start", true));
  out.push(corner(CARD.x + CARD.w - 20, CARD.y + 28, "ATLAS · QUERY AND INGEST LINE", "end", false, true));
  out.push(corner(CARD.x + 20, CARD.y + CARD.h - 18, "QUESTION IN AT LEFT · CITED ANSWER OUT AT RIGHT", "start", false, true));
  out.push(corner(CARD.x + CARD.w - 20, CARD.y + CARD.h - 18, "back line: upload → parse → chunk → embed → index", "end", true));
  return out.join("");
}

function legend() {
  const out = [];
  const colX = [CARD.x + 6, CARD.x + CARD.w / 2 + 6];
  LEGEND.forEach(([part, role], i) => {
    const col = i < 5 ? 0 : 1;
    const y = LEGEND_Y + (i % 5) * 21;
    const x = colX[col];
    out.push(`<path d="M${x} ${y - 4}h12" style="stroke:var(--anatomy-${i === 9 ? "lit" : "mid"});stroke-width:1;fill:none"/>`);
    out.push(`<text x="${x + 20}" y="${y}" style="${sans};font-size:12.5px;fill:var(--anatomy-strong)">${esc(part)}<tspan style="fill:var(--anatomy-muted)">  —  ${esc(role)}</tspan></text>`);
  });
  return out.join("");
}

// ---- assemble, back to front --------------------------------------------
const L2 = lanes();
const body = [
  frame(),
  plate(),
  ingestion(),
  cabinet(),
  intake(),
  splitter(),
  L2.dense,
  densePicker(),
  L2.sparse,
  sparsePicker(),
  sparsePost(),
  collator(),
  outputLine(),
  outTray(),
  legend(),
];

let svg = k.figureSvg({
  width: W,
  height: H,
  label:
    "Atlas drawn as a card-sorting line on a bench. A question card enters from a magazine, passes a guardrail gate with a reject tray, and is split into two parallel lanes: a magnet picker pulls dense-vector cards and a needle comb pulls keyword (BM25) cards from a shared drawer cabinet, the Qdrant index. A turntable collator fuses both rankings (RRF), a roller head for reranking stays lifted because both lanes agree, a stacker packs context under a stop collar (the token budget), a four-head turret with a cache drum and a limit spring is the LLM gateway, and a dial gauge checks citations before the lit answer sheet lands in the out tray. Behind, a second line feeds documents from a hopper through a scanner, a guillotine and an embosser into the cabinet.",
  body,
  standalone: theme,
});
// The figure must render inside an <img>: no overflow, explicit size.
svg = svg.replace("<svg ", `<svg width="${W}" height="${H}" `).replace(/\.iso-svg\{display:block;width:100%;height:auto;overflow:visible\}/, ".iso-svg{display:block}");

const name = theme === "light" ? "atlas-architecture-light.svg" : "atlas-architecture.svg";
writeFileSync(join(HERE, name), svg);
console.log("wrote", name, (svg.length / 1024).toFixed(0) + " KB");
