(function () {
  'use strict';

  const BASE = window.BASE || '/';
  const POLL_MS = 33;          // fallback polling rate, used only if the live stream is unavailable
  const RENDER_DELAY = 0.12;   // s; render slightly behind the newest data so updates can be interpolated
  const LAMP_ON = { red: 0xff2d2d, amber: 0xffb000, green: 0x22dd55 };
  const LAMP_OFF = { red: 0x3a0c0c, amber: 0x3d2a06, green: 0x0c3a1c };
  const SIGNAL_TO_LAMP = { red: 'red', yellow: 'amber', green: 'green' };
  const EGO_RED = 0xd7261e, WHITE = 0xf4f4f4;
  const KEYS = ['w', 'a', 's', 'd'];
  const YARDS_PER_METRE = 1.09361;
  const MILES_PER_METRE = 0.000621371;

  // ------------------------------------------------------------ renderer
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.domElement.style.cssText = 'position:fixed;inset:0;';
  document.body.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xa9d4f5);
  scene.fog = new THREE.Fog(0xa9d4f5, 280, 700);

  const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.5, 1500);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445533, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 0.7);
  sun.position.set(150, 260, 90);
  scene.add(sun);

  // ------------------------------------------------------------ helpers
  const matCache = new Map();
  function mat(color) {
    if (!matCache.has(color)) matCache.set(color, new THREE.MeshLambertMaterial({ color }));
    return matCache.get(color);
  }
  const box = (w, h, d, material) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  const lerp = (a, b, k) => a + (b - a) * k;
  const lerpAngle = (a, b, k) => {
    let d = b - a;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    return a + d * k;
  };
  const nowS = () => performance.now() / 1000;
  const hexOf = (c) => new THREE.Color(c).getHex();
  if (!window.Models) throw new Error('models.js did not load');
  const { makeHouse, makeCar, makePerson, animatePerson, pedStyle, makeOffice } = window.Models;   // see models.js
  const fmt = (n, d = 1) => (Number.isFinite(n) ? n.toFixed(d) : '–');

  // ------------------------------------------------------------ signals
  const housingMat = new THREE.MeshLambertMaterial({ color: 0x0a0a0a });
  const housingGeo = new THREE.BoxGeometry(0.7, 1.6, 0.45);
  const lensGeo = new THREE.CylinderGeometry(0.19, 0.19, 0.05, 16);
  lensGeo.rotateX(Math.PI / 2);

  // A UK-style black signal head: red on top, amber in the middle, green below.
  function makeHousing() {
    const group = new THREE.Group();
    group.add(new THREE.Mesh(housingGeo, housingMat));
    const lamps = {};
    for (const [name, y] of [['red', 0.5], ['amber', 0], ['green', -0.5]]) {
      const m = new THREE.MeshLambertMaterial({ color: LAMP_OFF[name], emissive: 0x000000 });
      const lens = new THREE.Mesh(lensGeo, m);
      lens.position.set(0, y, 0.25);
      group.add(lens);
      lamps[name] = m;
    }
    return { group, lamps };
  }

  function applySignal(head, state) {
    const on = SIGNAL_TO_LAMP[state];
    for (const name of ['red', 'amber', 'green']) {
      const lit = name === on;
      head.lamps[name].color.setHex(lit ? LAMP_ON[name] : LAMP_OFF[name]);
      head.lamps[name].emissive.setHex(lit ? LAMP_ON[name] : 0x000000);
    }
  }

  function makeTree(t) {
    const g = new THREE.Group();
    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.3, 2, 8), mat(0x6b4a2b));
    trunk.position.y = 1;
    g.add(trunk);
    const colour = hexOf(t.color);
    if (t.kind === 'round') {
      const crown = new THREE.Mesh(new THREE.IcosahedronGeometry(2.1, 1), mat(colour));
      crown.position.y = 3.6;
      g.add(crown);
    } else {
      const cone = new THREE.Mesh(new THREE.ConeGeometry(1.9, 5, 9), mat(colour));
      cone.position.y = 4.2;
      g.add(cone);
    }
    g.scale.setScalar(t.scale);
    g.position.set(t.x, 0, t.z);
    return g;
  }

  // ------------------------------------------------------------ billboards
  // Artwork comes from billboard_images.js (bundled). Falls back to the static file.
  // Panel sizes follow the artwork proportions: Hotseat is square, the logo is about 2.55 : 1.
  const AD_SIZE = { hotseat: [11, 11], advancing: [11, 11] };   // the animated logo is square
  const loader = new THREE.TextureLoader();
  const adFallback = {
    hotseat: BASE + 'static/img/the_data_hotseat.png',
    advancing: BASE + 'static/img/advancing_analytics_logo.png',
  };
  const adTextures = {};
  const adLoaded = new Set();
  let adFailed = 0;
  for (const key of Object.keys(adFallback)) {
    const src = (window.AD_IMAGES && window.AD_IMAGES[key]) || adFallback[key];
    adTextures[key] = loader.load(
      src,
      () => { adLoaded.add(key); reportBillboards(); },
      undefined,
      (err) => { adFailed++; console.error('Billboard image failed to load:', key, err); reportBillboards(); }
    );
    adTextures[key].anisotropy = 4;
  }

  function reportBillboards() {
    const el = document.getElementById('legend');
    if (!el) return;
    const status = `billboard images: ${adLoaded.size}/2 loaded${adFailed ? `, ${adFailed} failed` : ''}`;
    el.textContent = el.textContent.replace(/ · billboard images:.*$/, '') + ' · ' + status;
  }

  // The animated logo plays on the advancing billboards. It is muted, looped and drawn as a texture.
  const logoVideo = document.createElement('video');
  logoVideo.src = BASE + 'static/img/logo_animated.mp4';
  logoVideo.loop = true;
  logoVideo.muted = true;
  logoVideo.playsInline = true;
  logoVideo.style.display = 'none';
  document.body.appendChild(logoVideo);
  logoVideo.play().catch(() => { /* the logo shows its first frame if autoplay is blocked */ });
  const logoVideoTexture = new THREE.VideoTexture(logoVideo);
  logoVideoTexture.anisotropy = 4;

  function billboardTexture(image) {
    return image === 'advancing' ? logoVideoTexture : adTextures[image];
  }

  function makeBillboard(b, centre) {
    const g = new THREE.Group();
    const [w, h] = AD_SIZE[b.image];
    const cy = 8.5;

    // dark frame, white backing (so transparent logos stay readable), then the artwork
    const frame = box(w + 0.8, h + 0.8, 0.3, mat(0x2b2f36));
    frame.position.set(0, cy, -0.15);
    g.add(frame);

    const backing = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide })
    );
    backing.position.set(0, cy, 0.01);
    g.add(backing);

    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ map: billboardTexture(b.image), transparent: true, side: THREE.DoubleSide })
    );
    panel.position.set(0, cy, 0.03);
    g.add(panel);

    const postH = cy - h / 2;
    for (const x of [-w / 3, w / 3]) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, postH, 8), mat(0x555a60));
      post.position.set(x, postH / 2, -0.4);
      g.add(post);
    }

    // Face the panel towards the city centre.
    g.position.set(b.x, 0, b.z);
    g.rotation.y = Math.atan2(centre - b.x, centre - b.z);
    return g;
  }

  // ------------------------------------------------------------ world
  function buildWorld(L) {
    const world = new THREE.Group();
    scene.add(world);
    const P = L.pitch, B = L.block, R = L.road, N = L.grid;
    const span = (N - 1) * P;
    const side = L.drive_side === 'right' ? 1 : -1;

    const grass = new THREE.Mesh(new THREE.PlaneGeometry(span + 600, span + 600), mat(0x6b9b57));
    grass.rotation.x = -Math.PI / 2;
    grass.position.set(span / 2, -0.02, span / 2);
    world.add(grass);

    // blocks: pavement ring with a lawn inside
    const pave = mat(0xc9c5bb), lawn = mat(0x7fae63), asphalt = mat(0x3a3d42), dashMat = mat(0xf2f2f2);
    for (let bi = 0; bi < N - 1; bi++) {
      for (let bj = 0; bj < N - 1; bj++) {
        const cx = bi * P + R / 2 + B / 2;
        const cz = bj * P + R / 2 + B / 2;
        const base = box(B, 0.1, B, pave);
        base.position.set(cx, 0.02, cz);
        world.add(base);
        const inner = box(B - 6, 0.12, B - 6, lawn);
        inner.position.set(cx, 0.05, cz);
        world.add(inner);
      }
    }

    // roads
    for (let k = 0; k < N; k++) {
      const h = box(span + 2 * R, 0.12, R, asphalt);
      h.position.set(span / 2, 0.06, k * P);
      world.add(h);
      const v = box(R, 0.12, span + 2 * R, asphalt);
      v.position.set(k * P, 0.06, span / 2);
      world.add(v);
    }

    // centre-line dashes, kept clear of the junction markings
    for (let k = 0; k < N; k++) {
      for (let i = 0; i < N - 1; i++) {
        for (let x = i * P + 19; x < (i + 1) * P - 21; x += 10) {
          const d = box(4, 0.02, 0.3, dashMat);
          d.position.set(x + 2, 0.15, k * P);
          world.add(d);
        }
      }
      for (let j = 0; j < N - 1; j++) {
        for (let z = j * P + 19; z < (j + 1) * P - 21; z += 10) {
          const d = box(0.3, 0.02, 4, dashMat);
          d.position.set(k * P, 0.15, z + 2);
          world.add(d);
        }
      }
    }

    // junction markings: a stop line and a zebra crossing on every approach
    const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    for (const n of L.nodes) {
      for (const [fx, fz] of dirs) {
        const fromI = n.i - fx, fromJ = n.j - fz;
        if (fromI < 0 || fromJ < 0 || fromI >= N || fromJ >= N) continue;
        const rx = -fz * side, rz = fx * side;
        const horiz = fz === 0;

        const stop = horiz ? box(0.6, 0.03, 6, dashMat) : box(6, 0.03, 0.6, dashMat);
        stop.position.set(n.x - fx * 9 + rx * 3, 0.165, n.z - fz * 9 + rz * 3);
        world.add(stop);

        for (let k = -3; k <= 3; k++) {
          const stripe = horiz ? box(4, 0.03, 0.8, dashMat) : box(0.8, 0.03, 4, dashMat);
          stripe.position.set(n.x - fx * 15 + rx * k * 1.6, 0.165, n.z - fz * 15 + rz * k * 1.6);
          world.add(stripe);
        }
      }
    }

    for (const h of L.houses) world.add(makeHouse(h));
    for (const t of L.trees || []) world.add(makeTree(t));
    for (const b of L.billboards || []) world.add(makeBillboard(b, L.center));
    if (L.office) world.add(makeOffice(L.office, adTextures.advancing));

    // signals: each corner pole carries a north-south head and an east-west head,
    // both facing the approach they control.
    world.userData.signals = {};
    const poleMat = mat(0x333840);
    for (const n of L.nodes) {
      const entry = { ns: [], ew: [] };
      for (const sx of [-1, 1]) {
        for (const sz of [-1, 1]) {
          const px = n.x + sx * 7.5, pz = n.z + sz * 7.5;
          const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 5, 8), poleMat);
          pole.position.set(px, 2.5, pz);
          world.add(pole);

          const ns = makeHousing();
          ns.group.position.set(px, 4.5, pz);
          ns.group.rotation.y = sz < 0 ? 0 : Math.PI;
          world.add(ns.group);
          entry.ns.push(ns);

          const ew = makeHousing();
          ew.group.position.set(px, 3.0, pz);
          ew.group.rotation.y = sx < 0 ? Math.PI / 2 : -Math.PI / 2;
          world.add(ew.group);
          entry.ew.push(ew);
        }
      }
      world.userData.signals[n.i + ',' + n.j] = entry;
    }
    return world;
  }

  function applyLights(list, world) {
    if (!list || !world) return;
    for (const l of list) {
      const entry = world.userData.signals[l.i + ',' + l.j];
      if (!entry) continue;
      for (const h of entry.ns) applySignal(h, l.ns);
      for (const h of entry.ew) applySignal(h, l.ew);
    }
  }

  // ------------------------------------------------------------ actors
  const carObjs = new Map();
  const pedObjs = new Map();
  let egoObj = null;

  function setBrake(m, on) {
    m.color.setHex(on ? 0xff2a2a : 0x551010);
    m.emissive.setHex(on ? 0xff2a2a : 0x000000);
  }

  // ------------------------------------------------------------ snapshot buffer
  // The server sends state at 20 Hz. Rendering shows the world a fraction of a second
  // behind the newest data and interpolates between the two snapshots either side of
  // that moment, so motion (including corners) is smooth.
  const snaps = [];            // { t: simTime, data }
  let clockOffset = null;      // client clock minus simulation clock (median of recent samples)
  const offsetSamples = [];
  let lastSimT = null;

  function median(values) {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  }

  function renderSimTime() {
    return nowS() - (clockOffset ?? 0) - RENDER_DELAY;
  }

  function pushSnapshot(data) {
    if (lastSimT !== null && data.t < lastSimT - 1) {   // server restarted
      snaps.length = 0;
      offsetSamples.length = 0;
      clockOffset = null;
    }
    lastSimT = data.t;
    // Arrival time includes network jitter. The median of recent samples ignores spikes.
    offsetSamples.push(nowS() - data.t);
    if (offsetSamples.length > 31) offsetSamples.shift();
    clockOffset = median(offsetSamples);
    snaps.push({ t: data.t, data });
    const cutoff = renderSimTime() - 1.0;
    while (snaps.length > 2 && snaps[1].t < cutoff) snaps.shift();
  }

  function sample(simT) {
    if (!snaps.length) return null;
    if (simT <= snaps[0].t) return { a: snaps[0].data, b: snaps[0].data, k: 0, dt: 1 / 30 };
    for (let i = snaps.length - 2; i >= 0; i--) {
      const A = snaps[i], B = snaps[i + 1];
      if (simT >= A.t && simT <= B.t) {
        return { a: A.data, b: B.data, k: (simT - A.t) / Math.max(1e-6, B.t - A.t), dt: B.t - A.t };
      }
    }
    const last = snaps[snaps.length - 1].data;
    return { a: last, b: last, k: 0, dt: 1 / 30 };
  }

  // Cubic Hermite interpolation: the path follows the curve the vehicle is actually on,
  // using the position and velocity at each end. Straight-line steps would look angular.
  function hermite(p0, p1, v0, v1, dt, k) {
    const k2 = k * k, k3 = k2 * k;
    return (2 * k3 - 3 * k2 + 1) * p0
      + (k3 - 2 * k2 + k) * v0 * dt
      + (-2 * k3 + 3 * k2) * p1
      + (k3 - k2) * v1 * dt;
  }

  function lerpItem(a, b, k, dt) {
    const avx = a.vx ?? 0, avz = a.vz ?? 0, bvx = b.vx ?? 0, bvz = b.vz ?? 0;
    return Object.assign({}, b, {
      x: hermite(a.x, b.x, avx, bvx, dt, k),
      z: hermite(a.z, b.z, avz, bvz, dt, k),
      vx: lerp(avx, bvx, k),
      vz: lerp(avz, bvz, k),
      rot: lerpAngle(a.rot, b.rot, k),
    });
  }

  function lerpList(listA, listB, k, dt) {
    const aMap = new Map(listA.map((x) => [x.id, x]));
    const bMap = new Map(listB.map((x) => [x.id, x]));
    const out = [];
    for (const a of listA) {
      const b = bMap.get(a.id);
      out.push(b ? lerpItem(a, b, k, dt) : a);
    }
    for (const b of listB) if (!aMap.has(b.id)) out.push(b);
    return out;
  }

  function syncMap(map, list, factory) {
    const seen = new Set();
    for (const a of list) {
      seen.add(a.id);
      let o = map.get(a.id);
      if (!o) {
        o = factory(a);
        scene.add(o);
        map.set(a.id, o);
      }
      o.position.set(a.x, 0, a.z);
      o.rotation.y = a.rot;
      o.userData.braking = !!a.braking;
      o.userData.vx = a.vx ?? 0;
      o.userData.vz = a.vz ?? 0;
    }
    for (const [id, o] of map) {
      if (!seen.has(id)) {
        scene.remove(o);
        map.delete(id);
      }
    }
  }

  let lastEgo = null;

  function applyFrame(dt) {
    if (!snaps.length) return;
    const s = sample(renderSimTime());
    const ego = lerpItem(s.a.ego, s.b.ego, s.k, s.dt);
    lastEgo = ego;

    if (!egoObj) {
      egoObj = makeCar(EGO_RED, true);
      scene.add(egoObj);
    }
    egoObj.position.set(ego.x, 0, ego.z);
    egoObj.rotation.y = ego.rot;
    egoObj.userData.braking = !!ego.braking;
    egoObj.userData.lidar.rotation.y += dt * 6;
    setBrake(egoObj.userData.brakeMat, egoObj.userData.braking);

    syncMap(carObjs, lerpList(s.a.vehicles, s.b.vehicles, s.k, s.dt), (a) => makeCar(hexOf(a.color), false));
    for (const o of carObjs.values()) setBrake(o.userData.brakeMat, o.userData.braking);

    syncMap(pedObjs, lerpList(s.a.pedestrians, s.b.pedestrians, s.k, s.dt), (a) => makePerson(pedStyle(a.id)));
    // The walking pose advances every frame with the distance each person covers,
    // so the legs and arms move smoothly whatever the update rate is.
    for (const o of pedObjs.values()) {
      const speed = Math.hypot(o.userData.vx, o.userData.vz);
      o.userData.gait = (o.userData.gait || 0) + speed * dt * (2 * Math.PI / 1.4);
      animatePerson(o, o.userData.gait, speed);
    }
  }

  // ------------------------------------------------------------ camera
  let mode = 'chase';
  let center = 0;
  const camPos = new THREE.Vector3();
  const camLook = new THREE.Vector3();
  let camInitialised = false;

  function updateCamera(dt) {
    let desired, look;
    if (mode === 'overview' || !lastEgo) {
      desired = new THREE.Vector3(center, 260, center + 210);
      look = new THREE.Vector3(center, 0, center);
    } else {
      const fx = Math.sin(lastEgo.rot), fz = Math.cos(lastEgo.rot);
      desired = new THREE.Vector3(lastEgo.x - fx * 16, 6.5, lastEgo.z - fz * 16);
      look = new THREE.Vector3(lastEgo.x + fx * 6, 1.2, lastEgo.z + fz * 6);
    }
    if (!camInitialised) {
      camPos.copy(desired);
      camLook.copy(look);
      camInitialised = true;
    }
    const k = 1 - Math.exp(-dt * 6);
    camPos.lerp(desired, k);
    camLook.lerp(look, k);
    camera.position.copy(camPos);
    camera.lookAt(camLook);
  }

  // ------------------------------------------------------------ HUD
  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  let lastHudSimTime = null;

  // Telemetry is 10 Hz, so the telemetry rows only change when a new record arrives.
  function updateHUD(data) {
    const t = data.ego.telemetry;
    setText('speed', fmt(data.ego.mph, 1) + ' mph');
    if (!t || t.sim_time_s === lastHudSimTime) return;
    lastHudSimTime = t.sim_time_s;
    setText('mode', t.driver_mode === 'human' ? `human: ${t.vehicle_reg} (${t.driver_name})` : 'autonomous');
    setText('heading', fmt(t.heading_deg, 0) + '°');
    setText('pos', `${fmt(t.x_m)}, ${fmt(t.z_m)} m`);
    setText('latlon', `${fmt(t.latitude, 5)}, ${fmt(t.longitude, 5)}`);
    // The sensor reports only cars within 10 m. Beyond that there is no value.
    setText('gap', t.distance_to_vehicle_ahead_m == null ? '–' : fmt(t.distance_to_vehicle_ahead_m) + ' m');
    setText('brake', fmt(t.braking_force_kn) + ' kN');
    setText('accel', fmt(t.acceleration_force_kn) + ' kN');
    setText('tyres', [t.tyre_pressure_fl_bar, t.tyre_pressure_fr_bar, t.tyre_pressure_rl_bar, t.tyre_pressure_rr_bar]
      .map((v) => fmt(v, 2)).join(' / ') + ' bar');
    setText('range', fmt(t.range_km, 0) + ' km');
    setText('soc', fmt(t.battery_soc_pct, 0) + ' %');
  }

  // ------------------------------------------------------------ human driver mode
  let humanActive = false;
  let lastRemaining = null;
  let toastTimer = null;

  function showToast(text) {
    const el = document.getElementById('toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 5000);
  }

  function setHumanMode(active) {
    if (active === humanActive) return;
    humanActive = active;
    document.body.classList.toggle('human', active);
    document.getElementById('btn-human').style.display = active ? 'none' : '';
    document.getElementById('btn-stop').style.display = active ? '' : 'none';
    if (active) {
      hideStartScreen();
    } else {
      const timeUp = lastRemaining !== null && lastRemaining <= 1;
      lastRemaining = null;
      keysDown.clear();
      showStartScreen(timeUp
        ? 'Your human driver session has finished. Time is up.'
        : 'Your human driver session has ended.');
    }
  }

  function updateCountdown(session) {
    if (!session) return;
    const r = Math.max(0, session.remaining_s);
    const m = Math.floor(r / 60), s = Math.floor(r % 60);
    const el = document.getElementById('countdown-value');
    el.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    el.classList.toggle('low', r <= 20);
    lastRemaining = r;
  }

  function guidance(carX, carZ, rot, target) {
    const fx = Math.sin(rot), fz = Math.cos(rot);
    const dx = target.x - carX, dz = target.z - carZ;
    const dist = Math.hypot(dx, dz);
    const right = dx * (-fz) + dz * fx;
    const ahead = dx * fx + dz * fz;
    const angle = Math.atan2(right, ahead);          // positive = to the right
    const deg = Math.abs(angle) * 180 / Math.PI;
    let text;
    if (dist < 25) text = 'Arriving at destination';
    else if (deg < 20) text = 'Continue straight ahead';
    else if (deg > 150) text = 'Make a U-turn when safe';
    else text = angle > 0 ? 'Turn right at the junction' : 'Turn left at the junction';
    const distText = dist * YARDS_PER_METRE < 1000
      ? `${Math.round(dist * YARDS_PER_METRE)} yd`
      : `${(dist * MILES_PER_METRE).toFixed(1)} mi`;
    return { text, distText };
  }

  // Satnav map: heading-up, roads drawn as thick grey strips, target as a red pin.
  const mapCanvas = document.getElementById('satnav-map');
  const mctx = mapCanvas ? mapCanvas.getContext('2d') : null;

  function drawSatnav(car, target, span, P, R) {
    if (!mctx) return;
    const W = mapCanvas.width, H = mapCanvas.height;
    const scale = 1.0;                                 // pixels per metre
    const fx = Math.sin(car.rot), fz = Math.cos(car.rot);
    const rx = -fz, rz = fx;                           // right-hand side of travel
    const cx = W / 2, cy = H * 0.66;
    const toScreen = (x, z) => {
      const dx = x - car.x, dz = z - car.z;
      return [cx + (dx * rx + dz * rz) * scale, cy - (dx * fx + dz * fz) * scale];
    };

    mctx.fillStyle = '#8fb573';
    mctx.fillRect(0, 0, W, H);

    mctx.strokeStyle = '#3a3d42';
    mctx.lineWidth = R * scale;
    mctx.lineCap = 'butt';
    for (let k = 0; k <= Math.round(span / P); k++) {
      const z = k * P;
      const [ax, ay] = toScreen(-200, z), [bx, by] = toScreen(span + 200, z);
      mctx.beginPath(); mctx.moveTo(ax, ay); mctx.lineTo(bx, by); mctx.stroke();
      const x = k * P;
      const [cx1, cy1] = toScreen(x, -200), [cx2, cy2] = toScreen(x, span + 200);
      mctx.beginPath(); mctx.moveTo(cx1, cy1); mctx.lineTo(cx2, cy2); mctx.stroke();
    }

    if (target) {
      const [tx, ty] = toScreen(target.x, target.z);
      mctx.setLineDash([5, 5]);
      mctx.strokeStyle = 'rgba(37, 99, 235, 0.9)';
      mctx.lineWidth = 2;
      mctx.beginPath(); mctx.moveTo(cx, cy); mctx.lineTo(tx, ty); mctx.stroke();
      mctx.setLineDash([]);
      if (tx > -20 && tx < W + 20 && ty > -20 && ty < H + 20) {
        mctx.fillStyle = '#e11d48';
        mctx.strokeStyle = '#ffffff';
        mctx.lineWidth = 2;
        mctx.beginPath(); mctx.arc(tx, ty, 8, 0, Math.PI * 2); mctx.fill(); mctx.stroke();
      }
    }

    // car: a triangle pointing in the direction of travel (always up on this map)
    mctx.fillStyle = '#ff7a1a';
    mctx.strokeStyle = '#111827';
    mctx.lineWidth = 1.5;
    mctx.beginPath();
    mctx.moveTo(cx, cy - 10);
    mctx.lineTo(cx - 7, cy + 7);
    mctx.lineTo(cx + 7, cy + 7);
    mctx.closePath();
    mctx.fill();
    mctx.stroke();
  }

  function updateSatnav(span, P, R) {
    const session = latestSession;
    if (!session || !lastEgo) return;
    const g = guidance(lastEgo.x, lastEgo.z, lastEgo.rot, session.target);
    setText('sat-instruction', g.text);
    setText('sat-distance', g.distText);
    setText('sat-reached', `Destinations: ${session.reached} · Collisions: ${session.collisions}`);
    setText('sat-speed', `${fmt(latestMph, 0)} mph`);
    drawSatnav(lastEgo, session.target, span, P, R);
  }

  // Key handling: W accelerate, S brake, A left, D right. Keys are only used in human mode.
  const keysDown = new Set();
  const isTyping = (e) => e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA');

  window.addEventListener('keydown', (e) => {
    if (!humanActive || isTyping(e)) return;
    const k = e.key.toLowerCase();
    if (KEYS.includes(k)) {
      keysDown.add(k);
      e.preventDefault();
    }
  });
  window.addEventListener('keyup', (e) => {
    keysDown.delete(e.key.toLowerCase());
  });
  window.addEventListener('blur', () => keysDown.clear());

  // Send the held keys to the server regularly. The server releases keys if they stop arriving.
  setInterval(() => {
    if (!humanActive) return;
    fetch(BASE + 'api/human/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        throttle: keysDown.has('w'),
        brake: keysDown.has('s'),
        left: keysDown.has('a'),
        right: keysDown.has('d'),
      }),
    }).catch(() => {});
  }, 50);

  const modal = document.getElementById('human-modal');
  const regInput = document.getElementById('reg');
  const driverInput = document.getElementById('driver');
  const modalError = document.getElementById('modal-error');

  function openModal() {
    modalError.textContent = '';
    modal.classList.add('show');
    regInput.focus();
  }
  function closeModal() {
    modal.classList.remove('show');
  }

  async function startHuman() {
    const reg = regInput.value.trim().toUpperCase();
    const name = driverInput.value.trim();
    if (!reg) { modalError.textContent = 'Enter the vehicle registration.'; return; }
    if (!name) { modalError.textContent = 'Enter the driver name.'; return; }
    try {
      const r = await fetch(BASE + 'api/human/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reg, name }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        modalError.textContent = body.detail || `Could not start (HTTP ${r.status}).`;
        return;
      }
      closeModal();
      setHumanMode(true);
    } catch (e) {
      modalError.textContent = 'Could not reach the server.';
    }
  }

  async function stopHuman() {
    try {
      await fetch(BASE + 'api/human/stop', { method: 'POST' });
    } catch (e) {
      // the server ends the session on its own when the time runs out
    }
    setHumanMode(false);               // back to the menu, with nothing streaming
  }

  document.getElementById('btn-human').addEventListener('click', openModal);
  document.getElementById('btn-stop').addEventListener('click', stopHuman);
  document.getElementById('modal-cancel').addEventListener('click', closeModal);
  document.getElementById('modal-start').addEventListener('click', startHuman);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  [regInput, driverInput].forEach((el) => el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startHuman();
    if (e.key === 'Escape') closeModal();
  }));

  // ------------------------------------------------------------ start screen
  const startScreen = document.getElementById('start-screen');

  function showStartScreen(message) {
    setText('start-message', message || 'Choose how the car should be driven.');
    startScreen.classList.add('show');
    disconnectStream();                // nothing streams while the menu is shown
  }
  function hideStartScreen() {
    startScreen.classList.remove('show');
    connectStream();
  }

  document.getElementById('opt-auto').addEventListener('click', async () => {
    try {
      const r = await fetch(BASE + 'api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'autonomous' }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setText('start-message', body.detail || 'Could not start autonomous driving.');
        return;
      }
    } catch (e) {
      setText('start-message', 'Could not reach the server.');
      return;
    }
    hideStartScreen();
  });
  document.getElementById('opt-human').addEventListener('click', openModal);

  // ------------------------------------------------------------ polling
  let world = null;
  let latestSession = null;
  let latestMph = 0;

  function onSnapshot(data) {
    pushSnapshot(data);
    latestSession = data.session;
    latestMph = data.ego.mph;
    applyLights(data.lights, world);
    updateHUD(data);
    if (data.session) {
      setHumanMode(true);
      updateCountdown(data.session);
    } else {
      setHumanMode(false);
    }
  }

  // Live stream (one connection, every simulation step). If it is not available, the
  // browser polls /api/state instead.
  let streamOk = false;
  let live = false;                    // true only while the car is running (not on the menu)
  let es = null;

  function resetClock() {
    snaps.length = 0;
    offsetSamples.length = 0;
    clockOffset = null;
    lastSimT = null;
  }

  function connectStream() {
    if (live || !window.EventSource) return;
    live = true;
    resetClock();                      // the simulation was paused on the menu, so re-sync the clock
    es = new EventSource(BASE + 'api/stream');
    es.onopen = () => { streamOk = true; };
    es.onmessage = (ev) => {
      streamOk = true;
      try { onSnapshot(JSON.parse(ev.data)); } catch (e) { /* ignore a bad message */ }
    };
    es.onerror = () => { streamOk = false; };   // the browser reconnects automatically
  }

  function disconnectStream() {
    live = false;
    streamOk = false;
    if (es) es.close();
    es = null;
  }

  async function pollState() {
    if (!live) {
      setTimeout(pollState, 200);      // on the menu: no requests for car data
      return;
    }
    if (streamOk) {
      setTimeout(pollState, 200);
      return;
    }
    try {
      const r = await fetch(BASE + 'api/state', { cache: 'no-store' });
      if (r.ok) onSnapshot(await r.json());
    } catch (e) {
      // transient network error: retry on the next tick
    }
    setTimeout(pollState, POLL_MS);
  }

  async function pollZerobus() {
    if (!live) {                       // no status traffic while the menu is shown
      setTimeout(pollZerobus, 3000);
      return;
    }
    try {
      const r = await fetch(BASE + 'api/status', { cache: 'no-store' });
      const s = (await r.json()).zerobus;
      let text;
      if (!s.enabled) {
        text = 'Zerobus: not configured, telemetry is kept local only';
      } else {
        text = `Zerobus: streaming · ${s.sent} sent · ${s.queued} queued · ${s.failed} failed`;
        if (s.last_error) text += ` · last error: ${s.last_error}`;
      }
      setText('zb', text);
    } catch (e) {
      setText('zb', 'Zerobus: status unavailable');
    }
    setTimeout(pollZerobus, 3000);
  }

  // ------------------------------------------------------------ loop
  let last = nowS();
  let layoutInfo = null;
  function frame() {
    requestAnimationFrame(frame);
    const now = nowS();
    const dt = Math.min(0.1, now - last);
    last = now;

    applyFrame(dt);
    if (humanActive && layoutInfo) updateSatnav(layoutInfo.span, layoutInfo.pitch, layoutInfo.road);
    updateCamera(dt);
    renderer.render(scene, camera);
  }

  // ------------------------------------------------------------ wiring
  document.querySelectorAll('#controls button[data-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      mode = btn.dataset.mode;
      document.querySelectorAll('#controls button[data-mode]').forEach((b) => b.classList.toggle('active', b === btn));
    });
  });

  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  async function init() {
    const r = await fetch(BASE + 'api/layout');
    const layout = await r.json();
    center = layout.center;
    layoutInfo = {
      span: (layout.grid - 1) * layout.pitch,
      pitch: layout.pitch,
      road: layout.road,
    };
    const legend = document.getElementById('legend');
    if (legend) legend.textContent = `Black-box signals at every junction · drive side: ${layout.drive_side} · red & white = ego vehicle`;
    world = buildWorld(layout);
    requestAnimationFrame(frame);
    try {
      await fetch(BASE + 'api/human/stop', { method: 'POST' });   // always start from the menu
    } catch (e) { /* ignore */ }
    showStartScreen();
    try {
      const st = await (await fetch(BASE + 'api/status', { cache: 'no-store' })).json();
      const secs = st.human_driver_seconds;
      setText('opt-human-limit', secs % 60 === 0 ? `${secs / 60} minute session` : `${secs} second session`);
    } catch (e) { /* keep the default text */ }
    pollState();
    pollZerobus();
  }

  init();
})();
