"""Conditional FPGA resource and timing model used in the scalability study.

The model counts arithmetic lanes, RAM ports and mesh transfers. It does not
synthesize an FPGA, predict routed timing or simulate physical transceivers.
See docs/architecture-model.md for the assumptions and metric definitions.
"""

from pathlib import Path
import collections
import copy
import csv
import json
import math

PROFILE_ROOT = Path(__file__).resolve().parents[1] / "models" / "architecture"
SCREEN_AUDIT = []
WIRE_CACHE = {}
BANK_CACHE = {}

MAC_COSTS = {
    "bin16": dict(lut=17, ff=33),
    "bin32": dict(lut=33, ff=49),
    "u11s8a24": dict(lut=123, ff=147),
    "u21s8a36": dict(lut=205, ff=239),
    "u8s4a32": dict(lut=60, ff=77),
    "u8s8a32": dict(lut=102, ff=132),
}


COST_STATUS = (
    "Artix A200T-3 out-of-context LUT/FF anchors are used as area estimates, "
    "not validated UltraScale or Versal clocks. The 2D signed-product anchor "
    "includes a 10-LUT/2-FF allowance for unsigned inputs. INT8 and INT4 product "
    "pipelines take three and two cycles, respectively. The binary32 estimate "
    "adds 16 adder LUTs and flip-flops to the binary16 anchor."
)


# Layer keys: cin/cout are channel counts, k is the number of kernel sites,
# a/w/acc are activation, weight and accumulator widths, and outbits is the
# complete output-vector width. rq enables integer requantisation.
NETS = {
    "2D": dict(
        d=31,
        R=None,
        n=61,
        nt=1,
        r=5,
        input_bits=4,
        layers=[
            dict(
                cin=4,
                cout=9,
                k=121,
                a=1,
                w=8,
                acc=16,
                outbits=99,
                cost="bin16",
                binary=True,
                rq=False,
            ),
            dict(
                cin=9,
                cout=9,
                k=121,
                a=11,
                w=8,
                acc=24,
                outbits=189,
                cost="u11s8a24",
                binary=False,
                rq=False,
            ),
            dict(
                cin=9,
                cout=2,
                k=121,
                a=21,
                w=8,
                acc=36,
                outbits=2,
                cost="u21s8a36",
                binary=False,
                rq=False,
            ),
        ],
    ),
    "3D": dict(
        d=17,
        R=17,
        n=10,
        nt=18,
        r=1,
        input_bits=12,
        layers=[
            dict(
                cin=12,
                cout=128,
                k=27,
                a=1,
                w=8,
                acc=32,
                outbits=1024,
                cost="bin32",
                binary=True,
                rq=True,
            ),
            dict(
                cin=128,
                cout=128,
                k=27,
                a=8,
                w=4,
                acc=32,
                outbits=1024,
                cost="u8s4a32",
                binary=False,
                rq=True,
            ),
            dict(
                cin=128,
                cout=128,
                k=27,
                a=8,
                w=4,
                acc=32,
                outbits=1024,
                cost="u8s4a32",
                binary=False,
                rq=True,
            ),
            dict(
                cin=128,
                cout=12,
                k=27,
                a=8,
                w=8,
                acc=32,
                outbits=12,
                cost="u8s8a32",
                binary=False,
                rq=False,
                skip=324,
            ),
        ],
    ),
}


def ceildiv(a, b):
    return (a + b - 1) // b


def area(r):
    return (r[1] - r[0]) * (r[3] - r[2])


def expand(r, h, n):
    return max(0, r[0] - h), min(n, r[1] + h), max(0, r[2] - h), min(n, r[3] + h)


def intersect(a, b):
    return max(0, min(a[1], b[1]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[2], b[2])
    )


def regions(n, p):
    """Partition an n-by-n map into p-by-p nonoverlapping rectangles."""
    e = [i * n // p for i in range(p + 1)]
    return [(e[y], e[y + 1], e[x], e[x + 1]) for y in range(p) for x in range(p)]


def exchange_bank_loads(net, p, v):
    """Count exact source reads and destination halo writes by cyclic column.

    Every src/dst request is charged independently, including repeated reads of
    one source site for different neighbours. A local site is never a halo write.
    """
    key = (net["n"], net["r"], p, v)
    if key in BANK_CACHE:
        return BANK_CACHE[key]
    cores = regions(net["n"], p)
    outgoing = [[0] * v for _ in cores]
    incoming = [[0] * v for _ in cores]
    owned = [[0] * v for _ in cores]
    for i, c in enumerate(cores):
        for x in range(c[2], c[3]):
            owned[i][(x - c[2] + net["r"]) % v] += c[1] - c[0]
    for dst, core in enumerate(cores):
        needed = expand(core, net["r"], net["n"])
        for src, owner in enumerate(cores):
            if src == dst:
                continue
            y0 = max(needed[0], owner[0])
            y1 = min(needed[1], owner[1])
            x0 = max(needed[2], owner[2])
            x1 = min(needed[3], owner[3])
            if y1 <= y0 or x1 <= x0:
                continue
            for x in range(x0, x1):
                outgoing[src][(x - owner[2] + net["r"]) % v] += y1 - y0
                incoming[dst][(x - core[2] + net["r"]) % v] += y1 - y0
    result = dict(
        source_halo_read_cycles=max(max(a) for a in outgoing),
        destination_halo_write_cycles=max(max(a) for a in incoming),
        source_copy_and_halo_cycles=max(
            max([a + b for a, b in zip(o, w)]) for o, w in zip(outgoing, owned)
        ),
        destination_copy_and_halo_cycles=max(
            max([a + b for a, b in zip(i, w)]) for i, w in zip(incoming, owned)
        ),
        source_bank_reads=outgoing,
        destination_bank_halo_writes=incoming,
    )
    BANK_CACHE[key] = result
    return result


def wire_geometry(net, p, bits):
    """Exact directed halo payload; XY mesh routing, corners take two hops.

    Eight-neighbor logical requests share four cardinal physical directions.
    Packets traveling farther (possible radius>core size) are routed explicitly.
    Each logical src/dst request carries a 128-bit header. No free multicast.
    """
    key = (net["n"], net["r"], p, bits)
    if key in WIRE_CACHE:
        return WIRE_CACHE[key]
    cache_key = key
    cores = regions(net["n"], p)
    loads = {}
    maxhops = 0
    destination_cells = []
    for dst, core in enumerate(cores):
        needed = expand(core, net["r"], net["n"])
        destination_cells.append(area(needed) - area(core))
        for src, owner in enumerate(cores):
            if src == dst:
                continue
            cells = intersect(needed, owner)
            if not cells:
                continue
            payload = cells * bits + 128
            y, x = divmod(src, p)
            dy, dx = divmod(dst, p)
            hops = abs(x - dx) + abs(y - dy)
            maxhops = max(maxhops, hops)
            while x != dx:
                nx = x + (1 if dx > x else -1)
                key = ((y, x), (y, nx), "x")
                loads[key] = loads.get(key, 0) + payload
                x = nx
            while y != dy:
                ny = y + (1 if dy > y else -1)
                key = ((y, x), (ny, x), "y")
                loads[key] = loads.get(key, 0) + payload
                y = ny
    maxx = max([v for k, v in loads.items() if k[-1] == "x"] or [0])
    maxy = max([v for k, v in loads.items() if k[-1] == "y"] or [0])
    result = dict(
        max_horizontal_bits=maxx,
        max_vertical_bits=maxy,
        max_hops=maxhops,
        total_link_bits=sum(loads.values()),
        directed_links=len(loads),
        max_destination_halo_cells=max(destination_cells),
    )
    WIRE_CACHE[cache_key] = result
    return result


def weight_bram(width, depth):
    # One read port uses a 72-by-512 RAMB36 layout. At depth one the
    # coefficients are constants, but the arithmetic still has an area cost.
    return 0 if depth == 1 else ceildiv(width, 72) * ceildiv(depth, 512)


def layer_options(
    layer,
    cells,
    dsp_allow,
    device,
    native3=False,
    context_sites=1,
    double_frames=False,
    feeder="replicated",
    fixed_v=None,
    side=1,
    radius=1,
    context_height=1,
    context_width=1,
    context_planes=1,
    fixed_kernel=None,
    coarse_grid=False,
):
    """Enumerate lane counts with arithmetic, window and RAM-port costs."""
    q = layer["k"] * layer["cin"]
    co = layer["cout"]
    acc = layer["acc"]
    vs = (
        [fixed_v]
        if fixed_v is not None
        else sorted(set([1, 2, 4, 8, 16, 32, 64, 128, cells]))
    )
    coarse = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, q]
    ps = (
        [q]
        if fixed_kernel
        else sorted(
            set(coarse + ([] if coarse_grid else [3, 6, 12, 24, 48, 96, 192, 384, 768]))
        )
    )
    options = []
    for v in vs:
        if v > cells:
            continue
        if fixed_v is not None and v != fixed_v:
            continue
        for tap in ps:
            if tap > q:
                continue
            fold = ceildiv(q, tap)
            kernel_lanes = min(side, v) if fixed_kernel else v
            lanes = kernel_lanes * co * tap
            packing = 3 if native3 and not layer["binary"] else 1
            dspterms = (
                0 if layer["binary"] else min(tap, (dsp_allow // (v * co)) * packing)
            )
            # A DSP58 group needs three complete products to use the packing credit.
            if packing == 3:
                dspterms = (dspterms // 3) * 3
            dsp = v * co * ceildiv(dspterms, packing)
            fabric = lanes - v * co * dspterms
            cost = MAC_COSTS[layer["cost"]]
            lut = fabric * cost["lut"] + dsp * 8
            ff = fabric * cost["ff"] + dsp * 12
            # Every parallel partial accumulator must eventually enter an exact tree.
            tree = v * co * (tap - 1)
            lut += tree * acc
            ff += tree * acc
            if fixed_kernel:
                # Measured component includes all fixed products, all output trees,
                # registered pipeline, bias/ReLU/sign; no folded-lane constant shortcut.
                lut = kernel_lanes * fixed_kernel["lut"]
                ff = kernel_lanes * fixed_kernel["ff"]
                dsp = kernel_lanes * fixed_kernel["dsp"]
                fabric = lanes
                dspterms = 0
            # Explicit local window copies and fold selectors; input broadcast to Cout.
            window_bits = 2 * v * q * layer["a"]
            ff += window_bits
            selector_lut = v * tap * layer["a"] * ceildiv(max(0, fold - 1), 3)
            lut += selector_lut
            # Bias/ReLU/strict-positive compare, address/valid control allowance.
            lut += v * co * (acc + 8) + v * 256
            ff += v * co * acc + v * 256
            rq = 0
            if layer["rq"]:
                rq = v * ceildiv(co, fold)
                # Keep the 32-by-16 requantisation product at its full width.
                # Two scalar DSPs per unit provide a conservative area allowance.
                dsp += 2 * rq
                lut += rq * 64
                ff += rq * 96
            skipterms = 0
            skip_bram = 0
            skip_gather_cycles = 0
            if layer.get("skip"):
                skipterms = ceildiv(layer["skip"], fold)
                skip_lanes = v * co * skipterms
                lut += (
                    skip_lanes * MAC_COSTS["bin32"]["lut"]
                    + v * co * (skipterms - 1) * 32
                )
                ff += (
                    skip_lanes * MAC_COSTS["bin32"]["ff"]
                    + v * co * (skipterms - 1) * 32
                )
                skip_bram = weight_bram(
                    skipterms * co * 16, ceildiv(layer["skip"], skipterms)
                )
                # The original input supplies 324 binary terms to each skip output.
                ff += 2 * v * layer["skip"]
                skipfetch = min(27, max(1, ceildiv(27, max(1, fold - 2))))
                skip_gather_cycles = ceildiv(27, skipfetch) + 2
                # Read replicas supply the delayed binary skip input.
                skip_bram += (
                    v
                    * skipfetch
                    * ceildiv(
                        (context_sites // context_planes)
                        * layer.get("skip_planes", 64),
                        512,
                    )
                )
            rom_bram = weight_bram(tap * co * layer["w"], fold) + skip_bram
            # Concrete operand feed: each pixel lane has R independent read replicas
            # holding the entire previous map, one complete Cin-channel site/read.
            # Gather all K sites into a double-buffered register window. One producer
            # write/site/cycle is broadcast to every replica; multi-V results serialize.
            fetch_ports = min(layer["k"], max(1, ceildiv(layer["k"], max(1, fold - 2))))
            gather_cycles = ceildiv(layer["k"], fetch_ports) + 2
            read_replica_bram = (
                v
                * fetch_ports
                * ceildiv(layer["cin"] * layer["a"], 72)
                * ceildiv(context_sites, 512)
            )
            if double_frames:
                read_replica_bram *= 2
            batch_ii = max(fold, gather_cycles, v)
            scan_cycles = 0
            row_buffer_bram = 0
            if feeder == "raster":
                planes = 1 if double_frames else 3
                bank_depth = (
                    context_height
                    * ceildiv(context_width, v)
                    * ceildiv(context_planes, planes)
                )
                read_replica_bram = (
                    planes
                    * v
                    * ceildiv(layer["cin"] * layer["a"], 72)
                    * ceildiv(bank_depth, 512)
                )
                if double_frames:
                    read_replica_bram *= 2
                # Each spatial bank reads one Cin-channel vector per cycle.
                row_buffer_bram = (
                    planes
                    * (2 * radius)
                    * v
                    * ceildiv(layer["cin"] * layer["a"], 72)
                    * ceildiv(side + 2 * radius, v * 512)
                )
                read_replica_bram += row_buffer_bram
                ff += layer["k"] * layer["cin"] * layer["a"] * 2
                lut += (
                    planes
                    * v
                    * layer["cin"]
                    * layer["a"]
                    * max(1, math.ceil(math.log2(v)))
                )
                # Scan halo and padding sites through the window. Only owned output
                # batches stall for folded arithmetic. All stages use the same V.
                scan_cycles = (side + 2 * radius) * ceildiv(side + 2 * radius, v)
                active_batches = side * ceildiv(side, v)
                batch_ii = max(fold, skip_gather_cycles)
                gather_cycles = skip_gather_cycles
            # Store every plane in the finite window. Three temporal bank groups
            # expose t-1, t and t+1 together for a 3D convolution.
            product_depth = 1 if layer["binary"] else 2 if layer["w"] == 4 else 3
            pipe = (
                product_depth
                + 1
                + math.ceil(math.log2(max(1, tap)))
                + 2
                + (4 + fold - 1 if layer["rq"] else 0)
            )
            if fixed_kernel:
                pipe = fixed_kernel["latency_cycles"]
            rounds = ceildiv(cells, v)
            service = (
                scan_cycles + active_batches * (batch_ii - 1)
                if feeder == "raster"
                else rounds * batch_ii
            )
            options.append(
                dict(
                    V=v,
                    kernel_lanes=kernel_lanes,
                    P=tap,
                    fold_cycles=fold,
                    cycles_per_slice=service,
                    latency_cycles=gather_cycles
                    + service
                    + pipe
                    + (1 if feeder == "raster" else v),
                    pipe_cycles=pipe,
                    lut=lut,
                    ff=ff,
                    dsp=dsp,
                    weight_bram=rom_bram,
                    operand_bram=read_replica_bram,
                    fetch_ports_per_pixel_lane=fetch_ports,
                    gather_cycles=gather_cycles,
                    batch_ii_cycles=batch_ii,
                    product_pipeline_depth=product_depth,
                    feeder=feeder,
                    raster_scan_cycles=scan_cycles,
                    row_buffer_bram=row_buffer_bram,
                    requant_units=rq,
                    skip_terms_parallel=skipterms,
                    scalar_product_lanes=lanes,
                    dsp_terms_per_output=dspterms,
                    dsp_packing=packing,
                    window_register_bits=window_bits,
                    input_selector_lut=selector_lut,
                    weight_read_bits_per_cycle=(
                        0 if fixed_kernel else tap * co * layer["w"]
                    ),
                    activation_read_bits_per_cycle=v * tap * layer["a"],
                    result_write_bits_per_cycle=v * layer["outbits"],
                )
            )
    return options


def screen(
    net,
    dev,
    p,
    mapping,
    shared=False,
    feeder="replicated",
    fixed_v=None,
    fixed_kernels=None,
    coarse_grid=False,
):
    """Keep a small resource-feasible shortlist for one architecture family."""
    cells = max(map(area, regions(net["n"], p)))
    layers = net["layers"]
    budget = {
        k: int(dev[k] * f)
        for k, f in dev.get(
            "budget_fractions", dict(lut=0.60, ff=0.60, dsp=0.75, bram=0.70)
        ).items()
    }
    if shared:
        wide = max(layers, key=lambda l: MAC_COSTS[l["cost"]]["lut"])
        layers = [
            dict(l, cost=wide["cost"], acc=max(z["acc"] for z in layers), binary=False)
            for l in layers
        ]
    work = [0 if l["binary"] else l["k"] * l["cin"] * l["cout"] for l in layers]
    if mapping == "wide_first":
        work = [
            (
                0
                if l["binary"]
                else l["k"] * l["cin"] * l["cout"] * MAC_COSTS[l["cost"]]["lut"]
            )
            for l in layers
        ]
    if mapping == "lut_only":
        work = [0] * len(layers)
    avail = max(
        0, budget["dsp"] - int(budget["dsp"] * 0.15)
    )  # reserve for requantisers
    allo = [
        (
            avail
            if shared and mapping != "lut_only"
            else int(avail * w / max(1, sum(work)))
        )
        for w in work
    ]
    # Full tile context buffers at every layer, not merely raw owned cells.
    maxexpanded = max(area(expand(c, net["r"], net["n"])) for c in regions(net["n"], p))
    context_sites = maxexpanded * net["nt"]
    expanded = [expand(c, net["r"], net["n"]) for c in regions(net["n"], p)]
    context_height = max(c[1] - c[0] for c in expanded)
    context_width = max(c[3] - c[2] for c in expanded)
    opts = [
        layer_options(
            l,
            cells,
            da,
            dev,
            mapping == "native_dot",
            context_sites,
            net["nt"] == 1,
            feeder,
            fixed_v,
            ceildiv(net["n"], p),
            net["r"],
            context_height,
            context_width,
            net["nt"],
            fixed_kernels[i] if fixed_kernels else None,
            coarse_grid,
        )
        for i, (l, da) in enumerate(zip(layers, allo))
    ]
    mapsbits = (
        sum(l["outbits"] for l in layers[:-1])
        + net["input_bits"]
        + layers[-1]["outbits"]
    )
    mapbits = 2 * maxexpanded * mapsbits * net["nt"]
    # The skip path has its own input ring, sized for at least 64 time planes.
    if net["nt"] > 1:
        mapbits += net.get("skip_planes", 64) * maxexpanded * net["input_bits"]
    mapbram = (
        ceildiv(mapbits, 36864) * 2
    )  # aspect/port packing allowance, separate ROM geometry above
    # Separate producer staging has enough actual banks to supply V owned words
    # per cycle to the operand-copy stage. The two staging buffers may instead
    # be used as identical source-read replicas in serial-frame direct mode.
    source_v = fixed_v if feeder == "raster" else 1
    source_depth = max(
        (c[1] - c[0]) * ceildiv(c[3] - c[2], source_v) * net["nt"]
        for c in regions(net["n"], p)
    )
    source_mapbram = (
        2
        * source_v
        * sum(
            ceildiv(b, 72) for b in [net["input_bits"]] + [l["outbits"] for l in layers]
        )
        * ceildiv(source_depth, 512)
    )
    if net["nt"] > 1:
        source_mapbram += 2 * ceildiv(
            net.get("skip_planes", 64) * maxexpanded * net["input_bits"], 36864
        )
    mapbram = max(mapbram, source_mapbram)
    candidates = []
    limits = sorted(set(x["cycles_per_slice"] for ls in opts for x in ls))
    for target in limits:
        picked = []
        for ls in opts:
            feasible = [x for x in ls if x["cycles_per_slice"] <= target]
            if not feasible:
                break
            # Explicit search heuristic; not a globally optimal physical mapping.
            key = (
                lambda x: x["lut"] / budget["lut"]
                + x["ff"] / budget["ff"]
                + (x["weight_bram"] + x["operand_bram"]) / budget["bram"]
            )
            picked.append(min(feasible, key=key))
        if len(picked) != len(layers):
            continue
        used = {
            k: (
                math.ceil(max(x[k] for x in picked) * 1.10)
                if shared
                else sum(x[k] for x in picked)
            )
            for k in ["lut", "ff", "dsp"]
        }
        used["bram"] = (
            sum(x["weight_bram"] + x["operand_bram"] for x in picked) + mapbram
        )
        failed = [k for k in used if used[k] > budget[k]]
        SCREEN_AUDIT.append(
            dict(
                used=used,
                budget=budget,
                failed=failed,
                feeder=feeder,
                mapping=mapping,
                shared=shared,
                v=fixed_v,
            )
        )
        if failed:
            continue
        candidates.append(
            dict(
                mapping=mapping,
                architecture="shared_pool" if shared else "layer_pipeline",
                feeder=feeder,
                layers=picked,
                used=used,
                budget=budget,
                cells_per_slice=cells,
                feature_buffer_bits=mapbits,
                map_bram_allowance=mapbram,
                source_staging_bram=source_mapbram,
                slice_ii_cycles=max(x["cycles_per_slice"] for x in picked),
            )
        )
    # Keep a latency-sensitive shortlist as well as smallest II.
    candidates.sort(
        key=lambda c: (
            c["slice_ii_cycles"],
            sum(l["latency_cycles"] for l in c["layers"]),
        )
    )
    return candidates[:8]


def schedule(
    net,
    p,
    candidate,
    fmhz=250,
    physical_gbps=400,
    hop_us=0.05,
    arrival_us=None,
    nt=None,
):
    """Conservative slice-barrier timing, concurrent layer engines.

    Each cardinal direction is statically divided into L logical channels. Each
    exchange uses horizontal then vertical phases; phase payloads serialize, while
    the fixed hop latency pipelines across successive slices. Boundary time slices
    are padded globally. Whole local slices are ready before their next layer starts.
    """
    nt = net["nt"] if nt is None else nt
    layers = net["layers"]
    logical_gbps = physical_gbps / (
        1
        if candidate.get("architecture") in ["frame_serial_full_links", "shared_pool"]
        else len(layers)
    )
    geometries = [
        wire_geometry(net, p, b)
        for b in [net["input_bits"]] + [l["outbits"] for l in layers[:-1]]
    ]
    data = [0 if arrival_us is None else t * arrival_us for t in range(nt)]
    wire_iis = []
    wire_delays = []
    layer_finishes = []
    bank_checks = []
    for idx, (layer, geom) in enumerate(zip(candidate["layers"], geometries)):
        # A complete source-channel word is written per cycle to every read replica.
        # Throttle channel payload to this explicitly provisioned ingress bandwidth.
        link_service = (geom["max_horizontal_bits"] + geom["max_vertical_bits"]) / (
            logical_gbps * 1000 * 0.90
        )
        # Header processing does not write dummy feature-map sites. All incoming
        # cardinal directions share the one vector-write port of each replica.
        write_v = layer["V"] if candidate.get("feeder") == "raster" else 1
        banks = exchange_bank_loads(net, p, write_v)
        direct = candidate.get("dataflow") == "direct_write"
        if direct:
            assert (
                net["nt"] == 1
                and candidate["architecture"] == "frame_serial_full_links"
            )
            source_cycles = ceildiv(
                banks["source_halo_read_cycles"],
                candidate.get("source_read_replicas", 1),
            )
            receiver_cycles = banks["destination_halo_write_cycles"]
        else:
            source_cycles = banks["source_copy_and_halo_cycles"]
            receiver_cycles = banks["destination_copy_and_halo_cycles"]
        receiver_service = receiver_cycles / fmhz
        source_service = source_cycles / fmhz
        wireii = max(link_service, receiver_service, source_service)
        bank_checks.append(
            dict(
                source_cycles=source_cycles,
                destination_cycles=receiver_cycles,
                dataflow="direct_write" if direct else "staging_to_operand_copy",
                source_read_replicas=candidate.get("source_read_replicas", 1),
            )
        )
        delay = wireii + geom["max_hops"] * hop_us
        wire_iis.append(wireii)
        wire_delays.append(delay)
        halos = []
        lastlaunch = -1e99
        for ready in data:
            launch = max(ready, lastlaunch + wireii)
            halos.append(launch + delay)
            lastlaunch = launch
        outputs = []
        laststart = -1e99
        service = layer["cycles_per_slice"] / fmhz
        latency = layer["latency_cycles"] / fmhz
        for t in range(nt):
            deps = (
                halos[max(0, t - 1) : min(nt, t + 2)] if net["nt"] > 1 else [halos[t]]
            )
            start = max(max(deps), laststart + service)
            outputs.append(start + latency)
            laststart = start
        data = outputs
        layer_finishes.append(outputs)
    ii = max([x["cycles_per_slice"] / fmhz for x in candidate["layers"]] + wire_iis)
    if candidate.get("architecture") == "frame_serial_full_links":
        assert net["nt"] == 1
        ii = max(data)  # serial complete frames reserve all bandwidth for their stage
    if candidate.get("architecture") == "shared_pool":
        # Explicit shared-engine event order: in iteration r process layer l at
        # time index r-l. Wait for all required neighbors/slices, then block for
        # output halo exchange. Full-width runtime arithmetic reused by each layer.
        inputdata = [0 if arrival_us is None else t * arrival_us for t in range(nt)]
        ready = [[-1.0] * nt for _ in candidate["layers"]]
        pool = 0.0
        for r in range(nt + len(layers) - 1):
            for l, params in enumerate(candidate["layers"]):
                t = r - l
                if t < 0 or t >= nt:
                    continue
                previous = inputdata if l == 0 else ready[l - 1]
                needed = (
                    previous[max(0, t - 1) : min(nt, t + 2)]
                    if net["nt"] > 1
                    else [previous[t]]
                )
                assert min(needed) >= 0, "Invalid shared-pool future dependency"
                pool = (
                    max(pool, max(needed))
                    + wire_delays[l]
                    + params["latency_cycles"] / fmhz
                )
                ready[l][t] = pool
        data = ready[-1]
        layer_finishes = ready
        ii = sum(x["latency_cycles"] / fmhz for x in candidate["layers"]) + sum(
            wire_delays
        )
    return dict(
        full_pass_us=max(data),
        steady_ii_us=ii,
        wire_ii_us=max(wire_iis),
        layer_wire_delays_us=wire_delays,
        logical_link_gbps=logical_gbps,
        last_layer_ready_us=data,
        layer_outputs=layer_finishes,
        wire_geometry=geometries,
        exchange_bank_checks=bank_checks,
    )


def live_plane_check(
    stream, cadence, nt=64, map_planes_allocated=18, skip_planes_allocated=64
):
    """Conservative whole-plane live ranges: retain through consumer completion.

    Input/hidden maps are consumed by the next layer at t-1,t,t+1; the original
    input skip survives through the last layer at those same indices. This is a
    temporal occupancy check, not physical FIFO/backpressure RTL verification.
    """
    outputs = stream["layer_outputs"]
    produced = [[t * cadence for t in range(nt)]] + outputs[:-1]
    counts = []

    def peak(creation, consumers):
        events = []
        for t, when in enumerate(creation):
            end = max(consumers[max(0, t - 1) : min(nt, t + 2)])
            events += [(when, 1), (end, -1)]
        total = largest = 0
        # Allocation before release on an equal timestamp is conservative.
        for _, change in sorted(events, key=lambda e: (e[0], -e[1])):
            total += change
            largest = max(largest, total)
        return largest

    for i, creation in enumerate(produced):
        counts.append(peak(creation, outputs[i]))
    skip = peak(produced[0], outputs[-1])
    return dict(
        input_and_hidden_map_live_planes=counts,
        map_planes_allocated=map_planes_allocated,
        skip_live_planes=skip,
        skip_planes_allocated=skip_planes_allocated,
        pass_check=max(counts) <= map_planes_allocated
        and skip <= skip_planes_allocated,
        scope=f"{nt}-round whole-plane lifetimes; release at consumer completion",
    )


def architecture_label(candidate):
    feeder = (
        "raster line buffers"
        if candidate["feeder"] == "raster"
        else "replicated RAM windows"
    )
    if candidate["architecture"] == "shared_pool":
        return "Shared arithmetic pool + " + feeder
    engines = {
        "hardwired_constants": "Fixed-weight layer kernels",
        "lut_only": "Dedicated LUT layer engines",
        "native_dot": "Dedicated DSP58 dot-product/LUT engines",
    }.get(candidate["mapping"], "Dedicated DSP/LUT layer engines")
    tail = (
        "; serial complete frames"
        if candidate["architecture"] == "frame_serial_full_links"
        else "; overlapped layer pipeline"
    )
    if candidate.get("dataflow") == "direct_write":
        tail += (
            f"; direct writes/R{candidate.get('source_read_replicas',1)} source reads"
        )
    return engines + " + " + feeder + tail


def load_fixed_kernels():
    """Load the three separately synthesized, fixed-weight layer anchors.

    Missing anchors are an error: silently dropping this family would change
    the architecture search and could make its result look unnecessarily slow.
    """
    path = PROFILE_ROOT / "hardwired2d_resources.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 3 or {row["logical_layer"] for row in rows} != {"1", "2", "3"}:
        raise ValueError("Expected one fixed-weight anchor for each of three layers")
    return [
        dict(
            lut=int(row["lut_sites"]),
            ff=int(row["fabric_ff"]),
            dsp=int(row["dsp_blocks"]),
            latency_cycles=int(row["sample_to_output_cycles"]),
            source=row,
        )
        for row in sorted(rows, key=lambda row: int(row["logical_layer"]))
    ]


DEVICE_DATA = {
    "VU9P": dict(
        lut=1182240,
        ff=2364480,
        dsp=6840,
        bram=2160,
        uram=960,
        gt25=120,
        family="DSP48E2",
        transceiver="GTY",
        package="FLGA2577",
    ),
    "VU13P": dict(
        lut=1728000,
        ff=3456000,
        dsp=12288,
        bram=2688,
        uram=1280,
        gt25=128,
        family="DSP48E2",
        transceiver="GTY",
        package="FLGA2577 or FSGA2577",
    ),
    "VU19P": dict(
        lut=4085760,
        ff=8171520,
        dsp=3840,
        bram=2160,
        uram=320,
        gt25=80,
        family="DSP48E2",
        transceiver="GTY",
        package="FSVB3824",
    ),
    "VP1802": dict(
        lut=3360896,
        ff=6721792,
        dsp=14352,
        bram=4941,
        uram=2549,
        gt25=140,
        family="DSP58",
        transceiver="GTM NRZ25",
        package="LSVC4072 or VSVA5601",
    ),
    "VP1902": dict(
        lut=8460288,
        ff=16920576,
        dsp=6864,
        bram=6808,
        uram=2200,
        gt25=128,
        family="DSP58",
        transceiver="GTYP",
        package="VSVB6865",
    ),
}


GRIDS = {
    "2D": [5, 9, 13, 17, 21, 25, 33, 41, 49, 65, 97],
    "3D": [5, 9, 13, 17, 21, 25, 33, 49, 65],
}


BUDGETS = {
    "nominal": dict(lut=0.60, ff=0.60, dsp=0.75, bram=0.70),
    "conservative": dict(lut=0.45, ff=0.45, dsp=0.60, bram=0.55),
    "permissive": dict(lut=0.75, ff=0.75, dsp=0.85, bram=0.85),
}


def make_net(model, d):
    """Set the spatial map and finite time window for a code distance."""
    n = copy.deepcopy(NETS[model])
    n.update(
        d=d,
        R=d if model == "3D" else None,
        n=(d + 3) // 2 if model == "3D" else 2 * d - 1,
        nt=d + 1 if model == "3D" else 1,
    )
    n["skip_planes"] = max(64, n["nt"])
    for l in n["layers"]:
        if l.get("skip"):
            l["skip_planes"] = n["skip_planes"]
    return n


def candidates(model, net, dev, p):
    """Combine both tap grids, then remove duplicate retained allocations.

    The coarse grid is kept because changing the area tie-break choices in a
    finer grid can otherwise discard an allocation that was previously useful.
    Calls are sequential: this function resets the module's screening counters.
    """
    if p > net["n"]:
        return [], {"reason": "Empty ownership partitions excluded"}
    SCREEN_AUDIT.clear()
    cs = []
    mappings = ["lut_only", "work_share", "wide_first"] + (
        ["native_dot"] if model == "3D" and dev["family"] == "DSP58" else []
    )
    for shared in [False, True]:
        for mode in mappings:
            if shared and mode == "native_dot":
                continue
            for coarse in [True, False]:
                cs += screen(net, dev, p, mode, shared, coarse_grid=coarse)
                for v in [1, 2, 4, 8, 16]:
                    if v <= ceildiv(net["n"], p) ** 2:
                        cs += screen(
                            net, dev, p, mode, shared, "raster", v, coarse_grid=coarse
                        )
    if model == "2D":
        for v in [1, 2, 3, 4, 6, 8, 12, 16, 24, 32]:
            if v <= ceildiv(net["n"], p) ** 2:
                cs += screen(
                    net,
                    dev,
                    p,
                    "hardwired_constants",
                    False,
                    "raster",
                    v,
                    load_fixed_kernels(),
                )
        cs += [
            dict(c, architecture="frame_serial_full_links")
            for c in cs
            if c["architecture"] == "layer_pipeline"
        ]
        cs += [
            dict(c, dataflow="direct_write", source_read_replicas=r)
            for c in list(cs)
            if c["architecture"] == "frame_serial_full_links"
            and c["feeder"] == "raster"
            for r in [1, 2]
        ]
    cs = list({json.dumps(c, sort_keys=True): c for c in cs}.values())
    reasons = collections.Counter(k for a in SCREEN_AUDIT for k in a["failed"])
    audit = {
        "screen_trials": len(SCREEN_AUDIT),
        "rejection_resource_counts": dict(reasons),
        "retained_unique": len(cs),
    }
    SCREEN_AUDIT.clear()
    return cs, audit


def compact(s):
    return {
        k: v for k, v in s.items() if k not in ["layer_outputs", "last_layer_ready_us"]
    }


def io_budget(net, p, finite_us, ii):
    """Add ideal packed transfers at one central application endpoint.

    p is retained in the interface to make clear that the endpoint is shared
    by the entire mesh. Its bandwidth does not multiply with the board count.
    """
    inp = net["n"] ** 2 * net["nt"] * net["input_bits"]
    out = net["n"] ** 2 * net["nt"] * net["layers"][-1]["outbits"]
    return [
        dict(
            endpoint_gbps=rate,
            input_bits=inp,
            output_bits=out,
            load_us=inp / (rate * 1000),
            collect_us=out / (rate * 1000),
            sequential_finite_subtotal_us=finite_us + (inp + out) / (rate * 1000),
            ideal_overlapped_service_lower_bound_us=max(
                ii, inp / net["nt"] / (rate * 1000), out / net["nt"] / (rate * 1000)
            ),
            scope="Ideal packed application bits. Sequential load/compute/collect subtotal. Service lower bound is not proven continuous admission; excludes protocol, cleanup and controller.",
        )
        for rate in [32, 128, 400]
    ]


def explore(
    model="2D",
    distance=9,
    device="VP1902",
    boards=1,
    resource_budget="nominal",
    clock_mhz=250,
    link_gbps=400,
    hop_ns=50,
    mesh_budget_gbps=None,
    stream_rounds=0,
    arrival_us=1.1,
):
    """Select finite-window and steady-service winners from the enumerated family.

    Resource fractions are per FPGA. A square mesh partitions the feature map,
    including uneven edge tiles. The two winners can be different allocations,
    so each result carries its own schedule and resource counts.
    """
    model = model.upper()
    if model not in NETS:
        raise ValueError("model must be 2D or 3D")
    if not isinstance(distance, int) or distance < 3 or distance % 2 != 1:
        raise ValueError("distance must be an odd integer of at least 3")
    if device not in DEVICE_DATA:
        raise ValueError(f"Unknown device: {device}")
    if resource_budget not in BUDGETS:
        raise ValueError(f"Unknown resource budget: {resource_budget}")
    if not isinstance(boards, int) or boards < 1:
        raise ValueError("boards must be a positive square integer")
    mesh_side = math.isqrt(boards)
    if mesh_side * mesh_side != boards:
        raise ValueError("boards must form a square mesh, such as 1, 4, 16 or 64")
    for name, value in (
        ("clock_mhz", clock_mhz),
        ("link_gbps", link_gbps),
        ("arrival_us", arrival_us),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not math.isfinite(hop_ns) or hop_ns < 0:
        raise ValueError("hop_ns must be finite and nonnegative")
    if mesh_budget_gbps is not None and (
        not math.isfinite(mesh_budget_gbps) or mesh_budget_gbps <= 0
    ):
        raise ValueError("mesh_budget_gbps must be finite and positive")
    if not isinstance(stream_rounds, int) or stream_rounds < 0:
        raise ValueError("stream_rounds must be a nonnegative integer")
    if stream_rounds and (model != "3D" or stream_rounds < 16):
        raise ValueError("Stream checks require a 3D model and at least 16 rounds")

    net = make_net(model, distance)
    if mesh_side > net["n"]:
        raise ValueError("The mesh has more tiles per side than feature-map sites")
    dev = dict(DEVICE_DATA[device], budget_fractions=BUDGETS[resource_budget])
    directed_links = 4 * mesh_side * (mesh_side - 1)
    effective_link = link_gbps
    if directed_links and mesh_budget_gbps is not None:
        effective_link = min(link_gbps, mesh_budget_gbps / directed_links)
    physical_ports = 4 if mesh_side >= 3 else 2 if mesh_side == 2 else 0
    transceiver_lanes = physical_ports * math.ceil(effective_link / 25)
    if transceiver_lanes > dev["gt25"]:
        raise ValueError("Requested links exceed the device's 25 Gbit/s lane allowance")

    allocations, audit = candidates(model, net, dev, mesh_side)
    result = dict(
        model=model,
        distance=distance,
        rounds=net["R"],
        feature_map_side=net["n"],
        time_planes=net["nt"],
        skip_planes=net["skip_planes"],
        device=device,
        boards=boards,
        resource_budget=resource_budget,
        frequency_mhz=clock_mhz,
        direction_gbps=effective_link,
        hop_ns=hop_ns,
        directed_mesh_links=directed_links,
        total_installed_directed_gbps=directed_links * effective_link,
        transceiver_lanes_per_board=transceiver_lanes,
        audit=audit,
        scope="Conditional resource and event model; not synthesized or routed board timing",
        cost_status=COST_STATUS,
        winners={},
    )
    if not allocations:
        result["status"] = "no_enumerated_resource_feasible_candidate"
        return result

    schedules = [
        schedule(net, mesh_side, allocation, clock_mhz, effective_link, hop_ns / 1000)
        for allocation in allocations
    ]
    for objective, metric in (("finite", "full_pass_us"), ("service", "steady_ii_us")):
        index = min(range(len(allocations)), key=lambda i: schedules[i][metric])
        allocation, timing = allocations[index], schedules[index]
        winner = dict(
            architecture=architecture_label(allocation),
            allocation=allocation,
            schedule=compact(timing),
            io=io_budget(
                net, mesh_side, timing["full_pass_us"], timing["steady_ii_us"]
            ),
        )
        if stream_rounds:
            stream = schedule(
                net,
                mesh_side,
                allocation,
                clock_mhz,
                effective_link,
                hop_ns / 1000,
                arrival_us=arrival_us,
                nt=stream_rounds,
            )
            live = live_plane_check(
                stream, arrival_us, stream_rounds, net["nt"], net["skip_planes"]
            )
            delays = [
                ready - t * arrival_us
                for t, ready in enumerate(stream["last_layer_ready_us"])
            ]
            # Leave the final temporal boundary out of the backlog measurement.
            midpoint, endpoint = stream_rounds // 2, stream_rounds - 6
            predicted_growth = (endpoint - midpoint) * max(
                0, timing["steady_ii_us"] - arrival_us
            )
            measured_growth = delays[endpoint] - delays[midpoint]
            service_fits = timing["steady_ii_us"] <= arrival_us + 1e-12
            winner["stream_check"] = dict(
                arrival_us=arrival_us,
                rounds=stream_rounds,
                service_le_arrival=service_fits,
                bounded_on_test_horizon=service_fits and live["pass_check"],
                live_planes=live,
                measured_delay_growth_us=measured_growth,
                predicted_delay_growth_us=predicted_growth,
                scope="Whole-plane lifetimes with abstract queues; not physical FIFO verification",
            )
        result["winners"][objective] = winner
    result["status"] = "evaluated"
    return result
