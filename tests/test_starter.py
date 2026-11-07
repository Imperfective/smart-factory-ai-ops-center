import unittest
import json
from pathlib import Path
from starter.simulator import simulate
from starter.safety import VirtualPLC,VirtualStopRequest
from starter.decision import mock_jev_decide

class StarterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario=json.loads(Path("examples/press_factory_demo.json").read_text(encoding="utf-8"))

    def test_determinism(self):
        self.assertEqual(simulate(self.scenario),simulate(self.scenario))

    def test_more_cycle_time_less_output(self):
        a=dict(self.scenario)
        a["machines"]=[dict(m,cycle_time_s=m["cycle_time_s"]*2) for m in self.scenario["machines"]]
        self.assertLess(simulate(a)[1]["total_good"],simulate(self.scenario)[1]["total_good"])

    def test_no_approval_no_virtual_stop(self):
        plc=VirtualPLC(["PRESS_01"])
        res=plc.request_stop(VirtualStopRequest("c1","PRESS_01","alice",False,True))
        self.assertEqual(res["status"],"REJECTED")
        self.assertEqual(plc.states["PRESS_01"],"RUNNING")

    def test_stale_evidence_blocks_virtual_stop(self):
        plc=VirtualPLC(["PRESS_01"])
        res=plc.request_stop(VirtualStopRequest("c2","PRESS_01","alice",True,False))
        self.assertEqual(res["reason"],"EVIDENCE_STALE")

    def test_authorized_virtual_stop_only(self):
        plc=VirtualPLC(["PRESS_01"])
        req=VirtualStopRequest("c3","PRESS_01","alice",True,True)
        res=plc.request_stop(req)
        self.assertEqual(res["status"],"EXECUTED_VIRTUAL_ONLY")
        self.assertEqual(res,plc.request_stop(req))
        self.assertEqual(plc.states["PRESS_01"],"STOPPED")

    def test_external_target_rejected(self):
        plc=VirtualPLC(["PRESS_01"])
        res=plc.request_stop(VirtualStopRequest("c4","real-plc-192.168.1.22","alice",True,True))
        self.assertEqual(res["reason"],"VIRTUAL_TARGET_REQUIRED")

    def test_mock_provider_identified(self):
        self.assertEqual(mock_jev_decide({"fault_injected":True}).provider,"MOCK_JEV")

if __name__=="__main__":unittest.main()
