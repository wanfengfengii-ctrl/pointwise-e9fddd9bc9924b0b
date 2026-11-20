import unittest

from examples.shift import play_shift


class ShiftTests(unittest.TestCase):
    def test_shift_handoff(self):
        result = play_shift()
        self.assertEqual(result['case_id'], 'v5')
        self.assertEqual(result['actual']['甲件1']['rows'], [7, 7, 0])
        downstream = result['downstream_actual']
        self.assertEqual(downstream['installed'], [['甲件2', 2], ['乙件1', 1], ['甲件1', 2]])
        audits = downstream['audits']
        self.assertEqual((audits['甲']['count'], audits['甲']['liters'], audits['甲']['rows']), (3, 15, [8, 7, 0]))
        self.assertEqual((audits['甲旧']['count'], audits['甲旧']['liters']), (3, 14))
        self.assertEqual(audits['甲']['origins'], audits['甲旧']['origins'])
        self.assertEqual(audits['乙'], audits['乙发行'])
        self.assertEqual((audits['乙']['count'], audits['乙']['liters']), (1, 4))
