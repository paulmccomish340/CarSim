"""
Simulation core for the self-driving car demo.

Plain Python with no web dependencies. The city is a 3 x 3 grid of blocks ringed
by a 4 x 4 grid of junctions. Every junction has traffic signals.

Vehicles follow the road graph and obey the signals. They keep a safe following
distance using the Intelligent Driver Model (IDM).

Turns: a vehicle that is going to turn follows a smooth quarter-curve (a Bezier
curve that is tangent to the lane it enters and the lane it leaves) through the
junction, and it slows to a corner speed before it reaches the curve.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone

# ---------------------------------------------------------------- geometry (m)
BLOCK = 60.0                 # side of one city block
ROAD = 12.0                  # road width
PITCH = BLOCK + ROAD         # junction centre to junction centre
GRID = 4                     # 4 x 4 junctions -> 3 x 3 blocks
LANE_OFFSET = 3.0            # distance from road centre line to lane centre
CAR_LENGTH = 4.5
STOP_OFFSET = 9.0            # stop line sits this far before the junction centre
TURN_WINDOW = 7.0            # the curve runs this far either side of the junction centre
TURN_SPEED = 5.0             # m/s (about 18 km/h) through a corner
SENSOR_RANGE_M = 200.0       # forward radar maximum range (internal)
SENSOR_REPORT_M = 10.0       # distances above this are reported as null
LKA_MAX_ANGLE = math.radians(10)   # lane keeping only helps within 10 degrees of the road
LKA_GAIN = 3.0               # 1/s, how quickly the car is nudged straight
SIDEWALK_INSET = 1.5         # pedestrians walk this far inside the block edge
HOUSE_SETBACK = 6.0          # house centre distance from block edge
HOUSE_COLORS = ["#e9d8a6", "#f4a261", "#e76f51", "#8ecae6", "#b5e48c", "#ffd6a5", "#cdb4db", "#f1faee"]
CAR_COLORS = ["#2d6a4f", "#457b9d", "#f1faee", "#2a9d8f", "#e9c46a", "#264653", "#8d99ae", "#6a4c93"]
PED_COLORS = ["#ffb4a2", "#90be6d", "#577590", "#f9c74f", "#f94144", "#43aa8b", "#2d3748"]
TREE_COLORS = ["#2f6b3a", "#3c7d3f", "#4a8c3a", "#2c5e35", "#5a9a45"]

# ---------------------------------------------------------- traffic signals (s)
GREEN, YELLOW, ALL_RED = 15.0, 3.0, 2.0
PHASE = GREEN + YELLOW + ALL_RED   # each axis gets one phase
CYCLE = 2 * PHASE                  # north-south then east-west

# -------------------------------------------------------------- car following
IDM_A = 1.5        # max acceleration (m/s^2)
IDM_B = 2.5        # comfortable deceleration (m/s^2)
IDM_S0 = 2.0       # minimum gap (m)
IDM_T = 1.2        # desired time headway (s)
EGO_CRUISE_MPS = 12.0

# ------------------------------------------------------------- vehicle energy
VEHICLE_MASS = 1600.0          # kg
AIR_DENSITY = 1.2
DRAG_CDA = 0.29 * 2.2          # drag coefficient x frontal area (m^2)
ROLLING_COEF = 0.011
DRIVETRAIN_EFF = 0.9
REGEN_EFF = 0.6
AUX_LOAD_W = 400.0
BATTERY_KWH = 40.0
START_SOC = 0.70
RATED_KWH_PER_KM = 0.165       # rated consumption used for the range estimate

EGO_ID = "ego"
OFFICE_W = 16.0             # m, Advancing Analytics office footprint
OFFICE_H = 18.0             # m, five storeys

# ------------------------------------------------------------ collision bodies
CAR_HALF_LEN = 2.25         # m, half the car length (4.5 m)
CAR_HALF_W = 0.95           # m, half the car width (1.9 m)
LANE_BAND = CAR_HALF_W + 0.25   # lateral clearance used when looking for a car ahead
PED_HALF = 0.45             # m, pedestrian body half-size
POLE_HALF = 0.3             # m, signal pole half-size
BILLBOARD_WIDTH = {"hotseat": 11.0, "advancing": 11.0}   # panel widths, see main.js (both square)
TREE_HALF = 1.8             # m per unit of tree scale (canopy radius)
HOUSE_HALF = 3.0            # m, house is 6 m square
STATIC_CELL = 20.0          # m, size of the spatial index cells

# ------------------------------------------------------------ human driver
HUMAN_ACCEL = 3.0           # m/s^2 with W held
HUMAN_BRAKE = 8.0           # m/s^2 with S held
HUMAN_DRAG = 0.05           # 1/s rolling resistance
HUMAN_MAX_SPEED = 30.0      # m/s (about 67 mph)
HUMAN_MAX_YAW = 1.3         # rad/s with A/D held at low speed
HUMAN_STEER_RATE = 4.0      # 1/s, how quickly the steering responds
HUMAN_INPUT_TIMEOUT = 0.6   # s; keys are released if no input arrives for this long
ARRIVE_RADIUS = 18.0        # m, how close counts as reaching the target
MPS_TO_MPH = 2.23694
# Autonomous driving comfort limits. The commanded acceleration may change by at most
# JERK_MAX per second, so cars ease on and off the throttle and brake instead of jolting.
JERK_MAX = 6.0              # m/s^3, how quickly acceleration may build up
JERK_BRAKE = 16.0           # m/s^3, gentle braking builds up at this rate
JERK_EMERGENCY = 60.0       # m/s^3, braking harder than EMERGENCY_DECEL is applied almost at once
EMERGENCY_DECEL = 4.0       # m/s^2
A_FLOOR = -9.0              # m/s^2, hardest braking the autonomous controller will request


# ---------------------------------------------------------------- helpers
def _unit(x: float, z: float) -> tuple[float, float]:
    n = math.hypot(x, z) or 1.0
    return x / n, z / n


def _node_xy(node: tuple[int, int]) -> tuple[float, float]:
    return node[0] * PITCH, node[1] * PITCH


def _block_bounds(bi: int, bj: int) -> tuple[float, float, float, float]:
    """Solid block footprint between the road edges."""
    x0 = bi * PITCH + ROAD / 2
    x1 = (bi + 1) * PITCH - ROAD / 2
    z0 = bj * PITCH + ROAD / 2
    z1 = (bj + 1) * PITCH - ROAD / 2
    return x0, z0, x1, z1


def _sidewalk_rect(bi: int, bj: int) -> tuple[float, float, float, float]:
    x0, z0, x1, z1 = _block_bounds(bi, bj)
    return x0 + SIDEWALK_INSET, z0 + SIDEWALK_INSET, x1 - SIDEWALK_INSET, z1 - SIDEWALK_INSET


def _perimeter_len(rect: tuple[float, float, float, float]) -> float:
    x0, z0, x1, z1 = rect
    return 2 * ((x1 - x0) + (z1 - z0))


def _perimeter_point(rect, s: float):
    """Point and forward direction at distance s around a rectangle."""
    x0, z0, x1, z1 = rect
    w, h = x1 - x0, z1 - z0
    if s < w:
        return x0 + s, z0, 1, 0
    s -= w
    if s < h:
        return x1, z0 + s, 0, 1
    s -= h
    if s < w:
        return x1 - s, z1, -1, 0
    s -= w
    return x0, z1 - s, 0, -1


def _corners(b):
    """Corners of an oriented box (cx, cz, h1, h2, th). h1 runs along (sin th, cos th)."""
    cx, cz, h1, h2, th = b
    s, c = math.sin(th), math.cos(th)
    out = []
    for s1 in (-1.0, 1.0):
        for s2 in (-1.0, 1.0):
            out.append((cx + s * s1 * h1 + c * s2 * h2, cz + c * s1 * h1 - s * s2 * h2))
    return out


def _proj_radius(b, nx, nz):
    """Half-width of oriented box b projected onto the unit axis (nx, nz)."""
    _, _, h1, h2, th = b
    s, c = math.sin(th), math.cos(th)
    return h1 * abs(s * nx + c * nz) + h2 * abs(c * nx - s * nz)


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def _lane_heading(x: float, z: float, th: float):
    """Direction of the road under (x, z), as the heading closest to th, or None if off-road."""
    best = None
    for k in range(GRID):
        if abs(z - k * PITCH) < ROAD / 2 - 0.5:           # on an east-west road: heading +/- 90 deg
            for c in (math.pi / 2, -math.pi / 2):
                d = abs(_wrap(c - th))
                if best is None or d < best[0]:
                    best = (d, c)
        if abs(x - k * PITCH) < ROAD / 2 - 0.5:           # on a north-south road: heading 0 or 180 deg
            for c in (0.0, math.pi):
                d = abs(_wrap(c - th))
                if best is None or d < best[0]:
                    best = (d, c)
    return None if best is None else best[1]


def obb_mtv(a, b):
    """Separating-axis test for two oriented boxes.

    Returns None if they do not overlap. Otherwise returns (depth, nx, nz), where
    (nx, nz) points from b towards a and depth is how far a must move to be clear.
    """
    best = None
    dx, dz = a[0] - b[0], a[1] - b[1]
    for th in (a[4], b[4]):
        s, c = math.sin(th), math.cos(th)
        for nx, nz in ((s, c), (c, -s)):
            d = dx * nx + dz * nz
            overlap = _proj_radius(a, nx, nz) + _proj_radius(b, nx, nz) - abs(d)
            if overlap <= 0:
                return None
            if best is None or overlap < best[0]:
                sign = 1.0 if d >= 0 else -1.0
                best = (overlap, nx * sign, nz * sign)
    return best


# ----------------------------------------------------------------- actors
class Vehicle:
    def __init__(self, vid: str, kind: str, start, end, nxt, v: float, v0: float, color: str):
        self.id = vid
        self.kind = kind
        self.start = start        # node the current edge starts from
        self.end = end            # junction the vehicle is approaching
        self.nxt = nxt            # junction after that (decides the turn)
        self.s = 0.0              # progress along the edge (m)
        self.v = v                # speed (m/s)
        self.v0 = v0              # desired speed (m/s)
        self.color = color
        self.a = 0.0              # acceleration this tick (m/s^2), jerk-limited
        self.a_target = 0.0       # acceleration the controller asks for (m/s^2)
        self.sensor_gap = SENSOR_RANGE_M
        self.turning = False
        self.curve = None         # (P0, P1, P2) control points of the turn
        # Human driver state (used only when human is True)
        self.human = False
        self.hx = 0.0
        self.hz = 0.0
        self.hth = 0.0            # heading angle: forward = (sin, cos)
        self.hsteer = 0.0


@dataclass
class Pedestrian:
    id: str
    block: tuple
    s: float
    d: int                        # +1 or -1 walking direction around the block
    speed: float
    color: str
    x: float = 0.0
    z: float = 0.0
    rot: float = 0.0
    vx: float = 0.0
    vz: float = 0.0
    phase: float = 0.0


# -------------------------------------------------------------- simulation
class Simulation:
    def __init__(self, cfg, seed: int | None = None):
        if cfg.drive_side not in ("left", "right"):
            raise ValueError("DRIVE_SIDE must be 'left' or 'right'")
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.drive_side = cfg.drive_side
        # +1 keeps vehicles to the right of travel, -1 to the left.
        self.side_sign = 1.0 if cfg.drive_side == "right" else -1.0
        self.t = 0.0
        self.tick = 0
        self.telemetry_every = max(1, round(cfg.sim_tick_hz / cfg.telemetry_hz))
        self.energy_used_kwh = 0.0

        # Each junction runs its own offset so the grid is not perfectly in step.
        self.node_offset = {
            (i, j): self.rng.uniform(0, CYCLE) for i in range(GRID) for j in range(GRID)
        }

        self.mode = "menu"                    # menu (world frozen), autonomous or human
        self.session: dict | None = None      # active human driver session
        self.human_input = {"throttle": False, "brake": False, "left": False, "right": False}
        self.human_input_t = -1e9
        self.target: tuple[float, float] | None = None
        self.reached = 0
        self._human_touching = False
        self._relax_until = -1.0      # deadlock watchdog: sets when the approach rule is briefly lifted
        self._stalled_for = 0.0

        self.layout = self._build_layout()
        self._static = self._build_static_index()
        self.vehicles: list[Vehicle] = []
        self.pedestrians: list[Pedestrian] = []
        self._spawn_vehicles()
        self._spawn_pedestrians()

        poses = self._poses()
        self.latest_telemetry = self._telemetry(self.ego, poses[EGO_ID])
        self.snapshot = self._build_snapshot(poses)

    # ------------------------------------------------------------ layout
    def _build_layout(self) -> dict:
        houses = []
        trees = []
        centre = (GRID - 1) * PITCH / 2                 # middle of the city, centre of block (1, 1)
        office = {"x": centre, "z": centre, "w": OFFICE_W, "d": OFFICE_W, "h": OFFICE_H}
        for bi in range(GRID - 1):
            for bj in range(GRID - 1):
                x0, z0, x1, z1 = _block_bounds(bi, bj)
                # Houses sit along each edge, facing the road.
                for t in (6, 18, 30, 42, 54):
                    houses.append(self._house(x0 + t, z0 + HOUSE_SETBACK, "n"))
                    houses.append(self._house(x0 + t, z1 - HOUSE_SETBACK, "s"))
                for t in (18, 30, 42):
                    houses.append(self._house(x0 + HOUSE_SETBACK, z0 + t, "w"))
                    houses.append(self._house(x1 - HOUSE_SETBACK, z0 + t, "e"))

                # Trees in the lawn, kept clear of each other and the houses.
                placed: list[tuple[float, float]] = []
                for _ in range(300):
                    if len(placed) >= 6:
                        break
                    x = self.rng.uniform(x0 + 12, x1 - 12)
                    z = self.rng.uniform(z0 + 12, z1 - 12)
                    if math.hypot(x - centre, z - centre) < OFFICE_W / 2 + 6:
                        continue                          # keep the office and its frontage clear
                    if all(math.hypot(x - px, z - pz) > 8 for px, pz in placed):
                        placed.append((x, z))
                        trees.append({
                            "x": round(x, 2),
                            "z": round(z, 2),
                            "kind": self.rng.choice(["round", "conifer"]),
                            "scale": round(self.rng.uniform(0.8, 1.3), 2),
                            "color": self.rng.choice(TREE_COLORS),
                        })

        span = (GRID - 1) * PITCH

        # Billboards in two rings around the outside of the city, alternating artwork.
        # Ring 1 sits just outside the roads, ring 2 further out.
        billboards = []
        image_index = 0
        for offset, spacing in ((24.0, 36.0), (60.0, 45.0)):
            n = round(span / spacing) + 1
            positions = [k * span / (n - 1) for k in range(n)]
            edge_points = (
                [(t, -offset) for t in positions]          # north edge
                + [(t, span + offset) for t in positions]  # south edge
                + [(-offset, t) for t in positions]        # west edge
                + [(span + offset, t) for t in positions]  # east edge
            )
            for x, z in edge_points:
                billboards.append({
                    "x": round(x, 2),
                    "z": round(z, 2),
                    "image": "hotseat" if image_index % 2 == 0 else "advancing",
                })
                image_index += 1

        # Trees on the grass outside the 3 x 3 block city, clear of the billboards.
        placed_outer: list[tuple[float, float]] = []
        attempts = 0
        while len(placed_outer) < 90 and attempts < 6000:
            attempts += 1
            x = self.rng.uniform(-150, span + 150)
            z = self.rng.uniform(-150, span + 150)
            if -40 < x < span + 40 and -40 < z < span + 40:
                continue                      # keep the city area itself clear
            if any(math.hypot(x - b["x"], z - b["z"]) < 14 for b in billboards):
                continue
            if any(math.hypot(x - px, z - pz) < 8 for px, pz in placed_outer):
                continue
            placed_outer.append((x, z))
            trees.append({
                "x": round(x, 2),
                "z": round(z, 2),
                "kind": self.rng.choice(["round", "conifer"]),
                "scale": round(self.rng.uniform(0.9, 1.5), 2),
                "color": self.rng.choice(TREE_COLORS),
            })

        nodes = [
            {"i": i, "j": j, "x": i * PITCH, "z": j * PITCH}
            for i in range(GRID)
            for j in range(GRID)
        ]
        return {
            "grid": GRID,
            "pitch": PITCH,
            "block": BLOCK,
            "road": ROAD,
            "center": span / 2,
            "drive_side": self.drive_side,
            "nodes": nodes,
            "houses": houses,
            "trees": trees,
            "office": office,
            "billboards": billboards,
        }

    def _house(self, x: float, z: float, face: str) -> dict:
        return {
            "x": round(x, 2),
            "z": round(z, 2),
            "w": 6.0,
            "d": 6.0,
            "h": round(self.rng.uniform(6.0, 13.0), 1),
            "color": self.rng.choice(HOUSE_COLORS),
            "face": face,          # which side faces the road: n, s, e or w
        }

    # ------------------------------------------------------------ spawning
    def _spawn_vehicles(self) -> None:
        ego = Vehicle(EGO_ID, "ego", (0, 0), (1, 0), (1, 1), v=0.0, v0=EGO_CRUISE_MPS, color="#d7261e")
        ego.nxt = self.choose_next((0, 0), (1, 0))
        self._refresh_turn(ego)
        self.vehicles.append(ego)
        self.ego = ego

        for k in range(self.cfg.num_ambient_cars):
            start = (self.rng.randrange(GRID), self.rng.randrange(GRID))
            end = self.choose_next(None, start)
            nxt = self.choose_next(start, end)
            car = Vehicle(
                f"npc-{k:02d}",
                "npc",
                start,
                end,
                nxt,
                v=self.rng.uniform(4.0, 9.0),
                v0=self.rng.uniform(8.0, 12.0),
                color=self.rng.choice(CAR_COLORS),
            )
            car.s = self.rng.uniform(0, PITCH * 0.8)
            self._refresh_turn(car)
            self.vehicles.append(car)

    def _spawn_pedestrians(self) -> None:
        for k in range(self.cfg.num_pedestrians):
            bi = self.rng.randrange(GRID - 1)
            bj = self.rng.randrange(GRID - 1)
            perim = _perimeter_len(_sidewalk_rect(bi, bj))
            ped = Pedestrian(
                id=f"ped-{k:02d}",
                block=(bi, bj),
                s=self.rng.uniform(0, perim),
                d=self.rng.choice((1, -1)),
                speed=self.rng.uniform(1.0, 1.6),
                color=self.rng.choice(PED_COLORS),
            )
            self._place_pedestrian(ped)
            self.pedestrians.append(ped)

    # ------------------------------------------------------------ road graph
    def _neighbours(self, node):
        i, j = node
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < GRID and 0 <= nj < GRID:
                yield (ni, nj)

    def choose_next(self, prev, cur):
        """Pick the next junction, avoiding a U-turn where possible."""
        options = [n for n in self._neighbours(cur) if n != prev]
        if not options:
            options = list(self._neighbours(cur))
        return self.rng.choice(options)

    def _dir(self, a, b) -> tuple[float, float]:
        ax, az = _node_xy(a)
        bx, bz = _node_xy(b)
        return _unit(bx - ax, bz - az)

    def _lateral(self, f) -> tuple[float, float]:
        """Unit vector to the vehicle's lane side for a direction of travel f."""
        return -f[1] * self.side_sign, f[0] * self.side_sign

    def _point(self, start, end, s: float):
        """World position and unit forward vector for a point s metres along an edge."""
        ax, az = _node_xy(start)
        fx, fz = self._dir(start, end)
        rx, rz = self._lateral((fx, fz))
        return ax + fx * s + rx * LANE_OFFSET, az + fz * s + rz * LANE_OFFSET, fx, fz

    def _refresh_turn(self, v: Vehicle) -> None:
        """Work out whether the vehicle turns at the junction ahead and, if so,
        the Bezier control points of its path through the junction."""
        fi = self._dir(v.start, v.end)
        fo = self._dir(v.end, v.nxt)
        v.turning = (fi[0] * fo[0] + fi[1] * fo[1]) < 0.5   # perpendicular = a turn
        if not v.turning:
            v.curve = None
            return

        node = _node_xy(v.end)
        ri = self._lateral(fi)
        ro = self._lateral(fo)
        L, C = LANE_OFFSET, TURN_WINDOW
        # Intersection of the incoming and outgoing lane centre lines.
        a11, a12 = -fi[0], -fo[0]
        a21, a22 = -fi[1], -fo[1]
        dx = (ro[0] - ri[0]) * L
        dz = (ro[1] - ri[1]) * L
        det = a11 * a22 - a12 * a21
        t = (dx * a22 - a12 * dz) / det
        p1 = (node[0] + ri[0] * L - fi[0] * t, node[1] + ri[1] * L - fi[1] * t)
        p0 = (node[0] + ri[0] * L - fi[0] * C, node[1] + ri[1] * L - fi[1] * C)
        p2 = (node[0] + ro[0] * L + fo[0] * C, node[1] + ro[1] * L + fo[1] * C)
        v.curve = (p0, p1, p2)

    def _curve_point(self, v: Vehicle):
        (x0, z0), (x1, z1), (x2, z2) = v.curve
        tau = (v.s - (PITCH - TURN_WINDOW)) / (2 * TURN_WINDOW)
        tau = min(1.0, max(0.0, tau))
        a = (1 - tau) ** 2
        b = 2 * (1 - tau) * tau
        c = tau ** 2
        x = a * x0 + b * x1 + c * x2
        z = a * z0 + b * z1 + c * z2
        # Tangent gives the heading, so the car rotates smoothly through the turn.
        dx = 2 * (1 - tau) * (x1 - x0) + 2 * tau * (x2 - x1)
        dz = 2 * (1 - tau) * (z1 - z0) + 2 * tau * (z2 - z1)
        fx, fz = _unit(dx, dz)
        return x, z, fx, fz

    def pose(self, v: Vehicle):
        """True position and forward vector."""
        if v.human:
            return v.hx, v.hz, math.sin(v.hth), math.cos(v.hth)
        if v.turning and v.s >= PITCH - TURN_WINDOW:
            return self._curve_point(v)
        return self._point(v.start, v.end, v.s)

    def _poses(self) -> dict:
        return {v.id: self.pose(v) for v in self.vehicles}

    # --------------------------------------------------------- signals
    def light(self, node, axis: int) -> str:
        """axis 0 = north-south traffic, axis 1 = east-west traffic."""
        p = (self.t + self.node_offset[node] - axis * PHASE) % CYCLE
        if p < GREEN:
            return "green"
        if p < GREEN + YELLOW:
            return "yellow"
        return "red"

    # ------------------------------------------------------- control (IDM)
    def _control(self, v: Vehicle, poses: dict) -> None:
        x, z, fx, fz = poses[v.id]

        # Nearest car whose outline is in our path, using the car's own footprint.
        gap, lead_speed = self._lead_gap((x, z, CAR_HALF_LEN, CAR_HALF_W, math.atan2(fx, fz)), fx, fz, v, poses)

        # Each constraint is (distance to a point, speed of that point).
        constraints = []
        if gap is not None:
            v.sensor_gap = min(gap, SENSOR_RANGE_M)
            constraints.append((gap, lead_speed))
        else:
            v.sensor_gap = SENSOR_RANGE_M

        axis = 0 if abs(fz) >= abs(fx) else 1
        d = PITCH - v.s - STOP_OFFSET
        if 0.0 < d < 80.0:
            state = self.light(v.end, axis)
            if state == "red" or (state == "yellow" and v.v * v.v / (2 * IDM_B) < d):
                constraints.append((d, 0.0))
        # Only one car occupies a junction at a time, so cars can never meet inside one.
        # A car already inside has priority. Anyone approaching waits at the stop line.
        if v.s <= PITCH - 9.5 and self._junction_occupied(v, v.end):
            constraints.append((max(PITCH - STOP_OFFSET - v.s, 0.1), 0.0))

        a_free = IDM_A * (1.0 - (v.v / v.v0) ** 4)
        a = a_free
        for g, lv in constraints:
            g = max(g, 0.1)
            dv = v.v - lv
            s_star = IDM_S0 + max(0.0, v.v * IDM_T + v.v * dv / (2 * math.sqrt(IDM_A * IDM_B)))
            a = min(a, a_free - IDM_A * (s_star / g) ** 2)

        if v.turning and v.s < PITCH - TURN_WINDOW:
            # Corner speed limit: the fastest we may be at this distance from the
            # curve is the speed from which we can still slow to TURN_SPEED using
            # comfortable braking. Expressed as the acceleration needed to hit it.
            g = max(PITCH - TURN_WINDOW - v.s, 0.1)
            v_allowed_sq = TURN_SPEED ** 2 + 2 * IDM_B * g
            # Firm but not emergency braking on the approach; the rest is eased off in the curve.
            a = min(a, max(-4.0, (v_allowed_sq - v.v ** 2) / (2 * g)))
        elif v.turning and v.s < PITCH + TURN_WINDOW:
            # Inside the curve: ease down to the corner speed over the distance left,
            # so the car never has to brake hard in the middle of a turn.
            remaining = max(PITCH + TURN_WINDOW - v.s, 1.0)
            a = min(a, (TURN_SPEED ** 2 - v.v ** 2) / (2 * remaining))
        v.a_target = max(A_FLOOR, min(IDM_A, a))

    def _integrate(self, v: Vehicle, dt: float) -> None:
        # Limit how quickly acceleration changes. Braking may build faster than
        # acceleration so that a car can still stop safely when it has to.
        delta = v.a_target - v.a
        if delta < 0:
            braking_now = -v.a_target > EMERGENCY_DECEL
            step = (JERK_EMERGENCY if braking_now else JERK_BRAKE) * dt
        else:
            step = JERK_MAX * dt
        v.a += max(-step, min(step, delta))
        v_new = max(0.0, v.v + v.a * dt)
        v.s += 0.5 * (v.v + v_new) * dt
        v.v = v_new
        # The junction switch happens after the turn curve has finished, so the
        # path stays continuous.
        while v.s >= PITCH + TURN_WINDOW:
            v.s -= PITCH
            v.start, v.end = v.end, v.nxt
            v.nxt = self.choose_next(v.start, v.end)
            self._refresh_turn(v)

    def _energy(self, v: Vehicle, dt: float) -> None:
        vel = v.v
        power = (
            0.5 * AIR_DENSITY * DRAG_CDA * vel ** 3
            + ROLLING_COEF * VEHICLE_MASS * 9.81 * vel
            + VEHICLE_MASS * v.a * vel
        )
        power = power / DRIVETRAIN_EFF if power > 0 else power * REGEN_EFF
        power += AUX_LOAD_W
        self.energy_used_kwh += power * dt / 3.6e6

    def _remaining_kwh(self) -> float:
        return max(0.0, BATTERY_KWH * START_SOC - self.energy_used_kwh)

    # ----------------------------------------------------------- pedestrians
    def _place_pedestrian(self, p: Pedestrian) -> None:
        rect = _sidewalk_rect(*p.block)
        x, z, dx, dz = _perimeter_point(rect, p.s)
        p.x, p.z = x, z
        p.rot = math.atan2(dx * p.d, dz * p.d)
        p.vx = dx * p.d * p.speed
        p.vz = dz * p.d * p.speed

    def _walk(self, p: Pedestrian, dt: float) -> None:
        perim = _perimeter_len(_sidewalk_rect(*p.block))
        p.s = (p.s + p.d * p.speed * dt) % perim
        p.phase += p.speed * dt * 4.0
        self._place_pedestrian(p)

    # ------------------------------------------------------------ sensors
    def _tyres(self) -> dict:
        """Four tyre pressures in bar. The rear-left tyre has a slow leak after
        150 s so the telemetry shows a realistic anomaly to detect."""
        t = self.t
        base = 2.5
        leak = max(0.0, min(0.6, (t - 150.0) * 0.0008))
        wob = lambda k: 0.02 * math.sin(t / 25.0 + k)
        return {
            "tyre_pressure_fl_bar": round(base + wob(0), 3),
            "tyre_pressure_fr_bar": round(base + wob(1), 3),
            "tyre_pressure_rl_bar": round(base - leak + wob(2), 3),
            "tyre_pressure_rr_bar": round(base + wob(3), 3),
        }

    def _telemetry(self, ego: Vehicle, pose) -> dict:
        x, z, fx, fz = pose
        remaining = self._remaining_kwh()
        record = {
            "vehicle_id": EGO_ID,
            "driver_mode": "human" if ego.human else "autonomous",
            "vehicle_reg": self.session["reg"] if self.session else None,
            "driver_name": self.session["name"] if self.session else None,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "sim_time_s": round(self.t, 2),
            "speed_mph": round(ego.v * MPS_TO_MPH, 2),
            "heading_deg": round((math.degrees(math.atan2(fx, -fz)) + 360.0) % 360.0, 2),
            "x_m": round(x, 2),
            "z_m": round(z, 2),
            # north is -z in the scene
            "latitude": round(self.cfg.gps_origin_lat - z / 111_320.0, 7),
            "longitude": round(
                self.cfg.gps_origin_lon
                + x / (111_320.0 * math.cos(math.radians(self.cfg.gps_origin_lat))),
                7,
            ),
            # The sensor only reports cars within 10 m; beyond that the value is null.
            "distance_to_vehicle_ahead_m": round(ego.sensor_gap, 2) if ego.sensor_gap <= SENSOR_REPORT_M else None,
            "braking_force_kn": round(VEHICLE_MASS * max(0.0, -ego.a) / 1000.0, 3),
            "acceleration_force_kn": round(VEHICLE_MASS * max(0.0, ego.a) / 1000.0, 3),
            "range_km": round(remaining / RATED_KWH_PER_KM, 2),
            "battery_soc_pct": round(remaining / BATTERY_KWH * 100.0, 2),
        }
        record.update(self._tyres())
        return record

    # ------------------------------------------------------------ main step
    def step(self, dt: float) -> list[dict]:
        """Advance the world by dt seconds. Returns telemetry records due for sending."""
        self.t += dt
        self.tick += 1

        poses = self._poses()
        for v in self.vehicles:
            if not v.human:
                self._control(v, poses)
        if self.ego.human:
            self._human_step(self.ego, poses, dt)
        for v in self.vehicles:
            if not v.human:
                self._integrate(v, dt)
        self._energy(self.ego, dt)
        self._watchdog(dt)
        if self.session is not None:
            self._session_tick(dt)

        poses = self._poses()
        for p in self.pedestrians:
            self._walk(p, dt)

        self.latest_telemetry = self._telemetry(self.ego, poses[EGO_ID])
        self.snapshot = self._build_snapshot(poses)

        emit = []
        if self.tick % self.telemetry_every == 0:
            emit.append(self.latest_telemetry)
        return emit

    def _build_snapshot(self, poses: dict) -> dict:
        def actor(v: Vehicle) -> dict:
            x, z, fx, fz = poses[v.id]
            return {
                "x": round(x, 3),
                "z": round(z, 3),
                "vx": round(v.v * fx, 3),
                "vz": round(v.v * fz, 3),
                "rot": math.atan2(fx, fz),
                "braking": v.a < -0.5,
            }

        ego = actor(self.ego)
        ego["telemetry"] = self.latest_telemetry
        ego["mph"] = round(self.ego.v * MPS_TO_MPH, 1)
        lights = [
            {
                "i": i,
                "j": j,
                "ns": self.light((i, j), 0),
                "ew": self.light((i, j), 1),
            }
            for i in range(GRID)
            for j in range(GRID)
        ]
        vehicles = []
        for v in self.vehicles:
            if v is self.ego:
                continue
            item = actor(v)
            item.update({"id": v.id, "color": v.color})
            vehicles.append(item)
        pedestrians = [
            {
                "id": p.id,
                "x": round(p.x, 3),
                "z": round(p.z, 3),
                "vx": round(p.vx, 3),
                "vz": round(p.vz, 3),
                "rot": p.rot,
                "phase": round(p.phase, 3),
                "color": p.color,
            }
            for p in self.pedestrians
        ]
        return {
            "t": round(self.t, 3),
            "ego": ego,
            "vehicles": vehicles,
            "pedestrians": pedestrians,
            "lights": lights,
            "session": self._session_view(),
        }

    # ------------------------------------------------------ human driver mode
    def _in_junction(self, o: Vehicle, poses: dict) -> bool:
        """True when a car is within a junction (where it has priority over crossing traffic)."""
        if o.human:
            x, z, _, _ = poses[o.id]
            nx = round(x / PITCH) * PITCH
            nz = round(z / PITCH) * PITCH
            return math.hypot(x - nx, z - nz) < 9.5
        return o.s < 9.5 or o.s > PITCH - 9.5

    def _watchdog(self, dt: float) -> None:
        """If every autonomous car has been stopped for several seconds, briefly lift the
        approach rule so the jam clears. Cars inside junctions are still protected."""
        autonomous = [v for v in self.vehicles if not v.human]
        if autonomous and all(v.v < 0.1 for v in autonomous):
            self._stalled_for += dt
        else:
            self._stalled_for = 0.0
        if self._stalled_for > 6.0:
            self._relax_until = self.t + 4.0
            self._stalled_for = 0.0

    def _zone_node(self, o: Vehicle):
        """The junction a car is currently inside, or None."""
        if o.s > PITCH - 9.5:
            return o.end
        if o.s < 9.5:
            return o.start
        return None

    def _junction_occupied(self, me: Vehicle, node) -> bool:
        """True if another car is inside this junction, or is about to enter it and has
        priority (lower id). Priority is decided by id so two cars can never both enter."""
        for o in self.vehicles:
            if o is me or o.human:
                continue
            if self._zone_node(o) == node:
                return True
            if (self._relax_until <= self.t and o.end == node
                    and o.s > PITCH - 12.0 and o.id < me.id):
                return True
        return False

    def _lead_gap(self, a_box, fx, fz, me, poses):
        """Gap to the nearest other car whose outline overlaps our path ahead.

        Cars travelling the same way are followed. Cars crossing our path are yielded
        to only while we are outside the junction. Cars already inside a junction
        have priority, which stops two cars waiting on each other.

        Returns (gap, speed of that car along our heading), or (None, 0.0).
        """
        ax, az = a_box[0], a_box[1]
        rx, rz = -fz, fx                      # right-hand side of travel
        me_in_junction = self._in_junction(me, poses)
        best = None
        best_speed = 0.0
        for o in self.vehicles:
            if o is me:
                continue
            ox, oz, ofx, ofz = poses[o.id]
            crossing = (fx * ofx + fz * ofz) < 0.5
            # A car already turning through a junction is committed: crossing traffic
            # gives way to it, so it never stops in the middle of the turn.
            committed = (not me.human) and me.turning and (PITCH - TURN_WINDOW <= me.s < PITCH + TURN_WINDOW)
            if crossing and committed:
                continue
            if crossing:
                o_in_junction = self._in_junction(o, poses)
                # Both inside a junction: the car with the lower id goes first, so one always clears.
                have_priority = me_in_junction and (not o_in_junction or me.id < o.id)
                if have_priority or (not me_in_junction and not o_in_junction):
                    continue
            b = (ox, oz, CAR_HALF_LEN, CAR_HALF_W, math.atan2(ofx, ofz))
            min_u = min_w = float("inf")
            max_u = max_w = -float("inf")
            for cx, cz in _corners(b):
                dx, dz = cx - ax, cz - az
                u = dx * fx + dz * fz
                w = dx * rx + dz * rz
                min_u = min(min_u, u)
                max_u = max(max_u, u)
                min_w = min(min_w, w)
                max_w = max(max_w, w)
            if max_u <= 0 or min_w > LANE_BAND or max_w < -LANE_BAND:
                continue                       # behind us, or clear of our lane
            gap = max(min_u - CAR_HALF_LEN, 0.1)
            if best is None or gap < best:
                best = gap
                best_speed = o.v * (ofx * fx + ofz * fz)
        return best, best_speed

    def _static_near(self, x: float, z: float) -> list:
        kx = math.floor(x / STATIC_CELL)
        kz = math.floor(z / STATIC_CELL)
        out = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                out.extend(self._static.get((kx + dx, kz + dz), ()))
        return out

    def _build_static_index(self) -> dict:
        """Houses, trees, billboards and signal poles as oriented boxes, in spatial cells."""
        boxes = []
        for h in self.layout["houses"]:
            boxes.append((h["x"], h["z"], HOUSE_HALF, HOUSE_HALF, 0.0))
        for t in self.layout["trees"]:
            r = TREE_HALF * t["scale"]
            boxes.append((t["x"], t["z"], r, r, 0.0))
        centre = (GRID - 1) * PITCH / 2
        for b in self.layout["billboards"]:
            w = BILLBOARD_WIDTH[b["image"]]
            th = math.atan2(centre - b["x"], centre - b["z"])   # panel faces the city centre
            boxes.append((b["x"], b["z"], 0.5, (w + 0.8) / 2, th))
        for n in self.layout["nodes"]:
            for sx in (-1, 1):
                for sz in (-1, 1):
                    boxes.append((n["x"] + sx * 7.5, n["z"] + sz * 7.5, POLE_HALF, POLE_HALF, 0.0))
        o = self.layout["office"]
        boxes.append((o["x"], o["z"], o["w"] / 2, o["d"] / 2, 0.0))
        index: dict = {}
        for bx in boxes:
            key = (math.floor(bx[0] / STATIC_CELL), math.floor(bx[1] / STATIC_CELL))
            index.setdefault(key, []).append(bx)
        return index

    def _nearest_ahead_gap(self, v: Vehicle, poses: dict) -> float:
        gap, _ = self._lead_gap((v.hx, v.hz, CAR_HALF_LEN, CAR_HALF_W, v.hth),
                                math.sin(v.hth), math.cos(v.hth), v, poses)
        return SENSOR_RANGE_M if gap is None else min(gap, SENSOR_RANGE_M)

    def _collide_human(self, v: Vehicle, poses: dict) -> None:
        """Stop the human-driven car at any contact and push it clear of the overlap."""
        a = (v.hx, v.hz, CAR_HALF_LEN, CAR_HALF_W, v.hth)
        fx, fz = math.sin(v.hth), math.cos(v.hth)
        obstacles = []
        for o in self.vehicles:
            if o is v:
                continue
            ox, oz, ofx, ofz = poses[o.id]
            obstacles.append((ox, oz, CAR_HALF_LEN, CAR_HALF_W, math.atan2(ofx, ofz)))
        for p in self.pedestrians:
            obstacles.append((p.x, p.z, PED_HALF, PED_HALF, 0.0))
        obstacles.extend(self._static_near(v.hx, v.hz))

        touching = False
        for _ in range(3):                    # a few passes in case we were pushed into something else
            moved = False
            for b in obstacles:
                hit = obb_mtv(a, b)
                if hit is None:
                    continue
                depth, nx, nz = hit
                moved = True
                into = fx * nx + fz * nz      # below zero means we were driving into it
                if into < 0.3:
                    # Contact that we are not clearly moving away from: back off along our
                    # own heading until clear, so the car stops rather than sliding past.
                    touching = True
                    v.v *= max(0.0, 1.0 + min(into, 0.0))
                    back = 0.0
                    while back < 1.0 and obb_mtv((a[0] - fx * back, a[1] - fz * back, a[2], a[3], a[4]), b) is not None:
                        back += 0.02
                    a = (a[0] - fx * (back + 0.02), a[1] - fz * (back + 0.02), a[2], a[3], a[4])
                else:
                    # Glancing contact: push clear along the shortest way out.
                    depth += 0.02             # small clearance so the bodies rest just apart
                    a = (a[0] + nx * depth, a[1] + nz * depth, a[2], a[3], a[4])
            if not moved:
                break
        v.hx, v.hz = a[0], a[1]
        if touching and not self._human_touching:
            self.session["collisions"] += 1
        self._human_touching = touching

    def _human_step(self, v: Vehicle, poses: dict, dt: float) -> None:
        """Free driving for the human-controlled car.

        W accelerates, S brakes, A steers left and D steers right. Turning needs
        some speed, and the car cannot reverse.
        """
        inp = self.human_input
        target_steer = (1.0 if inp["left"] else 0.0) - (1.0 if inp["right"] else 0.0)
        v.hsteer += (target_steer - v.hsteer) * min(1.0, HUMAN_STEER_RATE * dt)

        v_old = v.v
        a = 0.0
        if inp["brake"] and v_old > 0.05:
            a -= HUMAN_BRAKE
        elif inp["throttle"]:
            a += HUMAN_ACCEL
        a -= HUMAN_DRAG * v_old
        v_new = min(HUMAN_MAX_SPEED, max(0.0, v_old + a * dt))

        # Lane keeping: with no steering input, nudge the car straight when it is on a road
        # and within 10 degrees of the road direction.
        steering = inp["left"] or inp["right"]
        if not steering and v_old > 1.0:
            target = _lane_heading(v.hx, v.hz, v.hth)
            if target is not None:
                diff = _wrap(target - v.hth)
                if abs(diff) <= LKA_MAX_ANGLE:
                    v.hth += diff * min(1.0, LKA_GAIN * dt)

        speed_gate = min(1.0, v_new / 3.0)
        yaw = v.hsteer * HUMAN_MAX_YAW * speed_gate / (1.0 + v_new / 20.0)
        v.hth += yaw * dt                       # positive yaw turns left
        avg = 0.5 * (v_old + v_new)
        v.hx += math.sin(v.hth) * avg * dt
        v.hz += math.cos(v.hth) * avg * dt

        span = (GRID - 1) * PITCH
        v.hx = min(max(v.hx, -200.0), span + 200.0)
        v.hz = min(max(v.hz, -200.0), span + 200.0)
        v.v = v_new
        self._collide_human(v, poses)
        v.a = (v.v - v_old) / dt
        v.sensor_gap = self._nearest_ahead_gap(v, poses)

    def _new_target(self, x: float, z: float) -> tuple[float, float]:
        """A junction at least 150 m away from (x, z)."""
        fallback = None
        for _ in range(200):
            i = self.rng.randrange(GRID)
            j = self.rng.randrange(GRID)
            tx, tz = _node_xy((i, j))
            if math.hypot(tx - x, tz - z) > 150.0:
                return tx, tz
            fallback = (tx, tz)
        return fallback

    def set_mode(self, mode: str) -> None:
        """Choose autonomous driving from the menu. The world only runs in a mode."""
        if mode not in ("menu", "autonomous"):
            raise ValueError("mode must be 'menu' or 'autonomous'")
        if mode == "autonomous" and self.session is not None:
            raise ValueError("A human driver session is already running.")
        self.mode = mode

    def start_human(self, reg: str, name: str) -> None:
        if self.session is not None:
            raise ValueError("A human driver session is already running.")
        ego = self.ego
        x, z, fx, fz = self.pose(ego)           # autonomous pose at the moment of takeover
        ego.human = True
        ego.hx, ego.hz = x, z
        ego.hth = math.atan2(fx, fz)
        ego.hsteer = 0.0
        limit = float(self.cfg.human_driver_seconds)
        self.session = {"reg": reg, "name": name, "remaining": limit, "limit": limit, "collisions": 0}
        self._human_touching = False
        self.mode = "human"
        self.reached = 0
        self.target = self._new_target(x, z)
        self.human_input = {k: False for k in self.human_input}
        self.human_input_t = self.t

    def set_human_input(self, keys: dict) -> None:
        if self.session is None:
            return
        for k in self.human_input:
            self.human_input[k] = bool(keys.get(k, False))
        self.human_input_t = self.t

    def stop_human(self, reason: str) -> None:
        """End the human session and return to the menu. Nothing runs until the user chooses again."""
        self.mode = "menu"
        if self.session is None:
            return
        ego = self.ego
        self._reattach_to_road(ego)
        ego.human = False
        ego.a = 0.0
        self.session = None
        self.target = None
        self.human_input = {k: False for k in self.human_input}

    def _reattach_to_road(self, v: Vehicle) -> None:
        """Put the car back on the nearest road so the autonomous route can resume."""
        px, pz = v.hx, v.hz
        fx, fz = math.sin(v.hth), math.cos(v.hth)
        best = None
        for i in range(GRID):
            for j in range(GRID):
                for ni, nj in ((i + 1, j), (i, j + 1)):
                    if ni >= GRID or nj >= GRID:
                        continue
                    a = _node_xy((i, j))
                    b = _node_xy((ni, nj))
                    dx, dz = _unit(b[0] - a[0], b[1] - a[1])
                    t = ((px - a[0]) * dx + (pz - a[1]) * dz) / PITCH
                    t = min(1.0, max(0.0, t))
                    qx = a[0] + dx * PITCH * t
                    qz = a[1] + dz * PITCH * t
                    d = math.hypot(px - qx, pz - qz)
                    if best is None or d < best[0]:
                        best = (d, (i, j), (ni, nj), t, dx * fx + dz * fz)
        _, A, B, t, dot = best
        if dot >= 0:
            v.start, v.end, v.s = A, B, t * PITCH
        else:
            v.start, v.end, v.s = B, A, (1 - t) * PITCH
        v.s = min(max(v.s, 0.0), PITCH - 0.5)
        v.nxt = self.choose_next(v.start, v.end)
        self._refresh_turn(v)

    def _session_tick(self, dt: float) -> None:
        s = self.session
        s["remaining"] -= dt
        if self.t - self.human_input_t > HUMAN_INPUT_TIMEOUT:
            self.human_input = {k: False for k in self.human_input}
        ego = self.ego
        if self.target and math.hypot(ego.hx - self.target[0], ego.hz - self.target[1]) < ARRIVE_RADIUS:
            self.reached += 1
            self.target = self._new_target(ego.hx, ego.hz)
        if s["remaining"] <= 0:
            self.stop_human("time up")

    def _session_view(self):
        if self.session is None:
            return None
        return {
            "reg": self.session["reg"],
            "name": self.session["name"],
            "remaining_s": round(max(0.0, self.session["remaining"]), 1),
            "limit_s": self.session["limit"],
            "target": {"x": round(self.target[0], 2), "z": round(self.target[1], 2)},
            "reached": self.reached,
            "collisions": self.session["collisions"],
        }
