# See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


ALLOWED_DOMAIN_OPERATORS = {
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not in",
}
VERIFIER_TYPES = {
    "record_exists",
    "record_count",
    "field_equals",
}


class PracticeRubric(models.Model):
    _name = "practice.rubric"
    _description = "Practice Grading Rubric"
    _order = "name, version desc, id desc"

    name = fields.Char(required=True)
    version = fields.Char(required=True, default="1.0")
    description = fields.Text()
    schema_version = fields.Char(required=True, default="1.0", readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("archived", "Archived"),
        ],
        default="draft",
        required=True,
        readonly=True,
    )
    pass_score = fields.Float(default=0.0)
    criterion_ids = fields.One2many(
        "practice.rubric.criterion", "rubric_id", string="Criteria"
    )
    total_weight = fields.Float(compute="_compute_total_weight", store=True)
    source_filename = fields.Char(readonly=True)
    source_checksum = fields.Char(readonly=True)
    source_file = fields.Binary(attachment=True, readonly=True)

    @api.depends("criterion_ids.weight")
    def _compute_total_weight(self):
        for rubric in self:
            rubric.total_weight = sum(rubric.criterion_ids.mapped("weight"))

    @api.constrains("pass_score", "total_weight")
    def _check_scores(self):
        for rubric in self:
            if rubric.pass_score < 0:
                raise ValidationError(_("The pass score cannot be negative."))
            if rubric.pass_score > rubric.total_weight:
                raise ValidationError(
                    _("The pass score cannot exceed the total criterion weight.")
                )

    def action_approve(self):
        for rubric in self:
            if not rubric.criterion_ids:
                raise ValidationError(_("A rubric requires at least one criterion."))
            rubric._check_scores()
            rubric.with_context(practice_grading_state_action=True).state = "approved"

    def action_archive(self):
        self.with_context(practice_grading_state_action=True).write({"state": "archived"})

    def write(self, values):
        protected_values = set(values) - {"state"}
        if protected_values and any(rubric.state != "draft" for rubric in self):
            raise UserError(_("Only draft rubrics can be modified."))
        if "state" in values and not self.env.context.get("practice_grading_state_action"):
            raise UserError(_("Use the available actions to change a rubric state."))
        return super().write(values)

    @api.model
    def validate_import_payload(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError(_("The rubric file must contain a JSON object."))
        allowed_keys = {
            "schema_version",
            "name",
            "version",
            "description",
            "pass_score",
            "criteria",
        }
        unexpected_keys = set(payload) - allowed_keys
        if unexpected_keys:
            raise ValidationError(
                _("Unsupported rubric fields: %s") % ", ".join(sorted(unexpected_keys))
            )
        if payload.get("schema_version") != "1.0":
            raise ValidationError(_("Only rubric schema version 1.0 is supported."))
        if not isinstance(payload.get("name"), str) or not payload["name"].strip():
            raise ValidationError(_("The rubric name is required."))
        version = payload.get("version", "1.0")
        if not isinstance(version, str) or not version.strip():
            raise ValidationError(_("The rubric version must be a non-empty string."))
        criteria = payload.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ValidationError(_("The rubric must include at least one criterion."))
        criterion_ids = set()
        total_weight = 0.0
        for criterion in criteria:
            self._validate_criterion_payload(criterion, criterion_ids)
            total_weight += criterion["weight"]
        pass_score = payload.get("pass_score", 0.0)
        if not isinstance(pass_score, (int, float)) or isinstance(pass_score, bool):
            raise ValidationError(_("The pass score must be a number."))
        if pass_score < 0 or pass_score > total_weight:
            raise ValidationError(
                _("The pass score must be between zero and the total criterion weight.")
            )

    @api.model
    def _validate_criterion_payload(self, criterion, criterion_ids):
        if not isinstance(criterion, dict):
            raise ValidationError(_("Each criterion must be a JSON object."))
        allowed_keys = {"id", "name", "description", "weight", "mode", "verifier"}
        unexpected_keys = set(criterion) - allowed_keys
        if unexpected_keys:
            raise ValidationError(
                _("Unsupported criterion fields: %s")
                % ", ".join(sorted(unexpected_keys))
            )
        identifier = criterion.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValidationError(_("Each criterion requires an identifier."))
        if identifier in criterion_ids:
            raise ValidationError(_("Criterion identifiers must be unique."))
        criterion_ids.add(identifier)
        if not isinstance(criterion.get("name"), str) or not criterion["name"].strip():
            raise ValidationError(_("Each criterion requires a name."))
        weight = criterion.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise ValidationError(_("Each criterion weight must be greater than zero."))
        mode = criterion.get("mode")
        if mode not in {"automatic", "manual"}:
            raise ValidationError(_("The criterion mode must be automatic or manual."))
        verifier = criterion.get("verifier")
        if mode == "automatic":
            self._validate_verifier_payload(verifier)
        elif verifier is not None:
            raise ValidationError(_("Manual criteria cannot define a verifier."))

    @api.model
    def _validate_verifier_payload(self, verifier):
        if not isinstance(verifier, dict):
            raise ValidationError(_("Automatic criteria require a verifier object."))
        verifier_type = verifier.get("type")
        if verifier_type not in VERIFIER_TYPES:
            raise ValidationError(_("The verifier type is not supported."))
        model_name = verifier.get("model")
        if not isinstance(model_name, str) or model_name not in self.env:
            raise ValidationError(_("The verifier model is not available."))
        model = self.env[model_name]
        if "company_id" not in model._fields:
            raise ValidationError(
                _("Automatic verifier models must have a company_id field.")
            )
        domain = verifier.get("domain")
        self._validate_domain(domain, model)
        if verifier_type == "field_equals":
            field_name = verifier.get("field")
            if field_name not in model._fields:
                raise ValidationError(_("The verifier field is not available."))
            if "value" not in verifier:
                raise ValidationError(_("The field verifier requires a value."))
        if verifier_type == "record_count":
            minimum = verifier.get("minimum", 1)
            if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
                raise ValidationError(_("The minimum record count must be a positive integer."))

    @api.model
    def _validate_domain(self, domain, model):
        if not isinstance(domain, list):
            raise ValidationError(_("The verifier domain must be a list."))
        for condition in domain:
            if not isinstance(condition, list) or len(condition) != 3:
                raise ValidationError(
                    _("Each verifier domain condition must contain three values.")
                )
            field_name, operator, value = condition
            if field_name not in model._fields:
                raise ValidationError(_("The domain field is not available."))
            if operator not in ALLOWED_DOMAIN_OPERATORS:
                raise ValidationError(_("The domain operator is not supported."))
            if isinstance(value, (dict, tuple)):
                raise ValidationError(_("The domain value must be a JSON value."))


class PracticeRubricCriterion(models.Model):
    _name = "practice.rubric.criterion"
    _description = "Practice Grading Rubric Criterion"
    _order = "rubric_id, sequence, id"

    rubric_id = fields.Many2one(
        "practice.rubric", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    external_id = fields.Char(required=True, readonly=True)
    name = fields.Char(required=True)
    description = fields.Text()
    weight = fields.Float(required=True)
    mode = fields.Selection(
        [("automatic", "Automatic"), ("manual", "Manual")], required=True
    )
    verifier_type = fields.Selection(
        [(value, value.replace("_", " ").title()) for value in sorted(VERIFIER_TYPES)]
    )
    verifier_config = fields.Json()

    _external_id_unique = models.Constraint(
        "UNIQUE (rubric_id, external_id)",
        "A rubric criterion identifier must be unique.",
    )

    @api.model_create_multi
    def create(self, values_list):
        rubric_ids = [values["rubric_id"] for values in values_list if values.get("rubric_id")]
        rubrics = self.env["practice.rubric"].browse(rubric_ids)
        if any(rubric.state != "draft" for rubric in rubrics):
            raise UserError(_("Criteria can only be added to draft rubrics."))
        return super().create(values_list)

    def write(self, values):
        if any(criterion.rubric_id.state != "draft" for criterion in self):
            raise UserError(_("Criteria can only be modified on draft rubrics."))
        return super().write(values)

    def unlink(self):
        if any(criterion.rubric_id.state != "draft" for criterion in self):
            raise UserError(_("Criteria can only be removed from draft rubrics."))
        return super().unlink()

    @api.constrains("weight", "mode", "verifier_type", "verifier_config")
    def _check_criterion(self):
        for criterion in self:
            if criterion.weight <= 0:
                raise ValidationError(_("The criterion weight must be greater than zero."))
            if criterion.mode == "automatic" and not criterion.verifier_type:
                raise ValidationError(_("Automatic criteria require a verifier type."))
            if criterion.mode == "manual" and criterion.verifier_type:
                raise ValidationError(_("Manual criteria cannot define a verifier."))


class PracticeRubricImportWizard(models.TransientModel):
    _name = "practice.rubric.import.wizard"
    _description = "Practice Rubric Import"

    rubric_file = fields.Binary(required=True)
    rubric_filename = fields.Char(required=True)
    preview = fields.Text(readonly=True)
    error_message = fields.Text(readonly=True)

    def _load_payload(self):
        self.ensure_one()
        try:
            raw_content = base64.b64decode(self.rubric_file)
            return json.loads(raw_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError(_("The file must be valid UTF-8 JSON: %s") % error) from error

    def action_validate(self):
        self.ensure_one()
        try:
            payload = self._load_payload()
            self.env["practice.rubric"].validate_import_payload(payload)
        except ValidationError as error:
            self.write({"preview": False, "error_message": str(error)})
            return self._reopen()
        preview = _("Rubric: %(name)s\nCriteria: %(count)s\nTotal weight: %(weight)s") % {
            "name": payload["name"],
            "count": len(payload["criteria"]),
            "weight": sum(item["weight"] for item in payload["criteria"]),
        }
        self.write({"preview": preview, "error_message": False})
        return self._reopen()

    def action_import(self):
        self.ensure_one()
        payload = self._load_payload()
        rubric_model = self.env["practice.rubric"]
        rubric_model.validate_import_payload(payload)
        raw_content = base64.b64decode(self.rubric_file)
        rubric = rubric_model.create(
            {
                "name": payload["name"].strip(),
                "version": payload.get("version", "1.0").strip(),
                "description": payload.get("description"),
                "schema_version": payload["schema_version"],
                "source_filename": self.rubric_filename,
                "source_file": self.rubric_file,
                "source_checksum": hashlib.sha256(raw_content).hexdigest(),
            }
        )
        criterion_values = []
        for sequence, criterion in enumerate(payload["criteria"], start=1):
            verifier = criterion.get("verifier") or {}
            criterion_values.append(
                {
                    "rubric_id": rubric.id,
                    "sequence": sequence,
                    "external_id": criterion["id"],
                    "name": criterion["name"],
                    "description": criterion.get("description"),
                    "weight": criterion["weight"],
                    "mode": criterion["mode"],
                    "verifier_type": verifier.get("type"),
                    "verifier_config": verifier or False,
                }
            )
        self.env["practice.rubric.criterion"].create(criterion_values)
        rubric.write({"pass_score": payload.get("pass_score", 0.0)})
        return {
            "type": "ir.actions.act_window",
            "res_model": "practice.rubric",
            "res_id": rubric.id,
            "view_mode": "form",
            "target": "current",
        }

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }