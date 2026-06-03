#!/usr/bin/env python3
"""corrections.py - JEC/JER jet corrections for the ScoutingNanoAOD slimmer.

Applies CMS JME POG Jet Energy Corrections (JEC) and, for MC, Jet Energy
Resolution (JER) smearing using coffea's ``jetmet_tools`` driven by the
plain-text correction files that the JME POG distributes.

The ScoutingPFJet collection is treated as *raw* (uncorrected): the stored
``pt``/``m`` are used as the raw inputs (rawFactor = 0) and the full chain is
applied on top:

  - MC   : L1FastJet + L2Relative + L3Absolute, then JER (PtResolution + SF,
           hybrid scaling/stochastic) with a per-jet matched GenJet pt.
  - Data : L1FastJet + L2Relative + L3Absolute + L2L3Residual, no JER. The
           L2L3Residual file is chosen per run period via the IOV table.

``JetCorrectionFactory`` builds the (expensive) coffea factory once and applies
it per chunk via :meth:`build_jets`, returning the coffea-corrected jets which
carry the nominal corrected ``pt``/``mass`` plus nested ``JES_jes`` and (MC)
``JER`` up/down variation records.

Notes on the JME text files (verified against coffea):
  - coffea selects its parser from the *second-to-last* dot token, so files
    MUST be named ``*.jec.txt`` (all JEC levels incl. L2L3Residual),
    ``*.junc.txt`` (Uncertainty), ``*.jr.txt`` (PtResolution) and
    ``*.jersf.txt`` (SF). A trailing ``.gz`` is allowed.
  - coffea classifies each correction by the *filename*, which must keep the
    JME tokens ``<campaign>_<dataera>_<datatype>_<level>_<jettype>``. JEC,
    Uncertainty and PtResolution files may have 5 or 6 underscore tokens; the
    JER **SF** file must have EXACTLY 5 tokens (coffea limitation).
"""

import os
import warnings

import awkward as ak
import numpy as np

# AK4 jets: match a reco jet to a GenJet within R_cone / 2.
AK4_R_CONE = 0.4
GEN_MATCH_DR = AK4_R_CONE / 2.0

# JEC levels applied, in the canonical order, per datatype.
_MC_JEC_LEVELS = ["L1FastJet", "L2Relative", "L3Absolute"]
_DATA_JEC_LEVELS = ["L1FastJet", "L2Relative", "L3Absolute", "L2L3Residual"]

# Required second-to-last dot token (coffea parser selector) per logical type.
_EXT = {"jec": ".jec.txt", "junc": ".junc.txt", "jr": ".jr.txt", "jersf": ".jersf.txt"}

# Rho branch the L1FastJet correction needs (one value per event).
RHO_BRANCH = "ScoutingRho_fixedGridRhoFastjetAll"


def _broadcast_rho(rho_per_event, jet_pt):
    """Broadcast a per-event rho scalar onto each jet (empty-event safe)."""
    _, rho_b = ak.broadcast_arrays(jet_pt, rho_per_event)
    return rho_b


def matched_gen_pt(reco_jets, gen_jets):
    """Per-reco-jet matched GenJet pt via Delta-R, 0.0 when unmatched.

    coffea's hybrid JER consumes this ``ptGenJet`` field and applies its own
    3*sigma resolution window internally; here we only do the nearest-within-
    cone (Delta-R < ``GEN_MATCH_DR``) match, since the ScoutingNanoAOD has no
    ``genJetIdx``. Reco jets with no gen match (or events with no GenJets) get
    0.0, which makes coffea fall back to stochastic smearing for those jets.
    """
    if gen_jets is None:
        return ak.values_astype(ak.zeros_like(reco_jets.pt), np.float32)

    pair = ak.cartesian({"r": reco_jets, "g": gen_jets}, nested=True)
    dphi = (pair.r.phi - pair.g.phi + np.pi) % (2 * np.pi) - np.pi
    deta = pair.r.eta - pair.g.eta
    dr2 = deta * deta + dphi * dphi

    # nearest gen jet per reco jet (axis=2 is the gen dimension)
    best = ak.argmin(dr2, axis=2, keepdims=True)
    min_dr2 = ak.firsts(dr2[best], axis=2)
    gpt = ak.firsts(pair.g.pt[best], axis=2)

    matched = ak.fill_none(min_dr2, np.inf) < (GEN_MATCH_DR * GEN_MATCH_DR)
    out = ak.where(matched, ak.fill_none(gpt, 0.0), 0.0)
    return ak.values_astype(out, np.float32)


class JetCorrectionFactory:
    """Builds a coffea ``CorrectedJetsFactory`` once and applies it per chunk.

    Operates on plain (eager) awkward arrays - no dask, no NanoEvents - which
    matches the slimmer's ``uproot.iterate`` chunk loop.
    """

    def __init__(self, corr_cfg: dict, is_mc: bool, run=None):
        # Imported lazily so the slimmer has no hard coffea dependency unless
        # corrections are actually requested.
        from coffea.jetmet_tools import CorrectedJetsFactory, JECStack
        from coffea.lookup_tools import extractor

        self.is_mc = bool(is_mc)
        self._cfg = corr_cfg
        self.resolved_files = self._resolve_files(corr_cfg, self.is_mc, run)

        ext = extractor()
        ext.add_weight_sets([f"* * {path}" for _kind, path in self.resolved_files])
        ext.finalize()
        evaluator = ext.make_evaluator()

        stack = JECStack({key: evaluator[key] for key in evaluator.keys()})
        self.has_jer = stack.jer is not None and stack.jersf is not None
        if self.is_mc and not self.has_jer:
            raise ValueError(
                "is_mc=True but no JER (PtResolution + SF) files were provided."
            )

        name_map = stack.blank_name_map
        name_map["JetPt"] = "pt"
        name_map["JetMass"] = "mass"
        name_map["JetEta"] = "eta"
        name_map["JetPhi"] = "phi"
        name_map["JetA"] = "area"
        name_map["ptRaw"] = "pt_raw"
        name_map["massRaw"] = "mass_raw"
        name_map["Rho"] = "rho"
        if self.has_jer:
            # Required by CorrectedJetsFactory.build() whenever JER is in the
            # stack; the field must exist on the jets passed to build_jets().
            name_map["ptGenJet"] = "pt_gen"
            self._factory = CorrectedJetsFactory(name_map, stack)
        else:
            # Data: no JER, so ptGenJet is intentionally absent. Silence coffea's
            # (irrelevant) warning about falling back to stochastic smearing.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message=".*ptGenJet.*", category=UserWarning
                )
                self._factory = CorrectedJetsFactory(name_map, stack)

        self.has_jes = stack.junc is not None
        self.uncertainties = list(self._factory.uncertainties())

    # ------------------------------------------------------------------ build
    def build_jets(self, jets_short, rho, gen_jets=None):
        """Attach the fields coffea needs and return the corrected jets.

        Parameters
        ----------
        jets_short : ak.Array
            Zipped jets with short fields ``pt, eta, phi, mass, area``.
        rho : ak.Array
            Per-event rho scalar (``ScoutingRho_fixedGridRhoFastjetAll``).
        gen_jets : ak.Array or None
            Per-event GenJet record (``pt, eta, phi, mass``); MC only.
        """
        if rho is None:
            raise ValueError(
                f"Rho ('{RHO_BRANCH}') is required for L1FastJet but was not provided."
            )
        jets = ak.with_field(jets_short, jets_short.pt, "pt_raw")
        jets = ak.with_field(jets, jets_short.mass, "mass_raw")
        jets = ak.with_field(jets, _broadcast_rho(rho, jets_short.pt), "rho")
        if self.has_jer:
            jets = ak.with_field(jets, matched_gen_pt(jets_short, gen_jets), "pt_gen")
        return self._factory.build(jets)

    # -------------------------------------------------------------- internals
    @staticmethod
    def _corrector_name(path: str) -> str:
        """coffea derives the corrector name from the basename before the dots."""
        return os.path.basename(path).split(".")[0]

    def _check_ext(self, path: str, kind: str) -> None:
        base = os.path.basename(path)
        ext = _EXT[kind]
        if not (base.endswith(ext) or base.endswith(ext + ".gz")):
            raise ValueError(
                f"'{base}' must end with '{ext}' (or '{ext}.gz') so coffea parses "
                f"it as a {kind} file. Rename the JME POG file accordingly."
            )

    def _check_tokens(self, path: str, kind: str) -> None:
        ntok = len(self._corrector_name(path).split("_"))
        if kind == "jersf":
            if ntok != 5:
                raise ValueError(
                    f"JER SF file '{self._corrector_name(path)}' must have EXACTLY 5 "
                    "underscore tokens (<campaign>_<dataera>_<datatype>_SF_<jettype>); "
                    "coffea's JetResolutionScaleFactor requires it. Rename e.g. "
                    "'Summer22_JRV1_MC_SF_AK4PFHLT.jersf.txt'."
                )
        elif kind in ("jec", "jr"):
            if not 5 <= ntok <= 6:
                raise ValueError(
                    f"'{self._corrector_name(path)}' must have 5-6 underscore tokens "
                    "(<campaign>_<dataera>_<datatype>_<level>_<jettype>)."
                )

    def _resolve_one(self, name: str, files_dir: str) -> str:
        """Locate a correction file: config dir, then cwd, then packaged data."""
        candidates = [os.path.join(files_dir, name), name]
        for cand in candidates:
            if os.path.exists(cand):
                return cand
        try:
            import importlib.resources as ir

            pkg = ir.files("run3_mj_slimmer") / "data" / "jme" / os.path.basename(name)
            if pkg.is_file():
                return str(pkg)
        except (ModuleNotFoundError, AttributeError, FileNotFoundError):
            pass
        raise FileNotFoundError(
            f"JME correction file not found: '{name}' "
            f"(looked in '{files_dir}', cwd, and packaged run3_mj_slimmer/data/jme)."
        )

    def _pick_residual(self, spec, cfg, run, level):
        """Resolve a run-period-dependent JEC file (e.g. L2L3Residual)."""
        if isinstance(spec, str):
            return spec
        if not isinstance(spec, dict):
            raise ValueError(f"corrections.jec.{level} must be a string or a map.")
        if len(spec) == 1:
            return next(iter(spec.values()))
        iovs = cfg.get("iovs")
        if run is None or not iovs:
            raise ValueError(
                f"corrections.jec.{level} is a run-period map; an 'iovs' table and a "
                "run number are required to select the right file."
            )
        for iov in iovs:
            if int(iov["run_min"]) <= int(run) <= int(iov["run_max"]):
                period = iov["period"]
                if period not in spec:
                    raise ValueError(
                        f"IOV period '{period}' (run {run}) has no entry in "
                        f"corrections.jec.{level}."
                    )
                return spec[period]
        raise ValueError(f"No IOV period matches run {run} for {level}.")

    def _resolve_files(self, cfg, is_mc, run):
        files_dir = cfg.get("files_dir", "data/jme")
        jec = cfg.get("jec")
        if not isinstance(jec, dict):
            raise ValueError("corrections.jec must be a mapping of JEC level -> file.")

        out = []
        for level in (_MC_JEC_LEVELS if is_mc else _DATA_JEC_LEVELS):
            spec = jec.get(level)
            if spec is None:
                raise ValueError(f"corrections.jec is missing level '{level}'.")
            name = self._pick_residual(spec, cfg, run, level)
            path = self._resolve_one(name, files_dir)
            self._check_ext(path, "jec")
            self._check_tokens(path, "jec")
            out.append((level, path))

        junc = cfg.get("junc")
        if junc:
            path = self._resolve_one(junc, files_dir)
            self._check_ext(path, "junc")
            out.append(("junc", path))

        if is_mc:
            jer = cfg.get("jer")
            if not isinstance(jer, dict) or "PtResolution" not in jer or "SF" not in jer:
                raise ValueError(
                    "MC corrections require corrections.jer with 'PtResolution' and 'SF'."
                )
            pr = self._resolve_one(jer["PtResolution"], files_dir)
            self._check_ext(pr, "jr")
            self._check_tokens(pr, "jr")
            out.append(("jr", pr))
            sf = self._resolve_one(jer["SF"], files_dir)
            self._check_ext(sf, "jersf")
            self._check_tokens(sf, "jersf")
            out.append(("jersf", sf))

        return out
