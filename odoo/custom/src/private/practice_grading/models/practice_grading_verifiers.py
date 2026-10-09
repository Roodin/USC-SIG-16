# See LICENSE file for full copyright and licensing details.

from itertools import product

from odoo import _, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class PracticeGradingRun(models.Model):
    _inherit = "practice.grading.run"

    def _scoped_search(self, company, model_name, domain, order=None):
        model = self.env[model_name].sudo().with_company(company).with_context(
            allowed_company_ids=[company.id]
        )
        domain = list(domain) + self._get_company_domain(model_name, company)
        return model.search(domain, order=order)

    def _find_products(self, company, config):
        if config.get("product_code"):
            domain = [("default_code", "=", config["product_code"])]
        else:
            domain = [("name", "=", config["product_name"])]
        return self._scoped_search(company, "product.product", domain)

    def _product_move_line_domain(self, products):
        return [("product_id", "in", products.ids)]

    def _evidence(self, model_name, records, **details):
        return {
            "model": model_name,
            "record_ids": records.ids,
            "count": len(records),
            **details,
        }

    def _matches_quantity(self, actual, expected, product):
        return float_compare(
            actual,
            expected,
            precision_rounding=product.uom_id.rounding,
        ) == 0

    def _warehouse_roots(self, company):
        warehouses = self._scoped_search(company, "stock.warehouse", [])
        return warehouses.mapped("lot_stock_id")

    def _find_location(self, company, path):
        locations = self._warehouse_roots(company)
        for part in path.split("/"):
            locations = self._scoped_search(
                company,
                "stock.location",
                [
                    ("location_id", "in", locations.ids),
                    ("name", "=", part),
                    ("usage", "=", "internal"),
                ],
            )
            if not locations:
                return locations
        return locations

    def _location_path(self, location, root):
        parts = []
        while location and location != root:
            parts.append(location.name)
            location = location.location_id
        return "/".join(reversed(parts)) if location == root else False

    def _execute_verifier_workflow_linked(self, company, config):
        products = self._find_products(company, config)
        if not products:
            return "failed", self._evidence("product.product", products)
        lines = self._scoped_search(
            company,
            "purchase.order.line",
            [("product_id", "in", products.ids)],
        )
        matched_lines = self.env["purchase.order.line"]
        for line in lines:
            moves = line.move_ids
            if (
                config["workflow"] == "replenishment_purchase"
                or config.get("purchase_origin") == "replenishment"
            ) and not line.orderpoint_id:
                continue
            receipts = moves.mapped("picking_id").filtered(
                lambda picking: picking.state == "done"
                and picking.picking_type_id.code == "incoming"
            )
            states = config.get("required_purchase_state", ["purchase"])
            if (
                ("purchase" in states and line.order_id.state == "purchase")
                or ("done" in states and receipts)
                or config["workflow"] == "purchase_receipt_done" and receipts
            ):
                matched_lines |= line
        evidence = self._evidence(
            "purchase.order.line",
            matched_lines,
            workflow=config["workflow"],
            picking_ids=matched_lines.mapped("move_ids.picking_id").ids,
        )
        return ("passed" if matched_lines else "failed", evidence)

    def _execute_verifier_hierarchical_locations(self, company, config):
        expected_paths = [
            "/".join(parts)
            for parts in product(
                config["pasillos"],
                config["estanterias"],
                config["columnas"],
                config["alturas"],
            )
        ]
        location_model = self.env["stock.location"]
        for root in self._warehouse_roots(company):
            descendants = self._scoped_search(
                company,
                "stock.location",
                [("id", "child_of", root.id), ("usage", "=", config["usage"])],
            )
            leaves = descendants.filtered(
                lambda location: not location.child_ids.filtered(
                    lambda child: child.usage == config["usage"]
                )
            )
            paths = {
                self._location_path(location, root): location
                for location in leaves
            }
            found_paths = [path for path in expected_paths if path in paths]
            if (
                len(found_paths) == config["expected_leaf_count"]
                and len(leaves) == config["expected_leaf_count"]
            ):
                records = location_model.browse([paths[path].id for path in found_paths])
                evidence = self._evidence(
                    "stock.location",
                    records,
                    expected_leaf_count=config["expected_leaf_count"],
                    found_leaf_count=len(leaves),
                    paths=found_paths,
                )
                return "passed", evidence
        return "failed", self._evidence(
            "stock.location",
            location_model,
            expected_paths=expected_paths,
            expected_leaf_count=config["expected_leaf_count"],
        )

    def _execute_verifier_putaway_rule(self, company, config):
        products = self._find_products(company, config)
        target = self._find_location(company, config["target_path"])
        rules = self._scoped_search(
            company,
            "stock.putaway.rule",
            [
                ("product_id", "in", products.ids),
                ("location_out_id", "in", target.ids),
                ("active", "=", True),
            ],
        )
        return ("passed" if rules else "failed", self._evidence(
            "stock.putaway.rule",
            rules,
            target_path=config["target_path"],
            product_ids=products.ids,
        ))

    def _execute_verifier_stock_move_destination(self, company, config):
        products = self._find_products(company, config)
        targets = self._find_location(company, config["destination_path"])
        lines = self._scoped_search(
            company,
            "stock.move.line",
            self._product_move_line_domain(products)
            + [
                ("state", "=", config["state"]),
                ("picking_id.picking_type_id.code", "=", config["picking_type"]),
                ("location_dest_id", "in", targets.ids),
            ],
        )
        quantity = sum(lines.mapped("quantity_product_uom"))
        passed = False
        for picking in lines.mapped("picking_id"):
            picking_quantity = sum(
                lines.filtered(lambda line: line.picking_id == picking).mapped(
                    "quantity_product_uom"
                )
            )
            if any(
                self._matches_quantity(picking_quantity, config["quantity"], product)
                for product in products
            ):
                passed = True
                break
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line",
            lines,
            quantity=quantity,
            expected_quantity=config["quantity"],
            destination_ids=targets.ids,
        ))

    def _execute_verifier_serial_receipt(self, company, config):
        products = self._find_products(company, config)
        targets = self._find_location(company, config["destination_path"])
        lines = self._scoped_search(
            company,
            "stock.move.line",
            self._product_move_line_domain(products)
            + [
                ("state", "=", config["state"]),
                ("picking_id.picking_type_id.code", "=", "incoming"),
                ("location_dest_id", "in", targets.ids),
                ("lot_id", "!=", False),
            ],
        )
        lots = lines.mapped("lot_id")
        count = len(set(lots.ids))
        passed = count >= config["minimum_serials"]
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line",
            lines,
            serial_count=count,
            minimum_serials=config["minimum_serials"],
            serial_names=lots.mapped("name"),
        ))

    def _execute_verifier_serial_delivery(self, company, config):
        products = self._find_products(company, config)
        lines = self._scoped_search(
            company,
            "stock.move.line",
            self._product_move_line_domain(products)
            + [
                ("state", "=", "done"),
                ("picking_id.picking_type_id.code", "=", "outgoing"),
                ("lot_id", "!=", False),
            ],
        )
        if config["require_related_sale"]:
            lines = lines.filtered(lambda line: line.move_id.sale_line_id)
        lots = lines.mapped("lot_id")
        quantity = sum(lines.mapped("quantity_product_uom"))
        matching_picking = self.env["stock.picking"]
        for picking in lines.mapped("picking_id"):
            picking_lines = lines.filtered(lambda line: line.picking_id == picking)
            picking_lots = picking_lines.mapped("lot_id")
            distinct_ok = (
                not config["require_distinct"]
                or len(set(picking_lots.ids)) == config["quantity"]
            )
            picking_quantity = sum(picking_lines.mapped("quantity_product_uom"))
            if distinct_ok and any(
                self._matches_quantity(picking_quantity, config["quantity"], product)
                for product in products
            ):
                matching_picking |= picking
        passed = bool(matching_picking)
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line",
            lines,
            quantity=quantity,
            serial_names=lots.mapped("name"),
            require_distinct=config["require_distinct"],
            matching_picking_ids=matching_picking.ids,
        ))

    def _execute_verifier_supplier_offer(self, company, config):
        products = self._find_products(company, config)
        sellers = self._scoped_search(
            company,
            "product.supplierinfo",
            [
                ("product_tmpl_id", "in", products.mapped("product_tmpl_id").ids),
                ("partner_id.ref", "=", config["vendor_ref"]),
                ("price", "=", config["price"]),
                ("min_qty", "=", config["min_qty"]),
                ("delay", "=", config["delay"]),
            ],
        )
        return ("passed" if sellers else "failed", self._evidence(
            "product.supplierinfo", sellers, product_ids=products.ids
        ))

    def _execute_verifier_linked_purchase_receipt(self, company, config):
        products = self._find_products(company, config)
        lines = self._scoped_search(
            company,
            "purchase.order.line",
            [
                ("product_id", "in", products.ids),
                ("partner_id.ref", "=", config["vendor_ref"]),
                ("product_qty", "=", config["qty"]),
                ("price_unit", "=", config["unit_price"]),
                ("order_id.state", "=", "purchase"),
            ],
        )
        receipts = lines.mapped("move_ids.picking_id").filtered(
            lambda picking: picking.state == config["receipt_state"]
            and picking.picking_type_id.code == "incoming"
        )
        matched = lines.filtered(
            lambda line: bool(line.move_ids.mapped("picking_id") & receipts)
        )
        return ("passed" if matched and receipts else "failed", self._evidence(
            "purchase.order.line",
            matched,
            receipt_ids=receipts.ids,
            product_ids=products.ids,
        ))

    def _execute_verifier_linked_return(self, company, config):
        products = self._find_products(company, config)
        sale_lines = self._scoped_search(
            company,
            "sale.order.line",
            [
                ("product_id", "in", products.ids),
                ("product_uom_qty", "=", config["original_sale_qty"]),
                ("order_id.state", "=", "sale"),
            ],
        )
        original_moves = self._scoped_search(
            company,
            "stock.move",
            [
                ("sale_line_id", "in", sale_lines.ids),
                ("state", "=", "done"),
                ("picking_id.picking_type_id.code", "=", "outgoing"),
            ],
        )
        returns = self._scoped_search(
            company,
            "stock.move",
            [
                ("origin_returned_move_id", "in", original_moves.ids),
                ("state", "=", config["state"]),
                ("location_dest_id.usage", "=", "internal"),
            ],
        )
        actual_quantity = sum(returns.mapped("product_qty"))
        passed = any(
            self._matches_quantity(actual_quantity, config["quantity"], product)
            for product in products
        )
        return ("passed" if passed else "failed", self._evidence(
            "stock.move",
            returns,
            original_move_ids=original_moves.ids,
            returned_quantity=actual_quantity,
        ))

    def _execute_verifier_return_same_serial(self, company, config):
        products = self._find_products(company, config)
        original_moves = self._sale_delivery_moves(company, products)
        returns = self._scoped_search(
            company,
            "stock.move",
            [
                ("origin_returned_move_id", "in", original_moves.ids),
                ("state", "=", "done"),
                ("location_dest_id.usage", "=", "internal"),
            ],
        )
        original_lines = original_moves.mapped("move_line_ids").filtered(
            lambda line: line.state == "done" and line.lot_id
        )
        return_lines = returns.mapped("move_line_ids").filtered(
            lambda line: line.state == "done" and line.lot_id
        )
        matching_lines = return_lines.filtered(
            lambda line: line.lot_id in original_lines.mapped("lot_id")
        )
        quantity = sum(matching_lines.mapped("quantity_product_uom"))
        passed = bool(matching_lines) and any(
            self._matches_quantity(quantity, config["returned_quantity"], product)
            for product in products
        )
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line",
            matching_lines,
            delivered_serials=original_lines.mapped("lot_id.name"),
            returned_serials=matching_lines.mapped("lot_id.name"),
            returned_quantity=quantity,
        ))

    def _execute_verifier_product_removal_configuration(self, company, config):
        products = self._find_products(company, config)
        matching = products.filtered(
            lambda product: product.tracking == config["tracking"]
            and (product.categ_id.removal_strategy_id.method or "").lower()
            == config["removal_strategy"]
            and (
                "expiration_enabled" not in config
                or product.product_tmpl_id.use_expiration_date
                == config["expiration_enabled"]
            )
        )
        return ("passed" if matching else "failed", self._evidence(
            "product.product",
            matching,
            expected_tracking=config["tracking"],
            expected_removal_strategy=config["removal_strategy"],
        ))

    def _lot_receipt_lines(self, company, product, lot_names):
        return self._scoped_search(
            company,
            "stock.move.line",
            [
                ("product_id", "=", product.id),
                ("lot_id.name", "in", lot_names),
                ("state", "=", "done"),
                ("picking_id.picking_type_id.code", "=", "incoming"),
            ],
            order="date, id",
        )

    def _execute_verifier_sequenced_lot_receipts(self, company, config):
        products = self._find_products(company, config)
        lot_names = [lot["name"] for lot in config["lots"]]
        all_lines = self.env["stock.move.line"]
        passed = bool(products)
        evidence_lots = {}
        for product in products:
            lines = self._lot_receipt_lines(company, product, lot_names)
            all_lines |= lines
            previous_date = False
            for expected_lot in config["lots"]:
                lot_lines = lines.filtered(lambda line: line.lot_id.name == expected_lot["name"])
                received = sum(lot_lines.mapped("quantity_product_uom"))
                first_date = min(lot_lines.mapped("date")) if lot_lines else False
                evidence_lots[expected_lot["name"]] = {
                    "received": received,
                    "expected": expected_lot["qty"],
                    "first_receipt": fields.Datetime.to_string(first_date) if first_date else False,
                }
                passed = passed and bool(lot_lines) and self._matches_quantity(
                    received, expected_lot["qty"], product
                )
                if previous_date and first_date:
                    passed = passed and previous_date < first_date
                previous_date = first_date or previous_date
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line", all_lines, lot_receipts=evidence_lots
        ))

    def _sale_delivery_moves(self, company, products):
        return self._scoped_search(
            company,
            "stock.move",
            [
                ("product_id", "in", products.ids),
                ("sale_line_id", "!=", False),
                ("state", "=", "done"),
                ("picking_id.picking_type_id.code", "=", "outgoing"),
            ],
        )

    def _execute_verifier_lot_delivery_split(self, company, config):
        products = self._find_products(company, config)
        lines = self._scoped_search(
            company,
            "stock.move.line",
            self._product_move_line_domain(products)
            + [
                ("state", "=", config["state"]),
                ("picking_id.picking_type_id.code", "=", "outgoing"),
            ],
        )
        if config["require_related_sale"]:
            lines = lines.filtered(lambda line: line.move_id.sale_line_id)
        expected = config["lot_quantities"]
        actual = {}
        total = sum(lines.mapped("quantity_product_uom"))
        passed = False
        for picking in lines.mapped("picking_id"):
            picking_lines = lines.filtered(lambda line: line.picking_id == picking)
            picking_actual = {}
            for line in picking_lines:
                lot_name = line.lot_id.name if line.lot_id else False
                picking_actual[lot_name] = (
                    picking_actual.get(lot_name, 0.0) + line.quantity_product_uom
                )
            quantities_match = set(picking_actual) == set(expected) and all(
                any(
                    self._matches_quantity(picking_actual[name], quantity, product)
                    for product in products
                )
                for name, quantity in expected.items()
            )
            picking_total = sum(picking_actual.values())
            if quantities_match and any(
                self._matches_quantity(picking_total, config["qty_total"], product)
                for product in products
            ):
                actual = picking_actual
                passed = True
                break
        if not actual:
            actual = {}
            for line in lines:
                lot_name = line.lot_id.name if line.lot_id else False
                actual[lot_name] = actual.get(lot_name, 0.0) + line.quantity_product_uom
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line",
            lines,
            actual_lot_quantities=actual,
            expected_lot_quantities=config["lot_quantities"],
            actual_total=total,
        ))

    def _execute_verifier_fefo_receipts_and_dates(self, company, config):
        products = self._find_products(company, config)
        lot_names = [lot["name"] for lot in config["lots"]]
        lots = self._scoped_search(
            company,
            "stock.lot",
            [("product_id", "in", products.ids), ("name", "in", lot_names)],
        )
        lines = self.env["stock.move.line"]
        passed = bool(products) and len(lots) == len(lot_names)
        details = {}
        for product in products:
            product_lines = self._lot_receipt_lines(company, product, lot_names)
            lines |= product_lines
            ordered_dates = []
            for expected_lot in config["lots"]:
                lot = lots.filtered(
                    lambda candidate: candidate.product_id == product
                    and candidate.name == expected_lot["name"]
                )
                lot_lines = product_lines.filtered(
                    lambda line: line.lot_id.name == expected_lot["name"]
                )
                received = sum(lot_lines.mapped("quantity_product_uom"))
                removal_date = lot.removal_date if lot else False
                first_arrival = min(lot_lines.mapped("date")) if lot_lines else False
                details[expected_lot["name"]] = {
                    "received": received,
                    "expected": expected_lot["qty"],
                    "removal_date": fields.Datetime.to_string(removal_date) if removal_date else False,
                    "first_arrival": fields.Datetime.to_string(first_arrival) if first_arrival else False,
                }
                passed = passed and bool(lot and removal_date and first_arrival)
                passed = passed and self._matches_quantity(
                    received, expected_lot["qty"], product
                )
                if config["removal_dates_must_be_future"]:
                    passed = passed and removal_date > fields.Datetime.now()
                ordered_dates.append((removal_date, first_arrival))
            if len(ordered_dates) == 2:
                first_removal, first_arrival = ordered_dates[0]
                second_removal, second_arrival = ordered_dates[1]
                passed = passed and second_removal < first_removal
                passed = passed and first_arrival < second_arrival
        return ("passed" if passed else "failed", self._evidence(
            "stock.move.line",
            lines,
            lot_dates=details,
            date_order=config["date_order"],
            arrival_order=config["arrival_order"],
        ))
