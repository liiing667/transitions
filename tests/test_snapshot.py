# -*- coding: utf-8 -*-
try:
    from builtins import object
except ImportError:
    pass

import json

from unittest import TestCase

from transitions import MachineError
from transitions.extensions.nesting import NestedState, HierarchicalMachine

try:
    from unittest.mock import MagicMock
except ImportError:
    from mock import MagicMock  # type: ignore


class TestSnapshot(TestCase):

    def setUp(self):
        self.states = [
            'idle',
            {'name': 'review', 'children': ['draft', 'ready'], 'initial': 'draft'},
            {'name': 'signing', 'parallel': [
                {'name': 'identity', 'children': ['pending', 'verified'], 'initial': 'pending'},
                {'name': 'payment', 'children': ['pending', 'done'], 'initial': 'pending'}]},
        ]
        self.transitions = [
            {'trigger': 'submit', 'source': 'idle', 'dest': 'review'},
            {'trigger': 'approve', 'source': 'review_draft', 'dest': 'review_ready'},
            {'trigger': 'sign', 'source': 'review', 'dest': 'signing'},
            {'trigger': 'verify', 'source': 'signing_identity_pending',
             'dest': 'signing_identity_verified'},
            {'trigger': 'pay', 'source': 'signing_payment_pending', 'dest': 'signing_payment_done'},
        ]
        self.machine_cls = HierarchicalMachine

    def create_machine(self, **kwargs):
        kwargs.setdefault('states', self.states)
        kwargs.setdefault('transitions', self.transitions)
        kwargs.setdefault('initial', 'idle')
        return self.machine_cls(**kwargs)

    def test_snapshot_nested(self):
        machine = self.create_machine()
        machine.submit()
        self.assertEqual({'review': {'draft': {}}}, machine.get_state_snapshot())
        machine.approve()
        self.assertEqual({'review': {'ready': {}}}, machine.get_state_snapshot())

    def test_snapshot_parallel(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        machine.sign()
        machine.verify()
        self.assertEqual({'signing': {'identity': {'verified': {}}, 'payment': {'pending': {}}}},
                         machine.get_state_snapshot())

    def test_snapshot_root_leaf(self):
        machine = self.create_machine()
        self.assertEqual({'idle': {}}, machine.get_state_snapshot())

    def test_snapshot_roundtrip_nested(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        snapshot = machine.get_state_snapshot()

        restored = self.create_machine()
        restored.set_state_snapshot(snapshot)
        self.assertEqual(machine.state, restored.state)
        self.assertEqual('review_ready', restored.state)
        # the restored machine can continue to process events
        restored.sign()
        self.assertEqual(['signing_identity_pending', 'signing_payment_pending'], restored.state)

    def test_snapshot_roundtrip_parallel(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        machine.sign()
        machine.verify()
        snapshot = machine.get_state_snapshot()

        restored = self.create_machine()
        restored.set_state_snapshot(snapshot)
        self.assertEqual(machine.state, restored.state)
        self.assertEqual(['signing_identity_verified', 'signing_payment_pending'], restored.state)
        # each parallel branch can continue independently after a restore
        restored.pay()
        self.assertEqual(['signing_identity_verified', 'signing_payment_done'], restored.state)

    def test_snapshot_json_serializable(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        machine.sign()
        snapshot = json.loads(json.dumps(machine.get_state_snapshot()))

        restored = self.create_machine()
        restored.set_state_snapshot(snapshot)
        self.assertEqual(machine.state, restored.state)

    def test_snapshot_restore_no_callbacks(self):
        enter_mock = MagicMock()
        exit_mock = MagicMock()

        class Model(object):
            def on_enter_review(self):
                enter_mock()

            def on_enter_review_ready(self):
                enter_mock()

            def on_exit_idle(self):
                exit_mock()

        model = Model()
        machine = self.create_machine()
        machine.add_model(model, initial='idle')
        machine.set_state_snapshot({'review': {'ready': {}}}, model=model)
        self.assertEqual('ready', model.state.split(self.machine_cls.state_cls.separator)[-1])
        self.assertFalse(enter_mock.called)
        self.assertFalse(exit_mock.called)

    def test_snapshot_invalid_type(self):
        machine = self.create_machine()
        for snapshot in [None, 'review_ready', ['review', 'ready'], 42, {}]:
            with self.assertRaises(ValueError):
                machine.set_state_snapshot(snapshot)

    def test_snapshot_unknown_state(self):
        machine = self.create_machine()
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'archived': {}})
        # unknown child of a known parent
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'review': {'archived': {}}})
        # child of a leaf state
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'idle': {'draft': {}}})

    def test_snapshot_stale_configuration(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        snapshot = machine.get_state_snapshot()

        # a machine with a changed state configuration must reject the old snapshot
        outdated = self.machine_cls(states=['idle', {'name': 'review', 'children': ['draft'],
                                                     'initial': 'draft'}], initial='idle')
        with self.assertRaises(ValueError):
            outdated.set_state_snapshot(snapshot)

    def test_snapshot_missing_parallel_branch(self):
        machine = self.create_machine()
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'signing': {'identity': {'pending': {}}}})
        # additional unknown branch
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'signing': {'identity': {'pending': {}},
                                                    'payment': {'pending': {}},
                                                    'shipping': {}}})

    def test_snapshot_refuses_to_guess_initial(self):
        machine = self.create_machine()
        # 'review' has an initial substate; an empty child dict is ambiguous
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'review': {}})
        # same for a parallel branch with initial
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'signing': {'identity': {}, 'payment': {'pending': {}}}})

    def test_snapshot_multiple_children_non_parallel(self):
        machine = self.create_machine()
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'review': {'draft': {}, 'ready': {}}})

    def test_snapshot_invalid_children_type(self):
        machine = self.create_machine()
        with self.assertRaises(ValueError):
            machine.set_state_snapshot({'review': 'ready'})

    def test_snapshot_multiple_models(self):
        machine = self.create_machine()
        model_a, model_b = object(), object()
        # object() has no 'state' attribute; use a simple namespace instead
        class Model(object):
            pass
        model_a, model_b = Model(), Model()
        machine.add_model([model_a, model_b], initial='idle')
        with self.assertRaises(MachineError):
            machine.get_state_snapshot()
        machine.submit()
        snapshot = machine.get_state_snapshot(model=machine)
        self.assertEqual({'review': {'draft': {}}}, snapshot)
        # without an explicit model, all models are restored
        machine.set_state_snapshot(snapshot)
        expected = 'review{0}draft'.format(self.machine_cls.state_cls.separator)
        self.assertEqual(expected, model_a.state)
        self.assertEqual(expected, model_b.state)


class TestSnapshotCustomSeparator(TestSnapshot):

    separator = '.'

    def setUp(self):
        super(TestSnapshotCustomSeparator, self).setUp()
        separator = self.separator

        class CustomNestedState(NestedState):
            pass

        CustomNestedState.separator = separator

        class CustomHierarchicalMachine(HierarchicalMachine):
            state_cls = CustomNestedState

        self.machine_cls = CustomHierarchicalMachine
        self.transitions = [
            {'trigger': 'submit', 'source': 'idle', 'dest': 'review'},
            {'trigger': 'approve', 'source': 'review{0}draft'.format(separator),
             'dest': 'review{0}ready'.format(separator)},
            {'trigger': 'sign', 'source': 'review', 'dest': 'signing'},
            {'trigger': 'verify', 'source': 'signing{0}identity{0}pending'.format(separator),
             'dest': 'signing{0}identity{0}verified'.format(separator)},
            {'trigger': 'pay', 'source': 'signing{0}payment{0}pending'.format(separator),
             'dest': 'signing{0}payment{0}done'.format(separator)},
        ]

    def test_snapshot_roundtrip_nested(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        snapshot = machine.get_state_snapshot()
        # the snapshot format does not depend on the configured separator
        self.assertEqual({'review': {'ready': {}}}, snapshot)

        restored = self.create_machine()
        restored.set_state_snapshot(snapshot)
        self.assertEqual('review{0}ready'.format(self.separator), restored.state)

    def test_snapshot_roundtrip_parallel(self):
        machine = self.create_machine()
        machine.submit()
        machine.approve()
        machine.sign()
        machine.verify()
        snapshot = machine.get_state_snapshot()
        self.assertEqual({'signing': {'identity': {'verified': {}}, 'payment': {'pending': {}}}},
                         snapshot)

        restored = self.create_machine()
        restored.set_state_snapshot(snapshot)
        separator = self.separator
        self.assertEqual(['signing{0}identity{0}verified'.format(separator),
                          'signing{0}payment{0}pending'.format(separator)], restored.state)
