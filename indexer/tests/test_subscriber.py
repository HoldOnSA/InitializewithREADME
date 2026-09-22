"""The failed-transaction log allow-list in `Subscriber._handle_logs`.

Most events only mean something if the transaction actually succeeded, since
`emit!` is a log side effect of a state mutation the runtime rolls back on
failure - processing one from a failed transaction risks a phantom feed line
for something that never happened. `VaultDeficitDetected` is the one
deliberate exception (see `reconcile.rs`'s handler), so this locks down that
the allow-list is narrow and doesn't leak to other events.
"""

import base64
import unittest

from stackapp_indexer.config import Settings
from stackapp_indexer.layouts import encode_event
from stackapp_indexer.store import Store
from stackapp_indexer.subscriber import Subscriber


def logs_value(event_name, data, err=None):
    blob = base64.b64encode(encode_event(event_name, data)).decode()
    return {
        "err": err,
        "signature": "fakeSig111",
        "logs": [
            "Program Fg6Paf... invoke [1]",
            f"Program data: {blob}",
            "Program Fg6Paf... success" if err is None else "Program Fg6Paf... failed",
        ],
    }


def sample(name):
    from stackapp_indexer.layouts import EVENT_LAYOUTS
    from stackapp_indexer.selftest import sample_struct

    return sample_struct(EVENT_LAYOUTS[name])


class TestFailedTransactionAllowList(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.subscriber = Subscriber(Settings(), self.store)

    def test_a_successful_transactions_events_are_always_processed(self):
        self.subscriber._handle_logs(logs_value("FeeCollected", sample("FeeCollected")), slot=1)
        self.assertEqual(len(self.store.feed), 1)
        self.assertEqual(self.store.feed[0].name, "FeeCollected")

    def test_most_events_from_a_failed_transaction_are_discarded(self):
        value = logs_value("FeeCollected", sample("FeeCollected"), err={"InstructionError": [0, {}]})
        self.subscriber._handle_logs(value, slot=1)
        self.assertEqual(len(self.store.feed), 0, "a phantom FeeCollected must not reach the feed")

    def test_vault_deficit_detected_survives_a_failed_transaction(self):
        """The whole point of the allow-list: `reconcile` always fails when
        it detects a deficit, but the alert must still get through."""
        value = logs_value(
            "VaultDeficitDetected", sample("VaultDeficitDetected"), err={"InstructionError": [0, {}]}
        )
        self.subscriber._handle_logs(value, slot=1)
        self.assertEqual(len(self.store.feed), 1)
        self.assertEqual(self.store.feed[0].name, "VaultDeficitDetected")

    def test_the_allow_list_is_exactly_one_event(self):
        self.assertEqual(Subscriber._OBSERVABLE_EVEN_ON_FAILURE, {"VaultDeficitDetected"})


if __name__ == "__main__":
    unittest.main()
