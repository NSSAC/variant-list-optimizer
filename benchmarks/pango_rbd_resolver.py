"""Build and verify Pango lineage -> Spike RBD (sites 331-531) amino acid mutation map across all lineages."""

import json
import re
import os
from pathlib import Path
import pandas as pd

BENCHMARKS_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("VLOPT_DATA_DIR", str(BENCHMARKS_DIR / "data")))
TRACK1_DIR = DATA_DIR / "track1"

# Canonical basal clade RBD mutations (relative to Wuhan-Hu-1, sites 331..531 -> mutant AA)
BASAL_CLADE_RBD_MUTS: dict[str, dict[int, str]] = {
    "B": {},
    "A": {},
    "B.1": {},
    # Alpha
    "B.1.1.7": {501: "Y"},
    "Q.1": {501: "Y"},
    # Beta
    "B.1.351": {417: "N", 484: "K", 501: "Y"},
    # Gamma
    "P.1": {417: "T", 484: "K", 501: "Y"},
    # Epsilon
    "B.1.427": {452: "R"},
    "B.1.429": {452: "R"},
    # Eta / Iota / Kappa / Mu / Lambda
    "B.1.525": {484: "K"},
    "B.1.526": {484: "K"},
    "B.1.617.1": {452: "R", 484: "Q"},
    "B.1.621": {346: "K", 484: "K", 501: "Y"},
    "C.37": {452: "Q", 490: "S"},
    # Delta (B.1.617.2 and AY.*)
    "B.1.617.2": {452: "R", 478: "K"},
    "AY.1": {417: "N", 452: "R", 478: "K"},
    "AY.2": {417: "N", 452: "R", 478: "K"},
    "AY.3": {452: "R", 478: "K"},
    "AY.4": {452: "R", 478: "K"},
    "AY.4.2": {452: "R", 478: "K"},
    # Omicron basal & BA.1
    "B.1.1.529": {
        339: "D", 371: "L", 373: "P", 375: "F", 417: "N", 440: "K",
        477: "N", 478: "K", 484: "A", 493: "R", 498: "R", 501: "Y", 505: "H"
    },
    "BA.1": {
        339: "D", 371: "L", 373: "P", 375: "F", 417: "N", 440: "K", 446: "S",
        477: "N", 478: "K", 484: "A", 493: "R", 496: "S", 498: "R", 501: "Y", 505: "H"
    },
    "BA.1.1": {
        339: "D", 346: "K", 371: "L", 373: "P", 375: "F", 417: "N", 440: "K", 446: "S",
        477: "N", 478: "K", 484: "A", 493: "R", 496: "S", 498: "R", 501: "Y", 505: "H"
    },
    # Omicron BA.2
    "BA.2": {
        339: "D", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 477: "N", 478: "K", 484: "A", 493: "R", 498: "R", 501: "Y", 505: "H"
    },
    "BA.2.12.1": {
        339: "D", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 452: "Q", 477: "N", 478: "K", 484: "A", 493: "R", 498: "R", 501: "Y", 505: "H"
    },
    "BA.2.75": {
        339: "H", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 498: "R", 501: "Y", 505: "H"
    },
    "BA.2.75.2": {
        339: "H", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "S", 498: "R", 501: "Y", 505: "H"
    },
    "BM.1.1": {
        339: "H", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "S", 498: "R", 501: "Y", 505: "H"
    },
    "BN.1": {
        339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "BR.2.1": {
        339: "H", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 446: "I", 460: "K", 477: "N", 478: "K", 484: "A", 486: "I", 498: "R", 501: "Y", 505: "H"
    },
    "CH.1.1": {
        339: "H", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "T", 446: "S", 452: "R", 460: "K", 477: "N", 478: "K", 484: "A", 486: "S", 498: "R", 501: "Y", 505: "H"
    },
    # Omicron BA.4 / BA.5 / BQ.1
    "BA.4": {
        339: "D", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 452: "R", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BA.4.6": {
        339: "D", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 452: "R", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BA.5": {
        339: "D", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 452: "R", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BF.7": {
        339: "D", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 452: "R", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BF.11": {
        339: "D", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 452: "R", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BQ.1": {
        339: "D", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "T", 452: "R", 460: "K", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BQ.1.1": {
        339: "D", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "T", 452: "R", 460: "K", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    "BQ.1.1.18": {
        339: "D", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "T", 452: "R", 460: "K", 477: "N", 478: "K", 484: "A", 486: "V", 498: "R", 501: "Y", 505: "H"
    },
    # XBB & descendants
    "XBB": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "S", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBB.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "S", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBB.1.5": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBB.1.16": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "R", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBB.1.9.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBB.1.9.2": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBB.2.3": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H", 521: "S"
    },
    "XBF": {
        339: "H", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 446: "S", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "XBC.1.6": {
        339: "D", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "T", 452: "R", 477: "N", 478: "K", 484: "A", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "FD.1.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "FE.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 455: "L", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "EG.5": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "EG.5.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "HK.3": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 455: "F", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "HV.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 452: "R", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "JD.1.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 455: "F", 456: "L", 460: "K", 475: "V", 477: "N", 478: "K", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "FL.1.5.1": {
        339: "H", 346: "T", 368: "I", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "P", 446: "S", 456: "L", 460: "K", 477: "N", 478: "R", 484: "A", 486: "P", 490: "S", 498: "R", 501: "Y", 505: "H"
    },
    "DV.7.1": {
        339: "H", 346: "T", 371: "F", 373: "P", 375: "F", 376: "A", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "T", 446: "S", 452: "W", 455: "F", 456: "L", 460: "K", 477: "N", 478: "K", 484: "A", 486: "S", 498: "R", 501: "Y", 505: "H"
    },
    # BA.2.86 & JN.1 & descendants
    "BA.2.86": {
        332: "V", 339: "H", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "JN.1": {
        332: "V", 339: "H", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "JN.1.11.1": {
        332: "V", 339: "H", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "KP.1.1": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "KP.2": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "KP.2.3": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "KP.3": {
        332: "V", 339: "H", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 493: "E", 498: "R", 501: "Y", 505: "H"
    },
    "KP.3.1.1": {
        332: "V", 339: "H", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 493: "E", 498: "R", 501: "Y", 505: "H"
    },
    "LB.1": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "XEC": {
        332: "V", 339: "H", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 493: "E", 498: "R", 501: "Y", 505: "H"
    },
    "LP.8.1": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 445: "R", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 493: "E", 498: "R", 501: "Y", 505: "H"
    },
    "LF.7": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "R", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 475: "V", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 498: "R", 501: "Y", 505: "H"
    },
    "NB.1.8.1": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 435: "S", 440: "K", 445: "H", 446: "S", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "I", 481: "K", 483: "del", 484: "K", 486: "P", 493: "E", 498: "R", 501: "Y", 505: "H"
    },
    "XFG": {
        332: "V", 339: "H", 346: "T", 356: "T", 371: "F", 373: "P", 375: "F", 376: "A", 403: "K", 405: "N", 408: "S",
        417: "N", 440: "K", 444: "R", 445: "R", 446: "D", 450: "D", 452: "W", 455: "S", 456: "L", 460: "K", 477: "N", 478: "K", 481: "K", 483: "del", 484: "K", 486: "P", 487: "D", 493: "E", 498: "R", 501: "Y", 505: "H"
    },
}

WUHAN_RBD_WT = {
    332: "I", 339: "G", 346: "R", 356: "K", 368: "L", 371: "S", 373: "S", 375: "S", 376: "T",
    403: "R", 405: "D", 408: "R", 417: "K", 435: "A", 440: "N", 441: "L", 444: "K", 445: "V",
    446: "G", 450: "N", 452: "L", 453: "Y", 455: "L", 456: "F", 460: "N", 475: "A", 477: "S",
    478: "T", 481: "N", 483: "V", 484: "E", 486: "F", 490: "F", 493: "Q", 494: "S", 496: "G",
    498: "Q", 501: "N", 505: "Y", 521: "P"
}


class PangoRBDResolver:
    """Resolves any Pango lineage to its Spike RBD (331..531) amino acid mutation dictionary."""

    def __init__(self, lineage_notes_path: Path, alias_key_path: Path):
        with open(alias_key_path) as f:
            raw_alias = json.load(f)

        self.alias_map: dict[str, str] = {}
        self.unaliased_to_short: dict[str, str] = {}
        self.recomb_parents: dict[str, list[str]] = {}
        for k, v in raw_alias.items():
            if isinstance(v, list):
                if v:
                    self.recomb_parents[k] = [p.rstrip("*") for p in v if p]
            elif isinstance(v, str) and v:
                self.alias_map[k] = v
                self.unaliased_to_short[v] = k

        self.branch_muts: dict[str, dict[int, str]] = {}
        self._parse_lineage_notes(lineage_notes_path)
        self._cache: dict[str, dict[int, str]] = {}

    def _parse_lineage_notes(self, lineage_notes_path: Path):
        for line in lineage_notes_path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("Lineage\t") or line.startswith("*"):
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            lin, desc = parts[0].strip(), parts[1].strip()
            muts = {}
            for m in re.finditer(r"(?:S|Spike)\s*:\s*[A-Z]?(\d{3})([A-Z]|del|-)", desc):
                site = int(m.group(1))
                aa = m.group(2)
                if 331 <= site <= 531:
                    muts[site] = "del" if aa == "-" else aa
            for m in re.finditer(
                r"\b(?:R346|K356|R403|K417|K440|L441|K444|V445|P445|H445|G446|N450|L452|L455|F455|S455|F456|N460|A475|T478|K478|E484|A484|K484|F486|S486|P486|F490|Q493|R493|E493|Q498|N501)([A-Z])\b",
                desc,
            ):
                full = m.group(0)
                site = int(re.search(r"\d{3}", full).group(0))
                aa = m.group(1)
                if 331 <= site <= 531:
                    muts[site] = aa
            if muts:
                self.branch_muts[lin] = muts

    def realias(self, unaliased: str) -> str:
        # Find longest matching strict prefix in unaliased_to_short
        parts = unaliased.split(".")
        for i in range(len(parts) - 1, 0, -1):
            pref = ".".join(parts[:i])
            if pref in self.unaliased_to_short:
                short_pref = self.unaliased_to_short[pref]
                rest = ".".join(parts[i:])
                return f"{short_pref}.{rest}"
        return unaliased

    def get_parent(self, lin: str) -> str | None:
        if lin in ("A", "B", "root", ""):
            return None
        if "." in lin:
            parent_candidate = lin.rsplit(".", 1)[0]
            # If parent_candidate is a bare alias root (no dot, e.g. 'JN', 'AY', 'BA'), resolve it to its canonical lineage
            if "." not in parent_candidate and parent_candidate in self.alias_map:
                return self.realias(self.alias_map[parent_candidate])
            return parent_candidate
        if lin in self.alias_map and self.alias_map[lin]:
            unaliased = self.alias_map[lin]
            if "." in unaliased:
                parent_unaliased = unaliased.rsplit(".", 1)[0]
                return self.realias(parent_unaliased)
            return unaliased
        if lin in self.recomb_parents:
            parents = self.recomb_parents[lin]
            return self.realias(parents[-1])
        return "B"

    def resolve(self, lin: str) -> dict[int, str]:
        if lin in self._cache:
            return self._cache[lin]

        if lin in BASAL_CLADE_RBD_MUTS:
            base = dict(BASAL_CLADE_RBD_MUTS[lin])
            if lin in self.branch_muts:
                for site, aa in self.branch_muts[lin].items():
                    if WUHAN_RBD_WT.get(site) == aa:
                        base.pop(site, None)
                    else:
                        base[site] = aa
            self._cache[lin] = base
            return base

        short_lin = self.unaliased_to_short.get(lin, lin)
        if short_lin in BASAL_CLADE_RBD_MUTS:
            base = dict(BASAL_CLADE_RBD_MUTS[short_lin])
            if short_lin in self.branch_muts:
                for site, aa in self.branch_muts[short_lin].items():
                    if WUHAN_RBD_WT.get(site) == aa:
                        base.pop(site, None)
                    else:
                        base[site] = aa
            self._cache[lin] = base
            return base

        parent = self.get_parent(lin)
        if parent and parent != lin:
            muts = dict(self.resolve(parent))
        else:
            muts = {}

        for key in (lin, short_lin):
            if key in self.branch_muts:
                for site, aa in self.branch_muts[key].items():
                    if WUHAN_RBD_WT.get(site) == aa:
                        muts.pop(site, None)
                    else:
                        muts[site] = aa

        self._cache[lin] = muts
        return muts


if __name__ == "__main__":
    resolver = PangoRBDResolver(DATA_DIR / "lineage_notes.txt", DATA_DIR / "alias_key.json")
    df = pd.read_csv(BENCHMARKS_DIR / "results_track2" / "track2_rolling_metrics.csv")
    lins = set()
    for col in ["base_list", "contour_list"]:
        for s in df[col].dropna():
            for x in str(s).split(";"):
                if x:
                    lins.add(x)
    zero_muts = [l for l in sorted(lins) if len(resolver.resolve(l)) == 0]
    print("Total selected lineages:", len(lins), "with 0 RBD muts:", len(zero_muts), zero_muts)
    for l in [
        "B.1.1.7", "B.1.617.2", "AY.4", "AY.103", "AY.1",
        "BA.1", "BA.1.1", "BA.2", "BA.2.12.1", "BA.5", "BA.5.2.1", "BF.7", "BQ.1", "BQ.1.1",
        "XBB.1.5", "XBB.1.16", "EG.5.1", "HK.3", "HV.1", "JD.1.1", "JN.1", "JN.1.4", "KP.2", "KP.3", "KP.3.1.1", "XEC", "LP.8.1", "NB.1.8.1", "XFG"
    ]:
        m = resolver.resolve(l)
        print(f"{l:10s} ({len(m):2d} RBD muts): {sorted(m.items())}")
