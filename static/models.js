// Geometry for houses and cars. Loaded before main.js and also usable from Node for checks.
(function (root, factory) {
  const api = factory(root.THREE || (typeof THREE !== 'undefined' ? THREE : undefined));
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Models = api;
})(typeof window !== 'undefined' ? window : globalThis, function (THREE) {
  'use strict';

  // Canonical house frame: the front facade faces +z, the width runs along x.
  const HOUSE_W = 6.0, HOUSE_D = 6.0;
  const FACE_NORMAL = { n: [0, -1], s: [0, 1], w: [-1, 0], e: [1, 0] };   // outward, (x, z)
  const ROOF_COLOURS = [0x4b5563, 0x9c4a2f, 0x5a3b2e, 0x6b7280];
  const DOOR_COLOURS = [0x2f5d50, 0x5b3a24, 0x1f2d4a, 0x7a1f1f];

  // ------------------------------------------------------------ materials (cached)
  const cache = new Map();
  function cached(key, make) {
    if (!cache.has(key)) cache.set(key, make());
    return cache.get(key);
  }
  const lambert = (color) => cached('L' + color, () => new THREE.MeshLambertMaterial({ color }));
  const phong = (color, extra) => cached('P' + color + JSON.stringify(extra || {}), () =>
    new THREE.MeshPhongMaterial(Object.assign({ color, shininess: 90, specular: 0x555555 }, extra || {})));

  // Brick texture drawn once on a canvas. It is multiplied by the house colour.
  let brickTexture = null;
  function getBrickTexture() {
    if (brickTexture !== null) return brickTexture;
    if (typeof document === 'undefined') { brickTexture = undefined; return brickTexture; }
    const c = document.createElement('canvas');
    c.width = 256; c.height = 256;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#e8e4dc';
    ctx.fillRect(0, 0, 256, 256);
    const rows = 8, cols = 4, bw = 256 / cols, bh = 256 / rows;
    for (let r = 0; r < rows; r++) {
      const offset = (r % 2) * bw / 2;
      for (let k = -1; k < cols + 1; k++) {
        const shade = 0.93 + 0.07 * Math.sin(r * 7.3 + k * 3.1);
        ctx.fillStyle = `rgb(${Math.round(255 * shade)},${Math.round(250 * shade)},${Math.round(245 * shade)})`;
        ctx.fillRect(k * bw + offset + 2, r * bh + 2, bw - 4, bh - 4);
      }
    }
    const tex = new THREE.CanvasTexture(c);
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.repeat.set(3, 4);
    tex.anisotropy = 4;
    brickTexture = tex;
    return tex;
  }
  function wallMaterial(colour) {
    return cached('W' + colour, () => {
      const map = getBrickTexture();
      return new THREE.MeshLambertMaterial(map ? { map, color: colour } : { color: colour });
    });
  }

  // ------------------------------------------------------------ house parts
  // Gable roof: the ridge runs along the width of the house, with a slope on the front and back.
  function gableRoof(w, d, wallH, pitch, material) {
    const overhangD = 0.5, overhangW = 0.3;
    const shape = new THREE.Shape();
    shape.moveTo(-d / 2 - overhangD, 0);
    shape.lineTo(d / 2 + overhangD, 0);
    shape.lineTo(0, pitch);
    shape.closePath();
    const geo = new THREE.ExtrudeGeometry(shape, { depth: w + overhangW * 2, bevelEnabled: false });
    geo.rotateY(-Math.PI / 2);                 // shape width -> z, extrusion -> x
    geo.translate((w + overhangW * 2) / 2, wallH, 0);
    const mesh = new THREE.Mesh(geo, material);
    mesh.castShadow = false;
    return mesh;
  }

  // A window centred on a facade at the origin, facing +z.
  function makeWindow() {
    const g = new THREE.Group();
    const frame = lambert(0xf4f1ea);
    const glass = phong(0x2b3f57, { shininess: 120, specular: 0x88aacc });
    const surround = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1.5, 0.1), frame);
    surround.position.set(0, 0, 0.04);
    g.add(surround);
    const pane = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.2, 0.12), glass);
    pane.position.set(0, 0, 0.09);
    g.add(pane);
    const bar = (w, h, x, y) => {
      const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.14), frame);
      b.position.set(x, y, 0.1);
      g.add(b);
    };
    bar(0.07, 1.2, 0, 0);
    bar(1.2, 0.07, 0, 0);
    const sill = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.12, 0.34), lambert(0xe9e4da));
    sill.position.set(0, -0.82, 0.14);
    g.add(sill);
    return g;
  }

  // ------------------------------------------------------------ houses
  function makeHouse(h) {
    const W = h.w || HOUSE_W, D = h.d || HOUSE_D, H = h.h;
    const pitch = 2.3;
    const g = new THREE.Group();
    const colourIndex = Math.abs(Math.round(h.x * 3 + h.z * 7)) % ROOF_COLOURS.length;

    // walls and plinth
    const body = new THREE.Mesh(new THREE.BoxGeometry(W, H, D), wallMaterial(h.color ? new THREE.Color(h.color).getHex() : 0xffffff));
    body.position.y = H / 2;
    g.add(body);
    const plinth = new THREE.Mesh(new THREE.BoxGeometry(W + 0.12, 0.7, D + 0.12), lambert(0x9a9890));
    plinth.position.y = 0.35;
    g.add(plinth);

    // roof and chimney
    g.add(gableRoof(W, D, H, pitch, lambert(ROOF_COLOURS[colourIndex])));
    const chimneyZ = -D * 0.22;
    const roofAtChimney = H + pitch * (1 - Math.abs(chimneyZ) / (D / 2 + 0.5));
    const chimneyBase = roofAtChimney - 0.6;
    const chimneyTop = H + pitch + 0.9;
    const chimney = new THREE.Mesh(new THREE.BoxGeometry(0.7, chimneyTop - chimneyBase, 0.7), lambert(0x8a4a3a));
    chimney.position.set(W * 0.22, (chimneyBase + chimneyTop) / 2, chimneyZ);
    g.add(chimney);

    // front door, frame and canopy
    const frontZ = D / 2;
    const frame = new THREE.Mesh(new THREE.BoxGeometry(1.2, 2.2, 0.1), lambert(0xf4f1ea));
    frame.position.set(0, 1.1, frontZ + 0.02);
    g.add(frame);
    const door = new THREE.Mesh(new THREE.BoxGeometry(1.0, 2.05, 0.12), lambert(DOOR_COLOURS[colourIndex]));
    door.position.set(0, 1.03, frontZ + 0.08);
    g.add(door);
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.06, 8, 6), phong(0xd4af37, { shininess: 150 }));
    knob.position.set(0.36, 1.0, frontZ + 0.16);
    g.add(knob);
    const canopy = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.12, 1.0), lambert(0x3a3f48));
    canopy.position.set(0, 2.5, frontZ + 0.5);
    g.add(canopy);
    for (const x of [-0.95, 0.95]) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.4, 8), lambert(0x3a3f48));
      post.position.set(x, 1.2, frontZ + 0.92);
      g.add(post);
    }

    // windows: ground floor beside the door, then rows above, plus one on each gable end
    const winTemplate = makeWindow();
    const addFront = (x, y) => {
      const w = winTemplate.clone();
      w.position.set(x, y, frontZ);
      g.add(w);
    };
    const addSide = (side, y) => {
      const w = winTemplate.clone();
      w.rotation.y = side * Math.PI / 2;
      w.position.set(side * (W / 2), y, 0);
      g.add(w);
    };
    addFront(-1.9, 1.75);
    addFront(1.9, 1.75);
    addSide(1, 1.75);
    addSide(-1, 1.75);
    for (let f = 1; 1.75 + f * 3.2 + 1.0 < H; f++) {
      const y = 1.75 + f * 3.2;
      addFront(-1.9, y);
      addFront(1.9, y);
      addSide(1, y);
      addSide(-1, y);
    }

    // orient the front facade to face the road
    const n = FACE_NORMAL[h.face || 'n'];
    g.rotation.y = Math.atan2(n[0], n[1]);
    g.position.set(h.x, 0, h.z);
    return g;
  }

  // ------------------------------------------------------------ cars
  // Canonical frame: the car points along +z, is 4.5 m long and 1.9 m wide.
  function extrudeAlongWidth(shape, width, bevel) {
    const geo = new THREE.ExtrudeGeometry(shape, {
      depth: width,
      bevelEnabled: true,
      bevelThickness: bevel,
      bevelSize: bevel * 0.8,
      bevelSegments: 3,
      curveSegments: 10,
    });
    geo.rotateY(-Math.PI / 2);                 // shape x -> z (forward), extrusion -> x (width)
    geo.translate(width / 2, 0, 0);
    geo.computeVertexNormals();
    return geo;
  }

  function makeCar(bodyColor, isEgo) {
    const g = new THREE.Group();
    const paint = phong(bodyColor, { shininess: 90, specular: 0x666666 });
    const glass = phong(0x1b2a3a, { shininess: 140, specular: 0x99bbdd });
    const trim = lambert(0x161a20);
    const chrome = phong(0xc9ced6, { shininess: 160, specular: 0xffffff });

    // lower body: a side profile extruded across the width, with rounded edges
    const side = new THREE.Shape();
    side.moveTo(-2.1, 0.45);
    side.lineTo(2.1, 0.45);
    side.lineTo(2.1, 0.78);
    side.quadraticCurveTo(2.0, 0.98, 1.6, 0.99);
    side.lineTo(-1.8, 0.99);
    side.quadraticCurveTo(-2.05, 0.99, -2.1, 0.78);
    side.closePath();
    const lower = new THREE.Mesh(extrudeAlongWidth(side, 1.55, 0.16), paint);
    g.add(lower);

    // glasshouse, sloped windscreen and rear window
    const cabinShape = new THREE.Shape();
    cabinShape.moveTo(-1.85, 0.98);
    cabinShape.lineTo(-1.25, 1.5);
    cabinShape.lineTo(0.55, 1.5);
    cabinShape.quadraticCurveTo(1.15, 1.48, 1.6, 0.98);
    cabinShape.closePath();
    const cabin = new THREE.Mesh(extrudeAlongWidth(cabinShape, 1.4, 0.1), glass);
    g.add(cabin);

    // painted roof panel over the glasshouse
    const roof = new THREE.Mesh(new THREE.BoxGeometry(1.36, 0.06, 1.6), paint);
    roof.position.set(0, 1.53, -0.3);
    g.add(roof);

    // wheels: black tyre, chrome hub
    const tyreGeo = new THREE.CylinderGeometry(0.34, 0.34, 0.24, 18);
    tyreGeo.rotateZ(Math.PI / 2);
    const hubGeo = new THREE.CylinderGeometry(0.2, 0.2, 0.26, 14);
    hubGeo.rotateZ(Math.PI / 2);
    for (const x of [-0.92, 0.92]) {
      for (const z of [-1.42, 1.42]) {
        const tyre = new THREE.Mesh(tyreGeo, lambert(0x151515));
        tyre.position.set(x, 0.34, z);
        g.add(tyre);
        const hub = new THREE.Mesh(hubGeo, chrome);
        hub.position.set(x, 0.34, z);
        g.add(hub);
      }
    }

    // mirrors, grille, bumpers, number plates
    for (const x of [-1.0, 1.0]) {
      const mirror = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.14, 0.16), paint);
      mirror.position.set(x, 1.0, 0.5);
      g.add(mirror);
    }
    const grille = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.22, 0.05), trim);
    grille.position.set(0, 0.7, 2.1);
    g.add(grille);
    for (const z of [2.18, -2.18]) {
      const bumper = new THREE.Mesh(new THREE.BoxGeometry(1.84, 0.17, 0.14), trim);
      bumper.position.set(0, 0.4, z);
      g.add(bumper);
      const plate = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.13, 0.03), lambert(0xf4f4f0));
      plate.position.set(0, 0.52, z + Math.sign(z) * 0.02);
      g.add(plate);
    }

    // lights: flattened lenses; the tail lights are shared so they can glow when braking
    const headMat = phong(0xfff8e0, { shininess: 100, emissive: 0x554422 });
    for (const x of [-0.62, 0.62]) {
      const head = new THREE.Mesh(new THREE.SphereGeometry(0.16, 12, 8), headMat);
      head.scale.set(1.4, 0.6, 0.5);
      head.position.set(x, 0.86, 2.12);
      g.add(head);
    }
    const brakeMat = new THREE.MeshPhongMaterial({ color: 0x7a1212, emissive: 0x000000, shininess: 100 });
    for (const x of [-0.7, 0.7]) {
      const tail = new THREE.Mesh(new THREE.SphereGeometry(0.15, 12, 8), brakeMat);
      tail.scale.set(1.5, 0.6, 0.5);
      tail.position.set(x, 0.88, -2.12);
      g.add(tail);
    }
    g.userData.brakeMat = brakeMat;

    if (isEgo) {
      // white racing stripes along both sides
      for (const x of [-0.955, 0.955]) {
        const stripe = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.12, 3.7), lambert(0xf4f4f4));
        stripe.position.set(x, 0.72, 0);
        g.add(stripe);
      }
      // sensor on the roof
      const lidar = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.28, 0.3, 16), lambert(0x111111));
      lidar.position.set(0, 1.72, 0.1);
      g.add(lidar);
      g.userData.lidar = lidar;
    }
    return g;
  }


  // ------------------------------------------------------------ pedestrians
  // Stylised avatars. Each person's look is chosen from their id so the same person
  // always looks the same. Hats are rare, and tops, hair and trousers vary.
  const SKIN = ['#f5cba7', '#e8b48a', '#d19a6e', '#a8704a', '#7a4d30'];
  const HAIR = ['#7a4a22', '#3b2616', '#1f1a17', '#c9a14a', '#5b3320', '#9a9a9a', '#7a2e1b'];
  const TOPS = ['plain', 'plain', 'plain', 'stripe', 'stripe', 'jacket'];
  const SHIRT = ['#2a2f3a', '#1e3a5f', '#7a1f1f', '#2f5d3a', '#4b5563', '#f4f4f5', '#c2410c', '#7c3aed', '#0e7490', '#eab308'];
  const STRIPE = ['#c7ccd6', '#e53935', '#f2c94c', '#ffffff'];
  const JACKET = ['#1f2937', '#7c2d12', '#334155', '#14532d', '#44403c'];
  const TROUSER = ['#1d2433', '#14171f', '#3a3f4b', '#2b3a55', '#4a4032', '#5b6b7a'];
  const SHORTS = ['#c8b99a', '#3b4a6b', '#2d2d2d', '#d6d3c4'];
  const HAIR_STYLE = ['short', 'short', 'fringe', 'long', 'buzz', 'bald'];
  const TRAINER = ['#f5f5f5', '#f2f2ee', '#1f2937', '#7f1d1d', '#e5e7eb'];
  const ACCENT = ['#e11d48', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7'];
  const CAP = ['#1e293b', '#7c2d12', '#334155', '#b91c1c', '#15803d'];

  function hashString(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  // One pedestrian is styled from the reference photo: swept grey-brown hair, a full beard,
  // and a dark button-down shirt. It is a cartoon approximation of those features.
  const REFERENCE_ID = 'ped-00';

  function pedStyle(id) {
    const pick = (arr, salt) => arr[hashString(id + salt) % arr.length];
    const top = pick(TOPS, 'top');
    if (id === REFERENCE_ID) {
      return {
        skin: '#e8b48a', hair: '#8c8176', hairStyle: 'swept', beard: true,
        top: 'button', shirt: '#1f2430', stripe: '#c7ccd6', jacket: '#1f2430',
        trousers: '#2a3142', shorts: false, shortsColour: '#2a3142', trainer: '#1f2937',
        accent: '#3b82f6', cap: null,
      };
    }
    return {
      skin: pick(SKIN, 'skin'),
      hair: pick(HAIR, 'hair'),
      hairStyle: pick(HAIR_STYLE, 'hairstyle'),
      top,
      shirt: pick(SHIRT, 'shirt'),
      stripe: pick(STRIPE, 'stripe'),
      jacket: pick(JACKET, 'jacket'),
      trousers: pick(TROUSER, 'trousers'),
      shorts: hashString(id + 'shorts') % 3 === 0,
      shortsColour: pick(SHORTS, 'shortscolour'),
      trainer: pick(TRAINER, 'trainer'),
      accent: pick(ACCENT, 'accent'),
      cap: hashString(id + 'hat') % 7 === 0 ? pick(CAP, 'cap') : null,
      beard: false,
    };
  }

  function makePerson(style) {
    const g = new THREE.Group();
    const col = (c) => new THREE.Color(c).getHex();
    const skin = phong(col(style.skin), { shininess: 35, specular: 0x333333 });
    const skinDark = phong(col(style.skin), { shininess: 35 });
    const hair = phong(col(style.hair), { shininess: 30 });
    const shirt = phong(col(style.shirt), { shininess: 30 });
    const stripe = phong(col(style.stripe), { shininess: 30 });
    const jacket = phong(col(style.jacket), { shininess: 40 });
    const trousers = phong(col(style.trousers), { shininess: 20 });
    const shorts = phong(col(style.shortsColour), { shininess: 20 });
    const legWear = style.shorts ? shorts : trousers;
    const trainer = phong(col(style.trainer), { shininess: 70, specular: 0x666666 });
    const accent = phong(col(style.accent), { shininess: 50 });
    const eyeMat = phong(0x111111, { shininess: 90, specular: 0xffffff });
    const white = phong(0xffffff, { shininess: 120, specular: 0xffffff });
    const blush = phong(0xf08a8a, { shininess: 10 });
    const sphere = (r, sw, sh, ...rest) => new THREE.SphereGeometry(r, sw, sh, ...rest);
    const topMat = style.top === 'jacket' ? jacket : shirt;

    // pelvis
    const pelvis = new THREE.Mesh(sphere(0.2, 16, 12), legWear);
    pelvis.scale.set(1.0, 0.65, 0.78);
    pelvis.position.y = 0.95;
    g.add(pelvis);

    // legs: hip and knee joints, trainers at the end (bare shins when wearing shorts)
    const legs = [];
    for (const side of [-1, 1]) {
      const hip = new THREE.Group();
      hip.position.set(side * 0.1, 0.92, 0);
      g.add(hip);
      const thigh = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.085, 0.42, 14), legWear);
      thigh.position.y = -0.21;
      hip.add(thigh);
      const knee = new THREE.Group();
      knee.position.y = -0.42;
      hip.add(knee);
      const shin = new THREE.Mesh(new THREE.CylinderGeometry(0.085, 0.07, 0.4, 14), style.shorts ? skin : trousers);
      shin.position.y = -0.2;
      knee.add(shin);
      const trainerMesh = new THREE.Mesh(sphere(0.095, 14, 10), trainer);
      trainerMesh.scale.set(0.95, 0.62, 1.55);
      trainerMesh.position.set(0, -0.44, 0.05);
      knee.add(trainerMesh);
      const stripeMesh = new THREE.Mesh(new THREE.BoxGeometry(0.018, 0.04, 0.12), accent);
      stripeMesh.position.set(side * 0.092, -0.44, 0.05);
      knee.add(stripeMesh);
      legs.push({ hip, knee });
    }

    // torso: plain t-shirt, striped t-shirt, or a jacket with a zip
    const torso = new THREE.Mesh(sphere(0.2, 18, 14), topMat);
    torso.scale.set(1.02, 1.12, 0.8);
    torso.position.y = 1.2;
    g.add(torso);
    if (style.top === 'stripe') {
      for (const y of [1.08, 1.27]) {
        const band = new THREE.Mesh(new THREE.CylinderGeometry(0.205, 0.205, 0.03, 20, 1, true), stripe);
        band.scale.set(1, 1, 0.82);
        band.position.y = y;
        g.add(band);
      }
      const centreStripe = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.42, 0.02), accent);
      centreStripe.position.set(0, 1.2, 0.163);
      g.add(centreStripe);
    }
    if (style.top === 'button') {
      const dark = phong(0x14181f, { shininess: 30 });
      for (const x of [-0.07, 0.07]) {
        const collar = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.05, 0.03), dark);
        collar.position.set(x, 1.47, 0.14);
        collar.rotation.z = x < 0 ? 0.35 : -0.35;
        g.add(collar);
      }
      for (const y of [1.36, 1.26, 1.16, 1.06]) {
        const button = new THREE.Mesh(new THREE.SphereGeometry(0.013, 8, 6), white);
        button.position.set(0, y, 0.168);
        g.add(button);
      }
    }
    if (style.top === 'jacket') {
      const body = new THREE.Mesh(new THREE.CylinderGeometry(0.218, 0.2, 0.46, 20, 1, true), jacket);
      body.scale.set(1, 1, 0.8);
      body.position.y = 1.2;
      g.add(body);
      const zip = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.44, 0.02), phong(0xd1d5db, { shininess: 90 }));
      zip.position.set(0, 1.2, 0.17);
      g.add(zip);
    }

    // neck, large rounded head, ears, face
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.075, 0.13, 12), skinDark);
    neck.position.y = 1.44;
    g.add(neck);
    const head = new THREE.Mesh(sphere(0.18, 24, 18), skin);
    head.scale.set(1.0, 1.08, 0.95);
    head.position.y = 1.6;
    g.add(head);
    for (const x of [-0.175, 0.175]) {
      const ear = new THREE.Mesh(sphere(0.04, 10, 8), skinDark);
      ear.scale.set(0.5, 1, 0.8);
      ear.position.set(x, 1.6, 0);
      g.add(ear);
    }
    for (const x of [-0.065, 0.065]) {
      const eye = new THREE.Mesh(sphere(0.035, 12, 10), eyeMat);
      eye.scale.set(0.8, 1.15, 0.4);
      eye.position.set(x, 1.63, 0.158);
      g.add(eye);
      const shine = new THREE.Mesh(sphere(0.011, 8, 6), white);
      shine.position.set(x + 0.012, 1.645, 0.175);
      g.add(shine);
    }
    for (const x of [-0.1, 0.1]) {
      const cheek = new THREE.Mesh(sphere(0.03, 10, 8), blush);
      cheek.scale.set(1, 0.6, 0.3);
      cheek.position.set(x, 1.55, 0.15);
      g.add(cheek);
    }
    const nose = new THREE.Mesh(sphere(0.018, 8, 6), skinDark);
    nose.position.set(0, 1.6, 0.178);
    g.add(nose);
    if (style.beard) {
      const beard = new THREE.Mesh(sphere(0.13, 16, 12), hair);
      beard.scale.set(1.0, 0.85, 0.85);
      beard.position.set(0, 1.5, 0.07);
      g.add(beard);
      const moustache = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.024, 0.04), hair);
      moustache.position.set(0, 1.535, 0.172);
      g.add(moustache);
    }
    const smile = new THREE.Mesh(new THREE.TorusGeometry(0.055, 0.011, 8, 16, Math.PI), eyeMat);
    smile.rotation.z = Math.PI;
    smile.position.set(0, 1.555, 0.168);
    g.add(smile);

    // hair, by style
    if (style.hairStyle !== 'bald') {
      const scalp = style.hairStyle === 'buzz' ? 0.98 : 1.0;
      const cap = new THREE.Mesh(sphere(0.19, 20, 12, 0, Math.PI * 2, 0, Math.PI * 0.55), hair);
      cap.scale.set(scalp, scalp, scalp);
      cap.position.set(0, 1.64, -0.01);
      g.add(cap);
      if (style.hairStyle === 'short' || style.hairStyle === 'fringe' || style.hairStyle === 'long') {
        const fringe = new THREE.Mesh(sphere(0.13, 14, 10), hair);
        fringe.scale.set(0.95, style.hairStyle === 'fringe' ? 0.55 : 0.42, 0.55);
        fringe.position.set(0.02, 1.74, 0.12);
        g.add(fringe);
      }
      if (style.hairStyle === 'swept') {
        const sweep = new THREE.Mesh(sphere(0.135, 14, 10), hair);
        sweep.scale.set(1.05, 0.5, 0.7);
        sweep.position.set(0.0, 1.75, -0.03);
        g.add(sweep);
        for (const x of [-0.17, 0.17]) {
          const side = new THREE.Mesh(sphere(0.06, 10, 8), hair);
          side.scale.set(0.6, 1.6, 0.7);
          side.position.set(x, 1.6, -0.02);
          g.add(side);
        }
      }
      if (style.hairStyle === 'long') {
        const back = new THREE.Mesh(sphere(0.18, 16, 12), hair);
        back.scale.set(1.0, 1.4, 0.7);
        back.position.set(0, 1.5, -0.1);
        g.add(back);
      }
    }
    if (style.cap) {
      const capMat = phong(col(style.cap), { shininess: 40 });
      const dome = new THREE.Mesh(sphere(0.195, 18, 10, 0, Math.PI * 2, 0, Math.PI * 0.5), capMat);
      dome.position.set(0, 1.7, 0);
      g.add(dome);
      const brim = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.02, 0.18), capMat);
      brim.position.set(0, 1.72, 0.19);
      g.add(brim);
    }

    // arms: rounded sleeves, mitten-like hands
    const arms = [];
    for (const side of [-1, 1]) {
      const shoulder = new THREE.Group();
      shoulder.position.set(side * 0.25, 1.36, 0);
      g.add(shoulder);
      const upper = new THREE.Mesh(new THREE.CylinderGeometry(0.065, 0.06, 0.28, 14), topMat);
      upper.position.y = -0.14;
      shoulder.add(upper);
      const elbow = new THREE.Group();
      elbow.position.y = -0.28;
      shoulder.add(elbow);
      const fore = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.05, 0.25, 14), skin);
      fore.position.y = -0.125;
      elbow.add(fore);
      const hand = new THREE.Mesh(sphere(0.07, 12, 10), skin);
      hand.scale.set(0.9, 1.1, 0.85);
      hand.position.y = -0.3;
      elbow.add(hand);
      arms.push({ shoulder, elbow });
    }

    const hipObjs = legs.map((l) => l.hip);
    const upper = new THREE.Group();
    for (const c of [...g.children]) {
      if (!hipObjs.includes(c)) {
        g.remove(c);
        upper.add(c);
      }
    }
    g.add(upper);
    g.userData.rig = { legs, arms, upper };
    g.scale.setScalar(0.96);          // about 1.8 m tall
    return g;
  }

  // Walking pose. The legs and arms swing in opposite phase, the knee bends as a leg swings
  // through, the body rises and falls twice per stride, sways gently, and the shoulders and
  // hips turn against each other. The stride grows with walking speed.
  function animatePerson(g, phase, speed) {
    const rig = g.userData.rig;
    if (!rig) return;
    const amp = speed < 0.1 ? 0 : Math.max(0.35, Math.min(1.0, (speed || 1.3) / 1.3));
    for (let i = 0; i < 2; i++) {
      const p = phase + (i === 0 ? 0 : Math.PI);
      rig.legs[i].hip.rotation.x = 0.45 * amp * Math.sin(p);
      rig.legs[i].knee.rotation.x = -0.75 * amp * Math.max(0, Math.cos(p));
      rig.arms[i].shoulder.rotation.x = -0.42 * amp * Math.sin(p);
      rig.arms[i].elbow.rotation.x = -0.18 - 0.22 * amp * Math.max(0, Math.sin(p));
    }
    const up = rig.upper;
    up.position.y = 0.016 * amp * (-Math.cos(2 * phase));   // rises and falls twice per stride
    up.position.x = 0.014 * amp * Math.sin(phase);          // gentle side-to-side sway
    up.rotation.y = 0.08 * amp * Math.sin(phase);           // shoulders turn against the hips
    up.rotation.z = -0.02 * amp * Math.sin(phase);          // slight roll with each step
  }


  // ------------------------------------------------------------ office
  // A five-storey office in the middle of the city, with the Advancing Analytics logo on
  // every face. The facade is white with glass bands, and the entrance faces the front.
  function makeOffice(o, logoTexture) {
    const W = o.w, D = o.d, H = o.h;
    const g = new THREE.Group();
    const wall = phong(0xe9eef4, { shininess: 40 });
    const glass = phong(0x2d4c66, { shininess: 140, specular: 0xaad4ff });
    const frame = phong(0xc7d0da, { shininess: 60 });

    const body = new THREE.Mesh(new THREE.BoxGeometry(W, H, D), wall);
    body.position.y = H / 2;
    g.add(body);

    const roof = new THREE.Mesh(new THREE.BoxGeometry(W + 0.6, 0.6, D + 0.6), phong(0x4b5563));
    roof.position.y = H + 0.3;
    g.add(roof);
    const accent = new THREE.Mesh(new THREE.BoxGeometry(W + 0.64, 0.14, D + 0.64), phong(0xb91c1c, { shininess: 60 }));
    accent.position.y = H + 0.62;
    g.add(accent);
    const plantRoof = new THREE.Mesh(new THREE.BoxGeometry(W * 0.4, 1.2, D * 0.4), phong(0xd1d5db));
    plantRoof.position.y = H + 1.3;
    g.add(plantRoof);

    const logoW = 10, logoH = logoW / 2.55;
    const logoMat = logoTexture
      ? new THREE.MeshBasicMaterial({ map: logoTexture, transparent: true, side: THREE.DoubleSide })
      : new THREE.MeshBasicMaterial({ color: 0xb22222, side: THREE.DoubleSide });

    // the four faces; the front (+z) carries the entrance
    for (const ry of [0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
      const face = new THREE.Group();
      face.rotation.y = ry;
      g.add(face);
      const out = D / 2 + 0.04;
      for (const y of [1.8, 5.4, 9.0]) {
        const band = new THREE.Mesh(new THREE.BoxGeometry(W - 1.4, 2.2, 0.08), glass);
        band.position.set(0, y, out);
        face.add(band);
        for (let x = -(W - 1.4) / 2 + 2.4; x < (W - 1.4) / 2 - 1; x += 2.4) {
          const mullion = new THREE.Mesh(new THREE.BoxGeometry(0.08, 2.2, 0.1), frame);
          mullion.position.set(x, y, out + 0.02);
          face.add(mullion);
        }
      }
      const logo = new THREE.Mesh(new THREE.PlaneGeometry(logoW, logoH), logoMat);
      logo.position.set(0, H - 2.3, D / 2 + 0.06);
      face.add(logo);
    }

    // entrance on the front
    const door = new THREE.Mesh(new THREE.BoxGeometry(3.0, 2.8, 0.1), glass);
    door.position.set(0, 1.4, D / 2 + 0.06);
    g.add(door);
    const canopy = new THREE.Mesh(new THREE.BoxGeometry(5.0, 0.22, 1.6), phong(0x1f2937));
    canopy.position.set(0, 3.2, D / 2 + 0.8);
    g.add(canopy);

    g.position.set(o.x, 0, o.z);
    return g;
  }

  return { makeHouse, makeCar, makePerson, animatePerson, pedStyle, makeOffice, HOUSE_W, HOUSE_D, FACE_NORMAL };
});
