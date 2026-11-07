"""Electrical twin: a small pandapower feeder (External grid → HV bus → transformer → LV bus → feeders → loads).

Static AC power flow only (docs/06). Values are DEMO/SYNTHETIC assumptions, not measurements. On solver
failure the result is `POWERFLOW_FAILED` — never fabricated normal values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..contracts.factory import ElectricalSpec, Machine


@dataclass
class PowerflowResult:
    status: str                      # OK | POWERFLOW_FAILED | UNAVAILABLE
    lv_bus_voltage_pu: float | None = None
    min_bus_voltage_pu: float | None = None
    transformer_loading_percent: float | None = None
    max_line_loading_percent: float | None = None
    total_active_kw: float | None = None
    total_reactive_kvar: float | None = None
    feeder_kw: dict | None = None
    error: str | None = None


class ElectricalTwin:
    def __init__(self, spec: ElectricalSpec, machines: list[Machine]):
        self.spec = spec
        self.machines = {m.machine_id: m for m in machines}
        self.feeders = sorted(set(spec.feeders) | {m.feeder_id for m in machines})
        self._net = None
        self._load_idx: dict[str, int] = {}
        self._extra_load_idx: dict[str, int] = {}
        self._aux_idx = None
        self.available = True
        self.error = None
        try:
            self._build()
        except Exception as exc:  # noqa: BLE001 — pandapower missing or build failure => UNAVAILABLE
            self.available = False
            self.error = f"{type(exc).__name__}: {exc}"

    def _build(self):
        import pandapower as pp
        net = pp.create_empty_network(name=self.spec.name, sn_mva=1.0)
        hv = pp.create_bus(net, vn_kv=self.spec.hv_kv, name="HV_BUS")
        lv = pp.create_bus(net, vn_kv=self.spec.lv_kv, name="LV_BUS")
        pp.create_ext_grid(net, hv, vm_pu=1.0, name="EXTERNAL_GRID")
        pp.create_transformer_from_parameters(net, hv, lv, sn_mva=self.spec.transformer_sn_mva, vn_hv_kv=self.spec.hv_kv,
                                              vn_lv_kv=self.spec.lv_kv, vkr_percent=self.spec.transformer_vkr_percent,
                                              vk_percent=self.spec.transformer_vk_percent, pfe_kw=0.5, i0_percent=0.2,
                                              name="TR_DEMO")
        self._aux_idx = pp.create_load(net, lv, p_mw=self.spec.auxiliary_kw / 1000, q_mvar=0.0, name="AUX_CONTROL_ROOM")
        for f in self.feeders:
            fb = pp.create_bus(net, vn_kv=self.spec.lv_kv, name=f)
            # generic LV cable: r/x per km typical of 95 mm² Cu (DEMO assumption)
            pp.create_line_from_parameters(net, lv, fb, length_km=self.spec.feeder_length_km, r_ohm_per_km=0.2,
                                           x_ohm_per_km=0.08, c_nf_per_km=0.0, max_i_ka=0.25, name=f"LINE_{f}")
            self._extra_load_idx[f] = pp.create_load(net, fb, p_mw=0.0, q_mvar=0.0, name=f"EXTRA_{f}")
            for m in self.machines.values():
                if m.feeder_id == f:
                    self._load_idx[m.machine_id] = pp.create_load(net, fb, p_mw=0.0, q_mvar=0.0, name=m.machine_id)
        self._net = net
        self._lv = lv
        self._pp = pp

    def solve(self, loads_kw: dict[str, float], extra_feeder_kw: dict[str, float] | None = None) -> PowerflowResult:
        if not self.available:
            return PowerflowResult(status="UNAVAILABLE", error=self.error)
        net, pp = self._net, self._pp
        try:
            for mid, kw in loads_kw.items():
                m = self.machines[mid]
                pf = m.power.power_factor
                q = kw * math.tan(math.acos(pf))
                net.load.at[self._load_idx[mid], "p_mw"] = kw / 1000
                net.load.at[self._load_idx[mid], "q_mvar"] = q / 1000
            for f, idx in self._extra_load_idx.items():
                kw = (extra_feeder_kw or {}).get(f, 0.0)
                net.load.at[idx, "p_mw"] = kw / 1000
                net.load.at[idx, "q_mvar"] = kw * 0.33 / 1000
            pp.runpp(net, numba=False, max_iteration=30)
            if not net.converged:
                return PowerflowResult(status="POWERFLOW_FAILED", error="not converged")
            vm = net.res_bus.vm_pu
            feeder_kw = {}
            for f in self.feeders:
                feeder_kw[f] = float(sum(net.res_load.p_mw[self._load_idx[mid]] for mid, m in self.machines.items() if m.feeder_id == f) * 1000
                                     + net.res_load.p_mw[self._extra_load_idx[f]] * 1000)
            return PowerflowResult(status="OK", lv_bus_voltage_pu=float(vm[self._lv]), min_bus_voltage_pu=float(vm.min()),
                                   transformer_loading_percent=float(net.res_trafo.loading_percent.iloc[0]),
                                   max_line_loading_percent=float(net.res_line.loading_percent.max()),
                                   total_active_kw=float(net.res_ext_grid.p_mw.iloc[0] * 1000),
                                   total_reactive_kvar=float(net.res_ext_grid.q_mvar.iloc[0] * 1000), feeder_kw=feeder_kw)
        except Exception as exc:  # noqa: BLE001
            return PowerflowResult(status="POWERFLOW_FAILED", error=f"{type(exc).__name__}: {exc}")


def current_a(kw: float, voltage_pu: float | None, lv_kv: float, pf: float) -> float:
    """Three-phase line current estimate I = P / (√3 · V · pf)."""
    v = lv_kv * 1000 * (voltage_pu if voltage_pu else 1.0)
    return 0.0 if kw <= 0 else kw * 1000 / (math.sqrt(3) * v * pf)
