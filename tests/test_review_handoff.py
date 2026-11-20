import unittest

from examples.review_handoff import (
    observe_v1,
    observe_v2_failure_keeps_state,
    observe_v2_primary,
    observe_v2_revocation_first,
    observe_v2_split_delivery,
)


class ReviewHandoffTests(unittest.TestCase):
    def test_v1_rehearsal_unchanged(self):
        result = observe_v1()
        self.assertEqual(result['v1_current'], [['P', 4], ['Q', 12], ['Q', 12], ['S', 13]])
        self.assertTrue(result['same_q_r_ledger_digest'])

    def test_v2_primary_fallback_recovery_reopen(self):
        result = observe_v2_primary()
        self.assertEqual(result['steps'][0][1], ['S', 13])
        self.assertEqual(result['steps'][1][1], ['P', 4])
        self.assertEqual(result['steps'][2][1], ['R', 12])
        reopened = result['reopened']
        self.assertEqual(reopened['status'], 'revoked')
        self.assertEqual((reopened['rows'], reopened['liters']), ([13, 0], 13))
        self.assertEqual(reopened['origins'][0][:3], ['甲', '甲表', '甲潮班'])

    def test_v2_revocation_first(self):
        result = observe_v2_revocation_first()
        self.assertEqual(result['before'], ['P', 4])
        self.assertEqual(result['late'], {'Q': 'revoked', 'S': 'revoked'})
        self.assertEqual(result['recovered'], ['R', 12])
        self.assertEqual(result['after_replay'], ['R', 12])

    def test_v2_split_delivery(self):
        result = observe_v2_split_delivery()
        self.assertEqual(result['pending'], 'pending')
        self.assertEqual(result['q_unreceived'], 'unreceived_release')
        self.assertEqual(result['formal_pending'], ['P', 4])
        self.assertEqual(result['eligible'], 'eligible')
        self.assertEqual(result['formal_complete'], ['S', 13])
        self.assertEqual(result['tie'], ['Q-2', 12])

    def test_v2_failure_keeps_state(self):
        result = observe_v2_failure_keeps_state()
        self.assertEqual(result['stored_error'], 'shift commit interrupted')
        self.assertEqual(result['after_failure'], ['S', 13])
        self.assertEqual(result['after_bad'], ['S', 13])
        self.assertEqual(result['after_retry'], ['P', 4])


if __name__ == '__main__':
    unittest.main()
