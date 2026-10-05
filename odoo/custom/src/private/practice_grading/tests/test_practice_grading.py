# See LICENSE file for full copyright and licensing details.

import base64
import json

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestPracticeGrading(TransactionCase):
    def setUp(self):
        super().setUp()
        self.student_company = self.env["res.company"].create(
            {"name": "Student Company", "is_student_company": True}
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