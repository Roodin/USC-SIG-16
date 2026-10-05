# See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    is_student_company = fields.Boolean(string="Student Company")
    student_identifier = fields.Char(string="Student Identifier")