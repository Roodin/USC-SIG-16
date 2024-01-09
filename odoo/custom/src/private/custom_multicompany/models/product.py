# © 2022Comunitea
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    company_id = fields.Many2one(
        default=lambda self: self.env.company.id)
