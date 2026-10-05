# See LICENSE file for full copyright and licensing details.

import base64
import csv
from io import StringIO

from odoo import fields, models


class PracticeGradingExportWizard(models.TransientModel):
    _name = "practice.grading.export.wizard"
    _description = "Practice Grading Export"

    run_id = fields.Many2one("practice.grading.run", required=True, readonly=True)
    export_file = fields.Binary(compute="_compute_export_file")
    export_filename = fields.Char(compute="_compute_export_file")

    def _compute_export_file(self):
        for wizard in self:
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(
                [
                    "company_id",
                    "company_name",
                    "criterion_id",
                    "criterion_name",
                    "status",
                    "weight",
                    "automatic_points",
                    "manual_points",
                    "awarded_points",
                    "review_note",
                ]
            )
            for result in wizard.run_id.result_ids:
                writer.writerow(
                    [
                        result.company_id.id,
                        result.company_id.display_name,
                        result.criterion_id.external_id,
                        result.criterion_id.name,
                        result.status,
                        result.weight,
                        result.automatic_points,
                        result.manual_points,
                        result.awarded_points,
                        result.review_note or "",
                    ]
                )
            wizard.export_file = base64.b64encode(buffer.getvalue().encode("utf-8"))
            wizard.export_filename = "grading-run-%s.csv" % wizard.run_id.id