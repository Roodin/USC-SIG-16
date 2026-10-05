# See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PracticeGradingRun(models.Model):
    _name = "practice.grading.run"
    _description = "Practice Grading Run"
    _order = "create_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    rubric_id = fields.Many2one(
        "practice.rubric", required=True, ondelete="restrict", readonly=True
    )
    company_ids = fields.Many2many(
        "res.company",
        string="Student Companies",
        required=True,
        domain=[("is_student_company", "=", True)],
        default=lambda self: self.env["res.company"].search(
            [("is_student_company", "=", True)]
        ),
        readonly=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("running", "Running"), ("done", "Done")],
        default="draft",
        required=True,
        readonly=True,
    )
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    rubric_snapshot = fields.Json(readonly=True)
    result_ids = fields.One2many("practice.grading.result", "run_id", readonly=True)
    summary_ids = fields.One2many("practice.grading.summary", "run_id", readonly=True)
    total_score = fields.Float(compute="_compute_scores", store=True)
    total_weight = fields.Float(compute="_compute_scores", store=True)

    @api.depends("rubric_id.name", "rubric_id.version", "create_date")
    def _compute_name(self):
        for run in self:
            run.name = _("%(rubric)s (%(version)s)") % {
                "rubric": run.rubric_id.name or "",
                "version": run.rubric_id.version or "",
            }

    @api.depends("result_ids.awarded_points", "result_ids.weight")
    def _compute_scores(self):
        for run in self:
            run.total_score = sum(run.result_ids.mapped("awarded_points"))
            run.total_weight = sum(run.result_ids.mapped("weight"))

    @api.constrains("company_ids")
    def _check_student_companies(self):
        for run in self:
            if not run.company_ids:
                raise ValidationError(_("Select at least one student company."))
            if any(not company.is_student_company for company in run.company_ids):
                raise ValidationError(_("Only student companies can be evaluated."))

    def action_run(self):
        for run in self:
            if run.state != "draft":
                raise ValidationError(_("Only draft grading runs can be executed."))
            if run.rubric_id.state != "approved":
                raise ValidationError(_("Only approved rubrics can be executed."))
            run._check_student_companies()
            run.write(
                {
                    "state": "running",
                    "started_at": fields.Datetime.now(),
                    "rubric_snapshot": run._get_rubric_snapshot(),
                }
            )
            result_values = []
            for company in run.company_ids:
                for criterion in run.rubric_id.criterion_ids:
                    result_values.append(run._evaluate_criterion(company, criterion))
            self.env["practice.grading.result"].create(result_values)
            run._ensure_summaries()
            run.write({"state": "done", "finished_at": fields.Datetime.now()})

    def action_export_csv(self):
        self.ensure_one()
        if self.state != "done":
            raise ValidationError(_("Only completed grading runs can be exported."))
        export = self.env["practice.grading.export.wizard"].create(
            {"run_id": self.id}
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": export._name,
            "res_id": export.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_results(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "practice_grading.action_practice_grading_result"
        )
        action["domain"] = [("run_id", "=", self.id)]
        action["context"] = {"search_default_group_by_company": 1}
        return action

    def action_view_student_grades(self):
        self.ensure_one()
        self._ensure_summaries()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "practice_grading.action_practice_grading_summary"
        )
        action["domain"] = [("run_id", "=", self.id)]
        return action

    def _ensure_summaries(self):
        summary_model = self.env["practice.grading.summary"]
        for run in self:
            existing_company_ids = set(run.summary_ids.mapped("company_id").ids)
            values = [
                {"run_id": run.id, "company_id": company.id}
                for company in run.company_ids
                if company.id not in existing_company_ids
            ]
            if values:
                summary_model.create(values)

    def write(self, values):
        if any(run.state != "draft" for run in self):
            allowed_values = {"state", "started_at", "finished_at", "rubric_snapshot"}
            if set(values) - allowed_values:
                raise UserError(_("Completed grading runs cannot be modified."))
        return super().write(values)

    def _get_rubric_snapshot(self):
        self.ensure_one()
        return {
            "name": self.rubric_id.name,
            "version": self.rubric_id.version,
            "schema_version": self.rubric_id.schema_version,
            "source_checksum": self.rubric_id.source_checksum,
            "criteria": [
                {
                    "id": criterion.external_id,
                    "name": criterion.name,
                    "weight": criterion.weight,
                    "mode": criterion.mode,
                    "verifier": criterion.verifier_config,
                }
                for criterion in self.rubric_id.criterion_ids
            ],
        }

    def _evaluate_criterion(self, company, criterion):
        values = {
            "run_id": self.id,
            "company_id": company.id,
            "criterion_id": criterion.id,
            "weight": criterion.weight,
        }
        if criterion.mode == "manual":
            values.update(
                {
                    "status": "manual_review",
                    "evidence": {"reason": "This criterion requires instructor review."},
                }
            )
            return values
        try:
            status, evidence = self._execute_verifier(company, criterion)
            values.update(
                {
                    "status": status,
                    "automatic_points": criterion.weight if status == "passed" else 0.0,
                    "evidence": evidence,
                }
            )
        except (ValidationError, ValueError) as error:
            values.update(
                {
                    "status": "error",
                    "evidence": {"error": str(error)},
                }
            )
        return values

    def _execute_verifier(self, company, criterion):
        self.ensure_one()
        config = criterion.verifier_config
        model = self.env[config["model"]].sudo().with_company(company)
        domain = list(config["domain"])
        domain.append(["company_id", "=", company.id])
        records = model.search(domain)
        evidence = {
            "model": config["model"],
            "domain": domain,
            "count": len(records),
            "record_ids": records.ids,
        }
        if criterion.verifier_type == "record_exists":
            return ("passed" if records else "failed", evidence)
        if criterion.verifier_type == "record_count":
            evidence["minimum"] = config.get("minimum", 1)
            status = "passed" if len(records) >= evidence["minimum"] else "failed"
            return status, evidence
        if criterion.verifier_type == "field_equals":
            matching_records = records.filtered(
                lambda record: record[config["field"]] == config["value"]
            )
            evidence["field"] = config["field"]
            evidence["expected_value"] = config["value"]
            evidence["matching_record_ids"] = matching_records.ids
            return ("passed" if matching_records else "failed", evidence)
        raise ValidationError(_("The verifier type is not implemented."))


class PracticeGradingResult(models.Model):
    _name = "practice.grading.result"
    _description = "Practice Grading Result"
    _order = "run_id, company_id, criterion_id"

    run_id = fields.Many2one(
        "practice.grading.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, ondelete="restrict", index=True
    )
    student_identifier = fields.Char(
        related="company_id.student_identifier", store=True, readonly=True
    )
    criterion_id = fields.Many2one(
        "practice.rubric.criterion", required=True, ondelete="restrict", index=True
    )
    status = fields.Selection(
        [
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("manual_review", "Manual Review"),
            ("error", "Error"),
        ],
        required=True,
        readonly=True,
    )
    weight = fields.Float(required=True, readonly=True)
    automatic_points = fields.Float(readonly=True)
    manual_points = fields.Float()
    awarded_points = fields.Float(compute="_compute_awarded_points", store=True)
    has_evidence_link = fields.Boolean(compute="_compute_has_evidence_link")
    review_note = fields.Text()
    evidence = fields.Json(readonly=True)

    _run_company_criterion_unique = models.Constraint(
        "UNIQUE (run_id, company_id, criterion_id)",
        "A company can only have one result per criterion in a grading run.",
    )

    @api.depends("automatic_points", "manual_points")
    def _compute_awarded_points(self):
        for result in self:
            result.awarded_points = result.automatic_points + result.manual_points

    @api.depends("status", "evidence")
    def _compute_has_evidence_link(self):
        for result in self:
            evidence = result.evidence or {}
            result.has_evidence_link = bool(
                result.status == "passed"
                and evidence.get("model")
                and (evidence.get("matching_record_ids") or evidence.get("record_ids"))
            )

    def action_view_evidence(self):
        self.ensure_one()
        evidence = self.evidence or {}
        model_name = evidence.get("model")
        record_ids = evidence.get("matching_record_ids") or evidence.get(
            "record_ids", []
        )
        if not model_name or model_name not in self.env or not record_ids:
            raise UserError(_("This result does not have navigable evidence."))
        records = self.env[model_name].browse(record_ids).exists()
        if not records:
            raise UserError(_("The evidence records no longer exist."))
        if len(records) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": model_name,
                "res_id": records.id,
                "view_mode": "form",
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Evidence"),
            "res_model": model_name,
            "view_mode": "list,form",
            "domain": [("id", "in", records.ids)],
            "target": "current",
        }

    @api.constrains("manual_points", "weight", "status")
    def _check_manual_points(self):
        for result in self:
            if result.status != "manual_review" and result.manual_points:
                raise ValidationError(_("Manual points can only be assigned to manual criteria."))
            if result.manual_points < 0 or result.manual_points > result.weight:
                raise ValidationError(_("Manual points must be between zero and the weight."))

    def write(self, values):
        protected_values = set(values) - {"manual_points", "review_note"}
        if protected_values:
            raise UserError(_("Only manual points and the review note can be modified."))
        if "manual_points" in values and any(
            result.status != "manual_review" for result in self
        ):
            raise UserError(_("Only manual-review results accept manual points."))
        return super().write(values)


class PracticeGradingSummary(models.Model):
    _name = "practice.grading.summary"
    _description = "Practice Grading Student Summary"
    _order = "run_id, company_id"

    run_id = fields.Many2one(
        "practice.grading.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, ondelete="restrict", index=True
    )
    student_identifier = fields.Char(
        related="company_id.student_identifier", store=True, readonly=True
    )
    earned_points = fields.Float(compute="_compute_scores", store=True)
    possible_points = fields.Float(compute="_compute_scores", store=True)
    final_score = fields.Float(compute="_compute_scores", store=True)

    _run_company_unique = models.Constraint(
        "UNIQUE (run_id, company_id)",
        "A grading run can only have one summary per company.",
    )

    @api.depends(
        "run_id.result_ids.awarded_points",
        "run_id.result_ids.company_id",
        "run_id.rubric_id.total_weight",
    )
    def _compute_scores(self):
        for summary in self:
            results = summary.run_id.result_ids.filtered(
                lambda result: result.company_id == summary.company_id
            )
            automatic_results = results.filtered(
                lambda result: result.criterion_id.mode == "automatic"
            )
            summary.earned_points = sum(automatic_results.mapped("awarded_points"))
            summary.possible_points = sum(automatic_results.mapped("weight"))
            if summary.possible_points:
                summary.final_score = 10 * summary.earned_points / summary.possible_points
            else:
                summary.final_score = 0.0

    def action_view_results(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "practice_grading.action_practice_grading_result"
        )
        action["domain"] = [
            ("run_id", "=", self.run_id.id),
            ("company_id", "=", self.company_id.id),
        ]
        return action