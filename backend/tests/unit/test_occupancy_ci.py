"""Occupancy CI: from the observed Hessian, not BFGS's approximation."""
import numpy as np

from occupancy_model import fit_occu

# Naive wild-boar histories of the Doñana worked example (58 sites x 6
# occasions; '.' = camera not operating). BFGS's hess_inv gave a 95 % CI of
# 0.76-0.80 here; the observed-information CI (as in unmarked) is 0.66-0.87.
WILD_BOAR = """
111111
110011
000000
111011
001010
000001
000000
000001
010001
00000.
111000
111111
000000
000110
011110
000000
010011
111011
000000
111111
010011
000110
111111
001111
100000
000000
111111
111000
101011
100000
000000
000100
100100
000000
000000
110000
000010
110000
000111
111011
111111
111111
100000
111111
111111
000000
010000
101111
000000
000001
000000
011001
100000
000111
111111
111011
100010
110000
"""


def _matrix(text):
    return np.array([[np.nan if c == "." else float(c) for c in line] for line in text.split()])


def test_ci_from_observed_information():
    fit = fit_occu(_matrix(WILD_BOAR))
    assert round(fit["psi"], 2) == 0.78
    assert round(fit["psi_lo"], 2) == 0.66
    assert round(fit["psi_hi"], 2) == 0.87
    assert round(fit["p"], 2) == 0.55
