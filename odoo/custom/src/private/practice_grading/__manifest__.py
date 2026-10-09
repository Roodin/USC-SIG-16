# See LICENSE file for full copyright and licensing details.

{
    "name": "Practice Grading",
    "author": "Comunitea",
    "maintainer": "Comunitea",
    "summary": "Evaluate Odoo practical exercises with imported rubrics",
    "category": "Education",
    "website": "http://www.comunitea.com",
    "version": "19.0.1.0.0",
    "license": "AGPL-3",
    "depends": [
        "account",
        "crm",
        "custom_multicompany",
        "mail",
        "product_expiry",
        "purchase_stock",
        "sale_management",
        "sale_stock",
        "stock",
    ],
    "data": [
        "security/practice_grading_security.xml",
        "security/ir.model.access.csv",
        "views/practice_rubric_views.xml",
        "views/practice_grading_run_views.xml",
        "views/practice_grading_result_views.xml",
        "views/res_company_views.xml",
    ],
    "images": [],
    "installable": True,
}
