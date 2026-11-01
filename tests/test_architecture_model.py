"""Geometry and regression checks for the conditional architecture model."""

import unittest

from qec.architecture import (
    area,
    exchange_bank_loads,
    explore,
    load_fixed_kernels,
    make_net,
    regions,
    wire_geometry,
)


class GeometryTests(unittest.TestCase):
    def test_uneven_tiles_cover_map_once(self):
        tiles = regions(9, 2)
        self.assertEqual([area(tile) for tile in tiles], [16, 20, 20, 25])
        sites = [
            (y, x)
            for y0, y1, x0, x1 in tiles
            for y in range(y0, y1)
            for x in range(x0, x1)
        ]
        self.assertEqual(len(sites), 81)
        self.assertEqual(len(set(sites)), 81)

    def test_small_mesh_counts_headers_and_corner_hops(self):
        net = dict(n=4, r=1)
        geometry = wire_geometry(net, 2, 8)
        # Eight edge requests carry two sites each; four diagonal requests
        # carry one site each and traverse two links.
        expected_bits = 8 * (2 * 8 + 128) + 4 * 2 * (8 + 128)
        self.assertEqual(geometry["total_link_bits"], expected_bits)
        self.assertEqual(geometry["max_hops"], 2)
        self.assertEqual(geometry["directed_links"], 8)
        banks = exchange_bank_loads(net, 2, 1)
        self.assertEqual(banks["source_halo_read_cycles"], 5)
        self.assertEqual(banks["destination_halo_write_cycles"], 5)
        self.assertEqual(banks["source_copy_and_halo_cycles"], 9)

    def test_fixed_weight_anchors_and_window_geometry(self):
        anchors = load_fixed_kernels()
        self.assertEqual([row["lut"] for row in anchors], [23060, 152450, 86406])
        self.assertEqual([row["latency_cycles"] for row in anchors], [11, 13, 13])
        net = make_net("3D", 65)
        self.assertEqual((net["n"], net["nt"], net["skip_planes"]), (34, 66, 66))


class ModelRegressionTests(unittest.TestCase):
    def test_accepted_small_and_distance_33_cases(self):
        # These values are from the accepted thesis model results, before the
        # public module was extracted. They are estimates, not board timings.
        cases = [
            ("2D", 9, 1, 0.484, 0.108),
            ("2D", 33, 4, 2.5929222222222226, 0.608),
            ("3D", 5, 1, 14.632000000000001, 1.152),
            ("3D", 33, 64, 29.670933333333323, 0.712),
        ]
        for model, distance, boards, finite, service in cases:
            with self.subTest(model=model, distance=distance, boards=boards):
                result = explore(model, distance, "VP1902", boards)
                self.assertEqual(result["status"], "evaluated")
                self.assertAlmostEqual(
                    result["winners"]["finite"]["schedule"]["full_pass_us"],
                    finite,
                    places=10,
                )
                self.assertAlmostEqual(
                    result["winners"]["service"]["schedule"]["steady_ii_us"],
                    service,
                    places=10,
                )
                for winner in result["winners"].values():
                    allocation = winner["allocation"]
                    for resource, used in allocation["used"].items():
                        self.assertLessEqual(used, allocation["budget"][resource])

    def test_fixed_mesh_bandwidth_and_stream_occupancy(self):
        mesh = explore("2D", 33, "VP1902", 64, mesh_budget_gbps=3200)
        self.assertAlmostEqual(mesh["direction_gbps"], 3200 / 224)
        self.assertAlmostEqual(
            mesh["winners"]["finite"]["schedule"]["full_pass_us"], 4.9552, places=10
        )
        stream = explore("3D", 33, "VP1902", 64, stream_rounds=512)
        for winner in stream["winners"].values():
            check = winner["stream_check"]
            self.assertTrue(check["bounded_on_test_horizon"])
            self.assertAlmostEqual(check["measured_delay_growth_us"], 0, places=8)

    def test_invalid_requests_fail_before_search(self):
        invalid = [
            dict(distance=4),
            dict(boards=3),
            dict(boards=0),
            dict(clock_mhz=0),
            dict(link_gbps=float("nan")),
            dict(hop_ns=-1),
            dict(mesh_budget_gbps=0),
            dict(device="unknown"),
            dict(stream_rounds=512),
            dict(model="3D", distance=3, boards=64),
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                explore(**arguments)


if __name__ == "__main__":
    unittest.main()
