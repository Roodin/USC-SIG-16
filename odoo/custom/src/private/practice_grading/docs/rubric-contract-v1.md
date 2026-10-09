# Rubric Import Contract v1

The rubric import wizard accepts UTF-8 encoded JSON documents conforming to
`schemas/rubric-v1.json`, with schema versions 1.0 and 1.1.

The document must contain `schema_version` with the value `1.0`, a rubric name,
and at least one criterion. It may contain a separate human-managed `version`,
which defaults to `1.0`. A criterion has a unique machine identifier, a name, a
positive weight, and a mode.

Version 1.1 uses an object for `evaluation_scope`, a list of strings for
`notes`, and requires `total_weight` to match the sum of criterion weights.
Version 1.0 remains supported with text metadata and an optional `total_weight`.
The imported rubric's total weight is always computed from its criteria.

Version 1.1 supports linked replenishment and purchase receipts, hierarchical
warehouse locations, putaway rules, completed stock moves, serial deliveries
and receipts, supplier offers, purchase returns, FIFO/FEFO configuration, lot
receipt order, delivery lot splits, and FEFO dates. Composite verifiers only
count completed stock moves and require company-scoped data.

Manual criteria do not contain a verifier. They are created as results requiring
instructor review.

Automatic criteria use one of these verifier types:

- `record_exists`: passes when a company-scoped search returns one record.
- `record_count`: passes when a company-scoped search returns at least `minimum`
  records.
- `field_equals`: passes when one company-scoped record returned by `domain` has
  the configured `field` equal to `value`.

Every automatic verifier must provide a model containing a `company_id` field.
The import wizard checks model and field availability in the active Odoo
database. Domains are lists of three-value conditions: `[field, operator, value]`.
Allowed operators are `=`, `!=`, `>`, `>=`, `<`, `<=`, `in`, and `not in`.

The evaluator always appends `company_id = evaluated_company` to the supplied
domain. To exclude company partner records from a `res.partner` criterion, use
`["ref_company_ids", "=", false]`. Imported criteria cannot execute Python, SQL,
formulas, or arbitrary Odoo domains with logical operators.

An import creates a draft rubric. A Practice Grading Manager must approve it
before it can be used in a grading run.