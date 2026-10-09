# See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import json
import math

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
    "workflow_linked",
    "hierarchical_locations",
    "putaway_rule",
    "stock_move_destination",
    "serial_receipt",
    "serial_delivery",
    "supplier_offer",
    "linked_purchase_receipt",
    "linked_return",
    "return_same_serial",
    "product_removal_configuration",
    "sequenced_lot_receipts",
    "lot_delivery_split",
    "fefo_receipts_and_dates",
}
VERIFIER_FIELDS = {
    "record_exists": ({"model", "domain"}, {"model", "domain"}),
    "record_count": ({"model", "domain", "minimum"}, {"model", "domain"}),
    "field_equals": (
        {"model", "domain", "field", "value"},
        {"model", "domain", "field", "value"},
    ),
    "workflow_linked": (
        {"workflow", "product_name", "product_code", "required_purchase_state", "purchase_origin"},
        {"workflow"},
    ),
    "hierarchical_locations": (
        {"parent_kind", "pasillos", "estanterias", "columnas", "alturas", "usage", "expected_leaf_count"},
        {"parent_kind", "pasillos", "estanterias", "columnas", "alturas", "usage", "expected_leaf_count"},
    ),
    "putaway_rule": (
        {"product_name", "product_code", "target_path"},
        {"target_path"},
    ),
    "stock_move_destination": (
        {"product_name", "product_code", "quantity", "destination_path", "state", "picking_type"},
        {"quantity", "destination_path", "state", "picking_type"},
    ),
    "serial_receipt": (
        {"product_code", "minimum_serials", "unique", "state", "destination_path"},
        {"product_code", "minimum_serials", "unique", "state", "destination_path"},
    ),
    "serial_delivery": (
        {"product_code", "quantity", "require_distinct", "require_related_sale"},
        {"product_code", "quantity", "require_distinct", "require_related_sale"},
    ),
    "supplier_offer": (
        {"product_name", "product_code", "vendor_ref", "price", "min_qty", "delay"},
        {"vendor_ref", "price", "min_qty", "delay"},
    ),
    "linked_purchase_receipt": (
        {"product_name", "product_code", "vendor_ref", "qty", "unit_price", "receipt_state"},
        {"vendor_ref", "qty", "unit_price", "receipt_state"},
    ),
    "linked_return": (
        {"product_code", "quantity", "state", "original_sale_qty"},
        {"product_code", "quantity", "state", "original_sale_qty"},
    ),
    "return_same_serial": (
        {"product_code", "returned_quantity", "must_match_original_delivery", "internal_destination"},
        {"product_code", "returned_quantity", "must_match_original_delivery", "internal_destination"},
    ),
    "product_removal_configuration": (
        {"product_code", "tracking", "removal_strategy", "expiration_enabled"},
        {"product_code", "tracking", "removal_strategy"},
    ),
    "sequenced_lot_receipts": (
        {"product_code", "lots"},
        {"product_code", "lots"},
    ),
    "lot_delivery_split": (
        {"product_code", "qty_total", "lot_quantities", "require_related_sale", "state"},
        {"product_code", "qty_total", "lot_quantities", "require_related_sale", "state"},
    ),
    "fefo_receipts_and_dates": (
        {"product_code", "lots", "date_order", "arrival_order", "removal_dates_must_be_future"},
        {"product_code", "lots", "date_order", "arrival_order", "removal_dates_must_be_future"},
    ),
}


class PracticeRubric(models.Model):
    _name = "practice.rubric"
    _description = "Practice Grading Rubric"
    _order = "name, version desc, id desc"

    name = fields.Char(required=True)
    version = fields.Char(required=True, default="1.0")
    description = fields.Text()
    evaluation_scope = fields.Json()
    notes = fields.Json()
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
            "evaluation_scope",
            "notes",
            "pass_score",
            "total_weight",
            "criteria",
        }
        unexpected_keys = set(payload) - allowed_keys
        if unexpected_keys:
            raise ValidationError(
                _("Unsupported rubric fields: %s") % ", ".join(sorted(unexpected_keys))
            )
        schema_version = payload.get("schema_version")
        if schema_version not in {"1.0", "1.1"}:
            raise ValidationError(_("Only rubric schema versions 1.0 and 1.1 are supported."))
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
        declared_total_weight = payload.get("total_weight")
        if schema_version == "1.1" and declared_total_weight is None:
            raise ValidationError(_("Rubric schema 1.1 requires total_weight."))
        if declared_total_weight is not None:
            if (
                not isinstance(declared_total_weight, (int, float))
                or isinstance(declared_total_weight, bool)
                or not math.isclose(declared_total_weight, total_weight, rel_tol=1e-9)
            ):
                raise ValidationError(
                    _("The total weight must match the sum of criterion weights.")
                )
        evaluation_scope = payload.get("evaluation_scope")
        if schema_version == "1.1":
            self._validate_evaluation_scope(evaluation_scope)
            notes = payload.get("notes", [])
            if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
                raise ValidationError(_("Schema 1.1 notes must be a list of strings."))
        else:
            for field_name in ("evaluation_scope", "notes"):
                value = payload.get(field_name)
                if value is not None and not isinstance(value, str):
                    raise ValidationError(
                        _("The %(field)s field must be a string.") % {"field": field_name}
                    )
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
        allowed_fields, required_fields = VERIFIER_FIELDS[verifier_type]
        unexpected_fields = set(verifier) - (allowed_fields | {"type"})
        missing_fields = required_fields - set(verifier)
        if unexpected_fields:
            raise ValidationError(
                _("Unsupported verifier fields: %s")
                % ", ".join(sorted(unexpected_fields))
            )
        if missing_fields:
            raise ValidationError(
                _("Missing verifier fields: %s") % ", ".join(sorted(missing_fields))
            )
        if verifier_type in {"record_exists", "record_count", "field_equals"}:
            model_name = verifier.get("model")
            if not isinstance(model_name, str) or model_name not in self.env:
                raise ValidationError(_("The verifier model is not available."))
            model = self.env[model_name]
            if not self._has_company_scope(model):
                raise ValidationError(
                    _("Automatic verifier models must be company-scoped.")
                )
            self._validate_domain(verifier.get("domain"), model)
            if verifier_type == "field_equals":
                self._validate_field_path(verifier["field"], model)
        if verifier_type == "record_count":
            minimum = verifier.get("minimum", 1)
            if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
                raise ValidationError(_("The minimum record count must be a positive integer."))
        self._validate_composite_verifier(verifier_type, verifier)

    @api.model
    def _has_company_scope(self, model):
        return any(
            self._field_from_path(path, model)
            for path in ("company_id", "product_id.company_id", "product_tmpl_id.company_id")
        )

    @api.model
    def _field_from_path(self, path, model):
        current_model = model
        field = None
        parts = path.split(".")
        for index, part in enumerate(parts):
            field = current_model._fields.get(part)
            if not field:
                return None
            if index < len(parts) - 1:
                if not field.relational:
                    return None
                current_model = self.env[field.comodel_name]
        return field

    @api.model
    def _validate_field_path(self, field_path, model):
        if not isinstance(field_path, str) or not self._field_from_path(field_path, model):
            raise ValidationError(
                _("The field '%(field)s' is not available on model '%(model)s'.")
                % {"field": field_path, "model": model._name}
            )

    @api.model
    def _validate_evaluation_scope(self, scope):
        expected = {
            "company": "assigned_student_company",
            "records": "student_database",
        }
        if not isinstance(scope, dict):
            raise ValidationError(_("Schema 1.1 evaluation_scope must be an object."))
        if set(scope) != {"company", "records", "exclude_demo_data"}:
            raise ValidationError(_("The schema 1.1 evaluation_scope fields are invalid."))
        if any(scope.get(key) != value for key, value in expected.items()):
            raise ValidationError(_("The evaluation scope must use the assigned student company and database."))
        if not isinstance(scope.get("exclude_demo_data"), bool):
            raise ValidationError(_("exclude_demo_data must be a boolean."))

    @api.model
    def _validate_composite_verifier(self, verifier_type, verifier):
        def require_product_selector():
            if bool(verifier.get("product_name")) == bool(verifier.get("product_code")):
                raise ValidationError(_("Specify exactly one of product_name or product_code."))

        def require_positive_number(key):
            value = verifier.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValidationError(_("The %(field)s field must be a positive number.") % {"field": key})

        if verifier_type == "workflow_linked":
            require_product_selector()
            if verifier["workflow"] not in {"replenishment_purchase", "purchase_receipt_done"}:
                raise ValidationError(_("The linked workflow is not supported."))
            states = verifier.get("required_purchase_state")
            if states is not None and (
                not isinstance(states, list)
                or not states
                or any(state not in {"purchase", "done"} for state in states)
            ):
                raise ValidationError(_("required_purchase_state contains an unsupported state."))
            if verifier.get("purchase_origin") not in {None, "replenishment"}:
                raise ValidationError(_("The purchase origin is not supported."))
        elif verifier_type == "hierarchical_locations":
            if verifier["parent_kind"] != "warehouse_stock" or verifier["usage"] != "internal":
                raise ValidationError(_("Only internal warehouse stock locations are supported."))
            for key in ("pasillos", "estanterias", "columnas", "alturas"):
                values = verifier[key]
                if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
                    raise ValidationError(_("%(field)s must be a non-empty list of names.") % {"field": key})
            require_positive_number("expected_leaf_count")
        elif verifier_type == "putaway_rule":
            if bool(verifier.get("product_name")) == bool(verifier.get("product_code")):
                raise ValidationError(_("Specify exactly one product_name or product_code."))
            self._validate_location_path(verifier["target_path"])
        elif verifier_type == "stock_move_destination":
            require_product_selector()
            require_positive_number("quantity")
            self._validate_location_path(verifier["destination_path"])
            if verifier["state"] != "done" or verifier["picking_type"] not in {"incoming", "outgoing", "internal"}:
                raise ValidationError(_("The stock move state or picking type is not supported."))
        elif verifier_type == "serial_receipt":
            require_positive_number("minimum_serials")
            if verifier["state"] != "done" or verifier["unique"] is not True:
                raise ValidationError(_("Serial receipts must be done and require unique serials."))
            self._validate_location_path(verifier["destination_path"])
        elif verifier_type == "serial_delivery":
            require_positive_number("quantity")
            if not isinstance(verifier["require_distinct"], bool) or not isinstance(verifier["require_related_sale"], bool):
                raise ValidationError(_("Serial delivery flags must be booleans."))
        elif verifier_type == "supplier_offer":
            require_product_selector()
            require_positive_number("price")
            require_positive_number("min_qty")
            require_positive_number("delay")
        elif verifier_type == "linked_purchase_receipt":
            require_product_selector()
            require_positive_number("qty")
            require_positive_number("unit_price")
            if verifier["receipt_state"] != "done":
                raise ValidationError(_("Linked purchase receipts must be done."))
        elif verifier_type == "linked_return":
            require_positive_number("quantity")
            require_positive_number("original_sale_qty")
            if verifier["state"] != "done":
                raise ValidationError(_("Linked returns must be done."))
        elif verifier_type == "return_same_serial":
            require_positive_number("returned_quantity")
            if verifier["must_match_original_delivery"] is not True or verifier["internal_destination"] is not True:
                raise ValidationError(_("Returned serials must match a delivery and return internally."))
        elif verifier_type == "product_removal_configuration":
            if verifier["tracking"] not in {"lot", "serial"} or verifier["removal_strategy"] not in {"fifo", "fefo"}:
                raise ValidationError(_("The tracking or removal strategy is not supported."))
            if "expiration_enabled" in verifier and not isinstance(verifier["expiration_enabled"], bool):
                raise ValidationError(_("expiration_enabled must be a boolean."))
        elif verifier_type in {"sequenced_lot_receipts", "fefo_receipts_and_dates"}:
            lots = verifier["lots"]
            if not isinstance(lots, list) or not lots:
                raise ValidationError(_("At least one lot receipt must be specified."))
            names = set()
            for lot in lots:
                if (
                    not isinstance(lot, dict)
                    or set(lot) != {"name", "qty"}
                    or not isinstance(lot["name"], str)
                    or not lot["name"]
                    or lot["name"] in names
                    or not isinstance(lot["qty"], (int, float))
                    or isinstance(lot["qty"], bool)
                    or lot["qty"] <= 0
                ):
                    raise ValidationError(_("Each lot receipt requires a unique name and positive quantity."))
                names.add(lot["name"])
            if verifier_type == "fefo_receipts_and_dates":
                if verifier["date_order"] != "B_before_A" or verifier["arrival_order"] != "A_before_B":
                    raise ValidationError(_("The FEFO date and arrival order are not supported."))
                if verifier["removal_dates_must_be_future"] is not True:
                    raise ValidationError(_("FEFO removal dates must be required to be in the future."))
        elif verifier_type == "lot_delivery_split":
            require_positive_number("qty_total")
            lot_quantities = verifier["lot_quantities"]
            if (
                not isinstance(lot_quantities, dict)
                or not lot_quantities
                or any(not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 for value in lot_quantities.values())
                or not math.isclose(sum(lot_quantities.values()), verifier["qty_total"], rel_tol=1e-9)
            ):
                raise ValidationError(_("Lot quantities must be positive and sum to qty_total."))
            if verifier["require_related_sale"] is not True or verifier["state"] != "done":
                raise ValidationError(_("Lot deliveries must be done and linked to a sale."))

    @api.model
    def _validate_location_path(self, path):
        if not isinstance(path, str) or not path or any(not part for part in path.split("/")):
            raise ValidationError(_("A location path must contain slash-separated names."))

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
            self._validate_field_path(field_name, model)
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
                "evaluation_scope": payload.get("evaluation_scope"),
                "notes": payload.get("notes"),
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