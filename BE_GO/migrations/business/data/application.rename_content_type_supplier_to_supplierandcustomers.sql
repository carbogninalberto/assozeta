UPDATE django_content_type
SET model = 'supplierandcustomers'
WHERE (app_label, model) = ('application', 'supplier')
  AND NOT EXISTS (
      SELECT 1 FROM django_content_type WHERE (app_label, model) = ('application', 'supplierandcustomers')
  );
