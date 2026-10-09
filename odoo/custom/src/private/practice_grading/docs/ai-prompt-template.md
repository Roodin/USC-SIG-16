# Prompt Template for External AI

Read the supplied Odoo practice and create a grading rubric as JSON.

Return JSON only. It must conform exactly to the attached `rubric-v1.json`
contract and set `schema_version` to `1.0`. Do not include markdown, comments,
SQL, Python, XML, formulas, additional properties, or logical domain operators.
Optional top-level `evaluation_scope` and `notes` fields may contain text. If you
include `total_weight`, it must equal the sum of all criterion weights.

Only use `record_exists`, `record_count`, and `field_equals`. Every automatic
criterion must query an Odoo model with `company_id`. Use manual criteria when a
requirement depends on a narrative answer, a visual decision, activity/comment
quality, or an ambiguous business decision.

Do not claim that a fiscal position is correct solely because it is assigned. If
the practice does not specify a country/VAT scenario and the expected fiscal
position, produce a manual criterion for that requirement.