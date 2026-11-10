package repository

// secCategoryDefaultSQL is PaymentCategory.default_for(association): the lowest-key
// category named 'entrate e proventi da attività tipiche' (case-insensitive) among the
// association's own categories and the global ones. $1 is the association (NULL: global only).
const secCategoryDefaultSQL = `SELECT payment_category_id::text AS payment_category_id, name FROM application_paymentcategory
 WHERE UPPER(name::text)=UPPER('entrate e proventi da attività tipiche')
 AND (sport_association_id IS NULL OR sport_association_id=$1::uuid) ORDER BY payment_category_id LIMIT 1`
