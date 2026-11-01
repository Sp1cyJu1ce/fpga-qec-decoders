"""Input sampler for the separate exploratory magnitude-pruning experiment.

The defect geometry differs from the unpunctured patches in the main decoder
comparison. Restricting this helper to the studied geometry avoids exposing
unfinished notebook branches for other defect arrangements.
"""

import numpy as np


def x_stab_locs(d, padding=False):
    """X stabilizer coordinates (odd rows, even columns): they flag adjacent Z errors."""
    r_vals = np.arange(1, 2 * d - 1, 2, dtype="int") + padding
    c_vals = np.arange(0, 2 * d - 1, 2, dtype="int") + padding
    return np.meshgrid(r_vals, c_vals)


def z_stab_locs(d, padding=False):
    """Z stabilizer coordinates (even rows, odd columns): they flag adjacent X errors."""
    r_vals = np.arange(0, 2 * d - 1, 2, dtype="int") + padding
    c_vals = np.arange(1, 2 * d - 1, 2, dtype="int") + padding
    return np.meshgrid(r_vals, c_vals)


def data_qubit_locs(d, padding=False):
    """Data qubit coordinates: the checkerboard the stabilizers sit between."""
    return np.array(
        [
            (r + padding, c + padding + r % 2)
            for r in range(0, 2 * d - 1)
            for c in range(0, 2 * d - 1 - r % 2, 2)
        ]
    ).T


def defect_error_boards(
    error_rate, n_codes, d, dr, dc, defect_type="Z-cut", n_defects=1, seed=2026
):
    """
    Create randomly generated error boards with a single pair of defect qubits

    The main idea is to first generate errors for a board with no defects and then introduce
    the defects by chopping out parts of the boards

    The distance of the defects is defect_dist = (dr+dc)*2
    So they have to be defect_dist data qubits away from a boundary and defect_dist data
    qubits away from each other

    # edge stabilizers only have weight on 3 qubits
    # edge data qubits only touch one of a type of stabilizer
    # this can be used to define where they are located.

    This experiment uses a pair of bulk Z-cut defects, away from the boundaries.
    """
    if (d, dr, dc) != (33, 3, 3) or defect_type != "Z-cut" or n_defects != 1:
        raise ValueError(
            "This sampler supports the studied distance-33 pair of 3x3 Z-cut defects only."
        )
    if n_codes < 1 or not 0 <= error_rate <= 1:
        raise ValueError("Positive sample count and probability in [0,1] required.")
    rng = np.random.RandomState(seed)
    defect_dist = (dr + dc) * 2
    x_stab_r, x_stab_c = x_stab_locs(d)
    z_stab_r, z_stab_c = z_stab_locs(d)
    errors = (rng.rand(n_codes, 2 * d - 1, 2 * d - 1) < error_rate) * 1
    error_types = rng.randint(1, 4, size=[n_codes, 2 * d - 1, 2 * d - 1])
    errors = errors * error_types
    defect_cols_s = (
        2 * rng.randint(low=defect_dist, high=d + 1 - defect_dist - dc, size=n_codes)
        - 1
    )
    defect_rows_s = 2 * rng.randint(low=1, high=d - (dr + 1) + 1, size=n_codes)
    defect_cols_e = defect_cols_s + 2 * (dc - 1) + 1
    defect_rows_e = defect_rows_s + 2 * (dr - 1) + 1
    defect_locations1 = (
        np.column_stack((defect_rows_s, defect_rows_e, defect_cols_s, defect_cols_e))
        .reshape([n_codes, 1, 4])
        .tolist()
    )
    for i in range(n_codes):
        valid_found = False
        while not valid_found:
            disp_c = rng.randint(-(defect_dist + dc - 1), defect_dist + dc - 1 + 1)
            if np.abs(disp_c) == defect_dist + dc - 1:
                disp_r = 0
            elif np.abs(disp_c) - dc + 1 > 0:
                disp_r = (defect_dist - (np.abs(disp_c) - dc + 1) + (dr - 1)) * (
                    2 * rng.randint(2) - 1
                )
            else:
                disp_r = (defect_dist + dr - 1) * (2 * rng.randint(2) - 1)
            disp_r, disp_c = (disp_r * 2, disp_c * 2)
            defect_rows_s, defect_rows_e, defect_cols_s, defect_cols_e = (
                defect_locations1[i][0]
            )
            if 2 * defect_dist - 1 <= defect_cols_s + disp_c < 2 * (
                d + 1 - defect_dist - dc
            ) - 1 and 2 * 1 <= defect_rows_s + disp_r < 2 * (d - (dr + 1) + 1):
                valid_found = True
                defect_rows_s, defect_rows_e, defect_cols_s, defect_cols_e = (
                    defect_locations1[i][0]
                )
                defect_locations1[i].append(
                    [
                        defect_rows_s + disp_r,
                        defect_rows_e + disp_r,
                        defect_cols_s + disp_c,
                        defect_cols_e + disp_c,
                    ]
                )
    for i in range(n_codes):
        for (
            defect_rows_s,
            defect_rows_e,
            defect_cols_s,
            defect_cols_e,
        ) in defect_locations1[i]:
            errors[i, defect_rows_s:defect_rows_e, defect_cols_s:defect_cols_e] = 0
    errors[:, x_stab_r, x_stab_c] = 0
    errors[:, z_stab_r, z_stab_c] = 0
    errors = np.pad(errors, 1)[1:-1]
    xz_errors = np.zeros([n_codes, 2 * d + 1, 2 * d + 1, 2])
    xz_errors[:, :, :, 0] = ((errors[:, :, :] - 1.5) ** 2 <= 1) * 1
    xz_errors[:, :, :, 1] = (errors[:, :, :] >= 2) * 1
    net_input = np.zeros([n_codes, 2 * d + 1, 2 * d + 1, 4])
    net_input[:, 2:-2:2, 1:-1:2, 0] = (
        xz_errors[:, 1:-2:2, 1:-1:2, 1]
        + xz_errors[:, 3:-1:2, 1:-1:2, 1]
        + xz_errors[:, 2:-2:2, 1 - 1 : -1 : 2, 1]
        + xz_errors[:, 2:-2:2, 1 + 1 :: 2, 1]
    ) % 2
    net_input[:, 1:-1:2, 2:-2:2, 1] = (
        xz_errors[:, 0:-1:2, 2:-2:2, 0]
        + xz_errors[:, 2::2, 2:-2:2, 0]
        + xz_errors[:, 1:-1:2, 1:-2:2, 0]
        + xz_errors[:, 1:-1:2, 3:-1:2, 0]
    ) % 2
    for i in range(n_codes):
        for (
            defect_rows_s,
            defect_rows_e,
            defect_cols_s,
            defect_cols_e,
        ) in defect_locations1[i]:
            net_input[
                i,
                defect_rows_s + 1 : defect_rows_e + 1,
                defect_cols_s + 1 : defect_cols_e + 1,
                :,
            ] = 0
    net_input[:, 1, 1:-1:2, 2] = 1
    net_input[:, -2, 1:-1:2, 2] = 1
    net_input[:, 1:-1:2, 1, 3] = 1
    net_input[:, 1:-1:2, -2, 3] = 1
    net_input = net_input[:, 1:-1, 1:-1, :]
    xz_errors = xz_errors[:, 1:-1, 1:-1]
    for i in range(n_codes):
        for (
            defect_rows_s,
            defect_rows_e,
            defect_cols_s,
            defect_cols_e,
        ) in defect_locations1[i]:
            net_input[i, defect_rows_s:defect_rows_e:2, defect_cols_s - 1, 3] = 1
            net_input[i, defect_rows_s:defect_rows_e:2, defect_cols_e, 3] = 1
            net_input[i, defect_rows_s - 1, defect_cols_s:defect_cols_e:2, 3] = 1
            net_input[i, defect_rows_e, defect_cols_s:defect_cols_e:2, 3] = 1
    return (net_input, xz_errors, defect_locations1)
