INSERT INTO application_paymentcategory (payment_category_id, name, expense, tax_deductible, archived)
SELECT gen_random_uuid(), 'Pagamento Fornitore', true, false, false
WHERE NOT EXISTS (
    SELECT 1 FROM application_paymentcategory WHERE upper(name) = upper('Pagamento Fornitore')
);
