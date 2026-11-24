INSERT INTO application_paymentcategory (payment_category_id, name, expense, tax_deductible, archived)
SELECT gen_random_uuid(), name, expense, tax_deductible, false
FROM (VALUES
    ('entrate e proventi da attività tipiche', false, true),
    ('entrate commerciali', false, false),
    ('contributi', false, false),
    ('liberalità e altro', false, false),
    ('camp e ritiri', false, false),
    ('affitto struttura sportiva', false, false),
    ('Oneri Federali', true, false),
    ('Oneri Finanziari', true, false),
    ('Impianti Sportivi Comunali', true, false),
    ('Impianti Sportivi Privati', true, false),
    ('Prestazioni Sanitarie e Sportive', true, false),
    ('Compensi e Rimborsi Spese', true, false),
    ('Formazione tecnica', true, false),
    ('Oneri generali', true, false),
    ('Oneri erariali', true, false),
    ('Svincolo / Riscatto atleti', true, false)
) AS categories(name, expense, tax_deductible);
