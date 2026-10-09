# See LICENSE file for full copyright and licensing details.

import base64
import json
from types import SimpleNamespace

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestPracticeGrading(TransactionCase):
    def setUp(self):
        super().setUp()
        company_partner = self.env["res.partner"].create(
            {
                "name": "Student Company",
                "is_company": True,
                "company_id": False,
            }
        )
        self.student_company = self.env["res.company"].create(
            {
                "name": "Student Company",
                "partner_id": company_partner.id,
                "is_student_company": True,
            }
        )
        self.payload = {
            "schema_version": "1.0",
            "name": "Partner Practice",
            "pass_score": 1,
            "criteria": [
                {
                    "id": "partner_created",
                    "name": "Partner created",
                    "weight": 1,
                    "mode": "automatic",
                    "verifier": {
                        "type": "record_exists",
                        "model": "res.partner",
                        "domain": [["name", "=", "Practice Partner"]],
                    },
                },
                {
                    "id": "explanation",
                    "name": "Explanation",
                    "weight": 1,
                    "mode": "manual",
                },
            ],
        }

    def _import_rubric(self, payload=None):
        content = json.dumps(payload or self.payload).encode("utf-8")
        wizard = self.env["practice.rubric.import.wizard"].create(
            {
                "rubric_file": base64.b64encode(content),
                "rubric_filename": "rubric.json",
            }
        )
        action = wizard.action_import()
        return self.env["practice.rubric"].browse(action["res_id"])

    def test_import_creates_draft_rubric(self):
        rubric = self._import_rubric()
        self.assertEqual(rubric.state, "draft")
        self.assertEqual(rubric.total_weight, 2)
        self.assertEqual(len(rubric.criterion_ids), 2)

    def test_import_accepts_rubric_metadata_and_declared_total(self):
        payload = dict(self.payload)
        payload.update(
            {
                "evaluation_scope": "Sales workflow setup",
                "notes": "Check the submitted practice database.",
                "total_weight": 2,
            }
        )
        rubric = self._import_rubric(payload)
        self.assertEqual(rubric.evaluation_scope, payload["evaluation_scope"])
        self.assertEqual(rubric.notes, payload["notes"])
        self.assertEqual(rubric.total_weight, payload["total_weight"])

    def test_import_rejects_incorrect_declared_total(self):
        payload = dict(self.payload)
        payload["total_weight"] = 3
        with self.assertRaisesRegex(ValidationError, "total weight must match"):
            self.env["practice.rubric"].validate_import_payload(payload)

    def test_import_accepts_schema_1_1_scope_and_notes(self):
        payload = dict(self.payload)
        payload.update(
            {
                "schema_version": "1.1",
                "evaluation_scope": {
                    "company": "assigned_student_company",
                    "records": "student_database",
                    "exclude_demo_data": True,
                },
                "notes": ["Only completed operations count."],
                "total_weight": 2,
            }
        )
        rubric = self._import_rubric(payload)
        self.assertEqual(rubric.schema_version, "1.1")
        self.assertEqual(rubric.evaluation_scope, payload["evaluation_scope"])
        self.assertEqual(rubric.notes, payload["notes"])

    def test_schema_1_1_accepts_all_rubric_verifiers(self):
        verifiers = [
            {"type": "workflow_linked", "workflow": "replenishment_purchase", "product_name": "Televisión", "required_purchase_state": ["purchase", "done"]},
            {"type": "workflow_linked", "workflow": "purchase_receipt_done", "product_name": "Televisión", "purchase_origin": "replenishment"},
            {"type": "hierarchical_locations", "parent_kind": "warehouse_stock", "pasillos": ["P1"], "estanterias": ["I"], "columnas": ["C1"], "alturas": ["A1"], "usage": "internal", "expected_leaf_count": 1},
            {"type": "putaway_rule", "product_name": "Televisión", "target_path": "P1/I/C1/A1"},
            {"type": "stock_move_destination", "product_name": "Televisión", "quantity": 4, "destination_path": "P1/I/C1/A1", "state": "done", "picking_type": "incoming"},
            {"type": "serial_receipt", "product_code": "W40-MSI-SER", "minimum_serials": 6, "unique": True, "state": "done", "destination_path": "P1/I/C1/A1"},
            {"type": "serial_delivery", "product_code": "W40-MSI-SER", "quantity": 2, "require_distinct": True, "require_related_sale": True},
            {"type": "supplier_offer", "product_name": "Televisión", "vendor_ref": "PROV-W40-A", "price": 490, "min_qty": 1, "delay": 5},
            {"type": "linked_purchase_receipt", "product_name": "Televisión", "vendor_ref": "PROV-W40-B", "qty": 12, "unit_price": 455, "receipt_state": "done"},
            {"type": "linked_return", "product_code": "W40-MSI-SER", "quantity": 1, "state": "done", "original_sale_qty": 2},
            {"type": "return_same_serial", "product_code": "W40-MSI-SER", "returned_quantity": 1, "must_match_original_delivery": True, "internal_destination": True},
            {"type": "product_removal_configuration", "product_code": "W40-FIFO", "tracking": "lot", "removal_strategy": "fifo"},
            {"type": "sequenced_lot_receipts", "product_code": "W40-FIFO", "lots": [{"name": "W40-FIFO-A", "qty": 8}, {"name": "W40-FIFO-B", "qty": 10}]},
            {"type": "lot_delivery_split", "product_code": "W40-FIFO", "qty_total": 12, "lot_quantities": {"W40-FIFO-A": 8, "W40-FIFO-B": 4}, "require_related_sale": True, "state": "done"},
            {"type": "fefo_receipts_and_dates", "product_code": "W40-FEFO", "lots": [{"name": "W40-FEFO-A", "qty": 10}, {"name": "W40-FEFO-B", "qty": 10}], "date_order": "B_before_A", "arrival_order": "A_before_B", "removal_dates_must_be_future": True},
        ]
        payload = {
            "schema_version": "1.1",
            "name": "W40 verifier contract",
            "evaluation_scope": {
                "company": "assigned_student_company",
                "records": "student_database",
                "exclude_demo_data": True,
            },
            "notes": [],
            "total_weight": len(verifiers),
            "criteria": [
                {
                    "id": "verifier_%02d" % index,
                    "name": verifier["type"],
                    "weight": 1,
                    "mode": "automatic",
                    "verifier": verifier,
                }
                for index, verifier in enumerate(verifiers, start=1)
            ],
        }
        self.env["practice.rubric"].validate_import_payload(payload)

    def test_composite_verifier_dispatches_without_matching_records(self):
        rubric = self._import_rubric()
        run = self.env["practice.grading.run"].create(
            {
                "rubric_id": rubric.id,
                "company_ids": [(6, 0, self.student_company.ids)],
            }
        )
        criterion = SimpleNamespace(
            id=rubric.criterion_ids[0].id,
            mode="automatic",
            verifier_type="stock_move_destination",
            verifier_config={
                "type": "stock_move_destination",
                "product_code": "W40-DOES-NOT-EXIST",
                "quantity": 4,
                "destination_path": "P1/D/C2/A1",
                "state": "done",
                "picking_type": "incoming",
            },
            weight=4,
        )
        result = run._evaluate_criterion(self.student_company, criterion)
        self.assertEqual(result["status"], "failed")

    def test_import_rejects_unavailable_model(self):
        payload = dict(self.payload)
        payload["criteria"] = [dict(self.payload["criteria"][0])]
        payload["criteria"][0]["verifier"] = dict(payload["criteria"][0]["verifier"])
        payload["criteria"][0]["verifier"]["model"] = "invalid.model"
        with self.assertRaises(ValidationError):
            self.env["practice.rubric"].validate_import_payload(payload)

    def test_run_keeps_company_evidence_separate(self):
        self.env["res.partner"].create(
            {"name": "Practice Partner", "company_id": self.student_company.id}
        )
        rubric = self._import_rubric()
        rubric.action_approve()
        run = self.env["practice.grading.run"].create(
            {"rubric_id": rubric.id, "company_ids": [(6, 0, self.student_company.ids)]}
        )
        run.action_run()
        automatic_result = run.result_ids.filtered(
            lambda result: result.criterion_id.external_id == "partner_created"
        )
        self.assertEqual(automatic_result.status, "passed")
        self.assertEqual(automatic_result.automatic_points, 1)
        self.assertEqual(automatic_result.evidence["record_ids"], [
            self.env["res.partner"].search(
                [
                    ("name", "=", "Practice Partner"),
                    ("company_id", "=", self.student_company.id),
                ]
            ).id
        ])

    def test_completed_run_can_be_repeated_until_locked(self):
        rubric = self._import_rubric()
        rubric.action_approve()
        run = self.env["practice.grading.run"].create(
            {"rubric_id": rubric.id, "company_ids": [(6, 0, self.student_company.ids)]}
        )

        run.action_run()
        first_result_ids = set(run.result_ids.ids)
        self.assertEqual(run.state, "done")
        self.assertEqual(
            len(
                run.message_ids.filtered(
                    lambda message: "Evaluation executed on" in message.body
                )
            ),
            1,
        )

        self.env["res.partner"].create(
            {"name": "Practice Partner", "company_id": self.student_company.id}
        )
        run.action_run()

        self.assertFalse(first_result_ids.intersection(run.result_ids.ids))
        self.assertEqual(
            run.result_ids.filtered(
                lambda result: result.criterion_id.external_id == "partner_created"
            ).status,
            "passed",
        )
        execution_messages = run.message_ids.filtered(
            lambda message: "Evaluation executed on" in message.body
        )
        self.assertEqual(len(execution_messages), 2)

        run.action_lock()
        self.assertTrue(run.locked)
        with self.assertRaisesRegex(ValidationError, "Locked grading runs"):
            run.action_run()

    def test_student_only_sees_published_company_evaluations(self):
        rubric = self._import_rubric()
        rubric.action_approve()
        run = self.env["practice.grading.run"].create(
            {"rubric_id": rubric.id, "company_ids": [(6, 0, self.student_company.ids)]}
        )
        run.action_run()
        student_group = self.env.ref(
            "practice_grading.group_practice_grading_student"
        )
        student = self.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": "Practice Student",
                "login": "practice.student",
                "email": "practice.student@example.com",
                "company_id": self.student_company.id,
                "company_ids": [(6, 0, self.student_company.ids)],
                "group_ids": [(6, 0, student_group.ids)],
            }
        )
        manager_group = self.env.ref(
            "practice_grading.group_practice_grading_manager"
        )
        manager = self.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": "Practice Grading Manager",
                "login": "practice.grading.manager",
                "email": "practice.grading.manager@example.com",
                "company_id": self.student_company.id,
                "company_ids": [(6, 0, self.student_company.ids)],
                "group_ids": [
                    (6, 0, (manager_group | self.env.ref("base.group_user")).ids)
                ],
            }
        )

        student_runs = self.env["practice.grading.run"].with_user(student)
        student_results = self.env["practice.grading.result"].with_user(student)
        manager_run = run.with_user(manager)
        self.assertEqual(student_runs.search_count([]), 0)
        self.assertEqual(student_results.search_count([]), 0)

        manager_run.action_publish()
        self.assertEqual(student_runs.search_count([]), 1)
        self.assertEqual(student_results.search_count([]), 2)
        visible_results = student_results.search([])
        self.assertEqual(
            set(visible_results.mapped("criterion_name")),
            {"Partner created", "Explanation"},
        )
        self.assertEqual(
            len(
                visible_results.read(
                    ["criterion_name", "criterion_description", "evidence"]
                )
            ),
            2,
        )
        result_action = student_runs.browse(run.id).action_view_my_results()
        self.assertIn(("company_id", "in", self.student_company.ids), result_action["domain"])

        manager_run.action_unpublish()
        self.assertEqual(student_runs.search_count([]), 0)
        self.assertEqual(student_results.search_count([]), 0)

        manager_run.action_publish()
        manager_run.action_run()
        self.assertEqual(run.publication_state, "unpublished")
        self.assertEqual(student_runs.search_count([]), 0)